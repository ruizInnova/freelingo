import asyncio
from typing import Any

import httpx
import openai

from app.services.stt_service import STTUnavailableError
from app.services.tts_service import TTSUnavailableError

SPEECH_HEALTH_TIMEOUT_SECONDS = 2.0
OPERATIONAL_SPEECH_ERRORS = (
    httpx.HTTPError,
    openai.APIError,
    TimeoutError,
    STTUnavailableError,
    TTSUnavailableError,
)


async def service_is_available(service: Any) -> bool:
    """Return availability without exposing provider failures to API clients."""
    if service is None:
        return False

    try:
        await asyncio.wait_for(service.health(), timeout=SPEECH_HEALTH_TIMEOUT_SECONDS)
    except OPERATIONAL_SPEECH_ERRORS:
        return False
    return True


async def speech_services_status(tts_service: Any, stt_service: Any) -> tuple[bool, bool]:
    """Check TTS and STT concurrently to keep the status endpoint responsive."""
    tts_available, stt_available = await asyncio.gather(
        service_is_available(tts_service),
        service_is_available(stt_service),
    )
    return tts_available, stt_available
