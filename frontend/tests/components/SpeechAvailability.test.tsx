import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

const { mockApiFetch } = vi.hoisted(() => ({
  mockApiFetch: vi.fn(),
}))

vi.mock('@/lib/api', () => ({ apiFetch: mockApiFetch }))
vi.mock('next-intl', () => ({
  useTranslations: () => (key: string) => key,
}))

import { AudioPlayer } from '@/components/ui/AudioPlayer'
import { VoiceRecorder } from '@/components/ui/VoiceRecorder'
import { useSpeechStore } from '@/store/speech'

beforeEach(() => {
  vi.clearAllMocks()
  useSpeechStore.setState({
    ttsAvailable: null,
    sttAvailable: null,
    conversationAvailable: null,
    reason: null,
    checking: false,
    checkedAt: null,
  })
  mockApiFetch.mockResolvedValue({
    ok: true,
    json: async () => ({
      tts: { available: false },
      stt: { available: false },
      voice_conversation: {
        available: false,
        reason: 'speech_services_unavailable',
      },
    }),
  })
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('speech availability guards', () => {
  it('does not request microphone access when speech is unavailable', async () => {
    const getUserMedia = vi.fn()
    vi.stubGlobal('navigator', { mediaDevices: { getUserMedia } })

    render(
      <VoiceRecorder studyPlanId={1} onTranscription={vi.fn()} />
    )
    fireEvent.click(screen.getByRole('button', { name: 'ariaRecord' }))

    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledWith('/api/speech/status'))
    expect(getUserMedia).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'ariaRecord' })).toBeDisabled()
  })

  it('does not request TTS audio when speech is unavailable', async () => {
    const fetchSpy = vi.fn()
    vi.stubGlobal('fetch', fetchSpy)

    render(<AudioPlayer text="Hello" />)
    fireEvent.click(screen.getByRole('button', { name: 'ariaListen' }))

    await waitFor(() => expect(mockApiFetch).toHaveBeenCalledWith('/api/speech/status'))
    expect(fetchSpy).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'ariaListen' })).toBeDisabled()
  })
})
