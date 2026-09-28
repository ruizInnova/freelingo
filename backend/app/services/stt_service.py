import base64
import io
from typing import Protocol

import httpx
import openai

from app.core.app_logger import get_logger

logger = get_logger(__name__)


class STTService(Protocol):
    async def health(self) -> None: ...

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str: ...


class STTUnavailableError(RuntimeError):
    """Raised after every configured STT provider fails."""


OPERATIONAL_STT_ERRORS = (
    httpx.HTTPError,
    openai.APIError,
    TimeoutError,
    STTUnavailableError,
    OSError,
)


class CloudflareSTTService:
    def __init__(self, account_id: str, api_token: str, model: str, timeout: float) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.model = model
        self.timeout = timeout

    async def health(self) -> None:
        if not self.account_id or not self.api_token or not self.model:
            raise STTUnavailableError("Cloudflare STT is not configured")

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str:
        del filename, mime_type
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/ai/run/{self.model}"
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.api_token}"},
                json={
                    "audio": base64.b64encode(audio_bytes).decode("ascii"),
                    "task": "transcribe",
                    "language": language,
                    "vad_filter": True,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        text = data.get("result", {}).get("text", "").strip()
        if not text:
            raise STTUnavailableError("Cloudflare returned an empty transcription")
        logger.info("[stt-cloudflare] Transcribed model=%s lang=%s", self.model, language)
        return text


class FallbackSTTService:
    model = "chain"

    def __init__(self, providers: list[tuple[str, STTService]]) -> None:
        if not providers:
            raise ValueError("At least one STT provider is required")
        self.providers = providers

    async def health(self) -> None:
        failures: list[str] = []
        for name, provider in self.providers:
            try:
                await provider.health()
                return
            except OPERATIONAL_STT_ERRORS as exc:
                failures.append(f"{name}: {type(exc).__name__}")
        raise STTUnavailableError("No STT provider is healthy: " + ", ".join(failures))

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str:
        failures: list[str] = []
        for name, provider in self.providers:
            try:
                text = await provider.transcribe(
                    audio_bytes,
                    filename,
                    mime_type,
                    language=language,
                )
                logger.info("stt_provider_ok", provider=name)
                return text
            except OPERATIONAL_STT_ERRORS as exc:
                failures.append(f"{name}: {type(exc).__name__}")
                logger.warning("stt_provider_failed", provider=name, error=type(exc).__name__)
        raise STTUnavailableError("All STT providers failed: " + ", ".join(failures))


class WhisperSTTService:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def health(self) -> None:
        """Raise if Whisper ASR is unreachable."""
        async with httpx.AsyncClient(follow_redirects=True) as client:
            r = await client.get(f"{self.base_url}/", timeout=5.0)
            r.raise_for_status()

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str:
        """Send audio to Whisper ASR and return the transcribed text.

        Compatible with onerahmet/openai-whisper-asr-webservice which exposes
        POST /asr?output=json&language=<code> (not the OpenAI /v1/audio/transcriptions path).
        """
        async with httpx.AsyncClient() as client:
            logger.debug(
                "[stt] POST /asr — %d bytes, filename=%s lang=%s",
                len(audio_bytes),
                filename,
                language,
            )
            response = await client.post(
                f"{self.base_url}/asr",
                params={"output": "json", "language": language, "task": "transcribe"},
                files={"audio_file": (filename, audio_bytes, mime_type)},
                timeout=60.0,
            )
            logger.debug("[stt] Response status: %s", response.status_code)
            response.raise_for_status()
            data = response.json()
            # Response: {"text": "...", ...}
            text = data.get("text", "").strip()
            logger.info("[stt] Transcribed: %r", text)
            return text


class OpenAISTTService:
    def __init__(self, api_key: str, model: str) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model

    async def health(self) -> None:
        """Raise if OpenAI STT is unreachable (lightweight models list call)."""
        await self._client.models.list()

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
        *,
        language: str,
    ) -> str:
        """Transcribe audio using OpenAI Whisper API."""
        audio_file = (filename, io.BytesIO(audio_bytes), mime_type)
        response = await self._client.audio.transcriptions.create(
            model=self.model,
            file=audio_file,
            language=language,
            timeout=60.0,
        )
        text = response.text.strip()
        logger.info(
            "[stt-openai] Transcribed model=%s lang=%s: %r",
            self.model,
            language,
            text,
        )
        return text
