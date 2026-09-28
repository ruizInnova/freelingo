import { create } from 'zustand'
import { apiFetch } from '@/lib/api'

const SPEECH_STATUS_TTL_MS = 15_000

interface SpeechStatusResponse {
  tts: { available: boolean }
  stt: { available: boolean }
  voice_conversation: {
    available: boolean
    reason: string | null
  }
}

interface SpeechStore {
  ttsAvailable: boolean | null
  sttAvailable: boolean | null
  conversationAvailable: boolean | null
  reason: string | null
  checking: boolean
  checkedAt: number | null
  check: (force?: boolean) => Promise<boolean>
  markTtsUnavailable: () => void
  markSttUnavailable: () => void
}

let pendingCheck: Promise<boolean> | null = null

export const useSpeechStore = create<SpeechStore>((set, get) => ({
  ttsAvailable: null,
  sttAvailable: null,
  conversationAvailable: null,
  reason: null,
  checking: false,
  checkedAt: null,
  check: async (force = false) => {
    const { conversationAvailable, checkedAt } = get()
    if (
      !force &&
      conversationAvailable !== null &&
      checkedAt !== null &&
      Date.now() - checkedAt < SPEECH_STATUS_TTL_MS
    ) {
      return conversationAvailable
    }
    if (pendingCheck) return pendingCheck

    set({ checking: true })
    pendingCheck = (async () => {
      try {
        const response = await apiFetch('/api/speech/status')
        if (!response.ok) throw new Error(`Speech status ${response.status}`)
        const data = (await response.json()) as SpeechStatusResponse
        const nextAvailable = data.voice_conversation.available === true
        set({
          ttsAvailable: data.tts.available === true,
          sttAvailable: data.stt.available === true,
          conversationAvailable: nextAvailable,
          reason: data.voice_conversation.reason,
          checkedAt: Date.now(),
        })
        return nextAvailable
      } catch {
        set({
          ttsAvailable: false,
          sttAvailable: false,
          conversationAvailable: false,
          reason: 'speech_status_unavailable',
          checkedAt: Date.now(),
        })
        return false
      } finally {
        pendingCheck = null
        set({ checking: false })
      }
    })()

    return pendingCheck
  },
  markTtsUnavailable: () =>
    set((state) => ({
      ttsAvailable: false,
      conversationAvailable: false,
      reason: 'speech_services_unavailable',
      checkedAt: state.checkedAt ?? Date.now(),
    })),
  markSttUnavailable: () =>
    set((state) => ({
      sttAvailable: false,
      conversationAvailable: false,
      reason: 'speech_services_unavailable',
      checkedAt: state.checkedAt ?? Date.now(),
    })),
}))
