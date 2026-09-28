from fastapi import APIRouter, Depends, Request

from app.core.config import settings
from app.core.deps import get_current_user
from app.models.user import User
from app.schemas.tts_stt import (
    SpeechServiceStatus,
    SpeechStatusResponse,
    VoiceConversationStatus,
)
from app.services.speech_availability import speech_services_status

router = APIRouter(prefix="/api/speech", tags=["speech"])


@router.get("/status", response_model=SpeechStatusResponse)
async def get_speech_status(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> SpeechStatusResponse:
    """Report whether configured speech providers are currently reachable."""
    _ = current_user
    tts_available, stt_available = await speech_services_status(
        getattr(request.app.state, "tts_service", None),
        getattr(request.app.state, "stt_service", None),
    )
    voice_available = tts_available and stt_available
    return SpeechStatusResponse(
        tts=SpeechServiceStatus(available=tts_available, provider=settings.TTS_PROVIDER),
        stt=SpeechServiceStatus(available=stt_available, provider=settings.STT_PROVIDER),
        voice_conversation=VoiceConversationStatus(
            available=voice_available,
            reason=None if voice_available else "speech_services_unavailable",
        ),
    )
