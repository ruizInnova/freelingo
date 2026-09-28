from pydantic import BaseModel, Field


class TTSRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5000)
    voice: str | None = None


class STTResponse(BaseModel):
    text: str


class SpeechServiceStatus(BaseModel):
    available: bool
    provider: str


class VoiceConversationStatus(BaseModel):
    available: bool
    reason: str | None = None


class SpeechStatusResponse(BaseModel):
    tts: SpeechServiceStatus
    stt: SpeechServiceStatus
    voice_conversation: VoiceConversationStatus
