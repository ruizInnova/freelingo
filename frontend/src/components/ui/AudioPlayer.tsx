'use client'

import { useEffect, useRef, useState } from 'react'
import { useTranslations } from 'next-intl'
import { useAuthStore } from '@/store/auth'
import { getLogger } from '@/lib/logger'
import { useSpeechStore } from '@/store/speech'

// CPU-only Kokoro can take more than 15 seconds on the first requests while
// its model and execution paths warm up. Keep the browser request alive long
// enough for those valid responses instead of showing a false playback error.
const TTS_TIMEOUT_MS = 60_000
const ttsLogger = getLogger('tts')

async function findBrowserVoice(language: string): Promise<SpeechSynthesisVoice | null> {
  const normalized = language.toLowerCase()
  const baseLanguage = normalized.split('-')[0]

  // Chrome and mobile browsers often populate the voice list shortly after
  // page load. Wait briefly so we do not accidentally use the device's
  // Spanish default voice for English vocabulary.
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const voices = window.speechSynthesis.getVoices()
    const exact = voices.find((voice) => voice.lang.toLowerCase() === normalized)
    const sameLanguage = voices.find((voice) =>
      voice.lang.toLowerCase().startsWith(`${baseLanguage}-`)
    )
    if (exact || sameLanguage) return exact ?? sameLanguage ?? null
    await new Promise((resolve) => setTimeout(resolve, 100))
  }

  return null
}

interface AudioPlayerProps {
  text: string
  voice?: string
  size?: 'sm' | 'md'
  className?: string
  /** Prefer the device's built-in speech engine and fall back to the API. */
  preferBrowserVoice?: boolean
  language?: string
  /** If set, fetches pre-cached audio via GET from this URL instead of POST /api/tts */
  audioUrl?: string
}

type PlayerState = 'idle' | 'loading' | 'playing' | 'error'

export function AudioPlayer({
  text,
  voice,
  size = 'sm',
  className = '',
  preferBrowserVoice = false,
  language,
  audioUrl,
}: AudioPlayerProps) {
  const [state, setState] = useState<PlayerState>('idle')
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const utteranceRef = useRef<SpeechSynthesisUtterance | null>(null)
  const controllerRef = useRef<AbortController | null>(null)
  const accessToken = useAuthStore((s) => s.accessToken)
  const t = useTranslations('audioPlayer')
  const speechAvailable = useSpeechStore((s) => s.ttsAvailable)
  const checkSpeech = useSpeechStore((s) => s.check)
  const markTtsUnavailable = useSpeechStore((s) => s.markTtsUnavailable)

  // Resolve voice: explicit prop > user localStorage preference > backend default
  const resolvedVoice =
    voice ??
    (typeof window !== 'undefined'
      ? (localStorage.getItem('tts_voice') ?? undefined)
      : undefined)

  useEffect(
    () => () => {
      controllerRef.current?.abort()
      if (utteranceRef.current) window.speechSynthesis?.cancel()
    },
    []
  )

  async function playServerAudio() {
    await checkSpeech()
    if (useSpeechStore.getState().ttsAvailable !== true) {
      setState('idle')
      return
    }

    setState('loading')
    controllerRef.current?.abort()
    const controller = new AbortController()
    controllerRef.current = controller
    const timeoutId = setTimeout(() => controller.abort(), TTS_TIMEOUT_MS)
    try {
      const traceId = `tts-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
      const t0 = performance.now()

      const fetchStart = performance.now()
      const res = await fetch(
        audioUrl ?? '/api/tts',
        audioUrl
          ? {
              headers: {
                ...(accessToken
                  ? { Authorization: `Bearer ${accessToken}` }
                  : {}),
              },
              credentials: 'include' as RequestCredentials,
              signal: controller.signal,
            }
          : {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
                'X-TTS-Trace-ID': traceId,
                ...(accessToken
                  ? { Authorization: `Bearer ${accessToken}` }
                  : {}),
              },
              body: JSON.stringify({ text, voice: resolvedVoice }),
              signal: controller.signal,
            }
      )
      clearTimeout(timeoutId)
      const fetchMs = performance.now() - fetchStart
      if (!res.ok) {
        if (res.status === 503) markTtsUnavailable()
        throw new Error(`TTS error ${res.status}`)
      }

      const blobStart = performance.now()
      const blob = await res.blob()
      const blobMs = performance.now() - blobStart

      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      audioRef.current = audio
      setState('playing')

      const playStart = performance.now()
      await audio.play()
      const playMs = performance.now() - playStart
      const totalMs = performance.now() - t0

      const backendSynthMs = res.headers.get('X-TTS-Backend-Synth-Ms')
      const backendTotalMs = res.headers.get('X-TTS-Backend-Total-Ms')
      const proxyFetchMs = res.headers.get('X-TTS-Proxy-Fetch-Ms')
      const proxyBufferMs = res.headers.get('X-TTS-Proxy-Buffer-Ms')
      const proxyTotalMs = res.headers.get('X-TTS-Proxy-Total-Ms')
      const responseTraceId = res.headers.get('X-TTS-Trace-ID') || traceId

      // TTS latency metrics — only logged in development
      if (process.env.NODE_ENV === 'development') {
        ttsLogger.info('tts-metrics', {
          traceId: responseTraceId,
          textLength: text.length,
          blobBytes: blob.size,
          client: {
            fetchMs: Number(fetchMs.toFixed(1)),
            blobMs: Number(blobMs.toFixed(1)),
            playMs: Number(playMs.toFixed(1)),
            totalMs: Number(totalMs.toFixed(1)),
          },
          proxy: {
            fetchMs: proxyFetchMs ? Number(proxyFetchMs) : null,
            bufferMs: proxyBufferMs ? Number(proxyBufferMs) : null,
            totalMs: proxyTotalMs ? Number(proxyTotalMs) : null,
          },
          backend: {
            synthMs: backendSynthMs ? Number(backendSynthMs) : null,
            totalMs: backendTotalMs ? Number(backendTotalMs) : null,
          },
        })
      }

      audio.onended = () => {
        URL.revokeObjectURL(url)
        audioRef.current = null
        setState('idle')
      }
      audio.onerror = () => {
        URL.revokeObjectURL(url)
        audioRef.current = null
        setState('error')
        setTimeout(() => setState('idle'), 2000)
      }
    } catch {
      clearTimeout(timeoutId)
      setState('error')
      setTimeout(() => setState('idle'), 2000)
    }
  }

  async function handleClick() {
    if (state === 'loading') return

    if (state === 'playing') {
      audioRef.current?.pause()
      audioRef.current = null
      if (utteranceRef.current) {
        window.speechSynthesis.cancel()
        utteranceRef.current = null
      }
      setState('idle')
      return
    }

    if (
      preferBrowserVoice &&
      typeof window !== 'undefined' &&
      'speechSynthesis' in window &&
      'SpeechSynthesisUtterance' in window
    ) {
      setState('loading')
      const utterance = new SpeechSynthesisUtterance(text)
      utterance.lang = language || 'en-US'
      const matchingVoice = await findBrowserVoice(utterance.lang)
      if (!matchingVoice) {
        await playServerAudio()
        return
      }
      utterance.voice = matchingVoice
      utterance.onstart = () => setState('playing')
      utterance.onend = () => {
        utteranceRef.current = null
        setState('idle')
      }
      utterance.onerror = () => {
        utteranceRef.current = null
        void playServerAudio()
      }
      utteranceRef.current = utterance
      window.speechSynthesis.speak(utterance)
      return
    }

    await playServerAudio()
  }

  const sizeClass =
    size === 'sm' ? 'px-2 py-1 text-fl-hint' : 'px-3 py-2 text-xs'

  const label =
    state === 'loading'
      ? '...'
      : state === 'playing'
        ? '■'
        : state === 'error'
          ? '✕'
          : '▶'

  const colorClass =
    state === 'playing'
      ? 'border-fl-border-2 text-fl-fg'
      : state === 'loading'
        ? 'border-fl-border text-fl-muted-3 animate-pulse'
        : state === 'error'
          ? 'border-fl-error/40 text-fl-error-fg'
          : 'border-fl-border text-fl-muted-2 hover:border-fl-border-2 hover:text-fl-fg'

  return (
    <button
      onClick={handleClick}
      disabled={!preferBrowserVoice && speechAvailable === false && state === 'idle'}
      title={state === 'playing' ? t('stop') : t('listen')}
      aria-label={state === 'playing' ? t('ariaStop') : t('ariaListen')}
      className={`border font-mono tracking-widest uppercase transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${colorClass} ${sizeClass} ${className}`}
    >
      {label}
    </button>
  )
}
