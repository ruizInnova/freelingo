import asyncio
import base64
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Protocol

import httpx
import openai

from app.core.app_logger import get_logger

logger = get_logger(__name__)


class TTSService(Protocol):
    async def health(self) -> None: ...

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes: ...


class TTSUnavailableError(RuntimeError):
    """Raised after every configured TTS provider fails."""


OPERATIONAL_TTS_ERRORS = (
    httpx.HTTPError,
    openai.APIError,
    TimeoutError,
    TTSUnavailableError,
    OSError,
    ValueError,
)


class KokoroTTSService:
    def __init__(self, base_url: str, voice: str) -> None:
        self.base_url = base_url
        self.voice = voice

    async def health(self) -> None:
        """Raise if Kokoro is unreachable."""
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{self.base_url}/v1/models", timeout=5.0)
            r.raise_for_status()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call Kokoro-FastAPI and return MP3 audio bytes."""
        _ = language
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}/v1/audio/speech",
                json={
                    "model": "kokoro",
                    "input": text,
                    "voice": voice or self.voice,
                    "response_format": "mp3",
                },
                timeout=55.0,
            )
            response.raise_for_status()
            return response.content


class CloudflareTTSService:
    def __init__(self, account_id: str, api_token: str, model: str, timeout: float) -> None:
        self.account_id = account_id
        self.api_token = api_token
        self.model = model
        self.timeout = timeout

    async def health(self) -> None:
        """Validate that the adapter has the credentials needed for requests."""
        if not self.account_id or not self.api_token:
            raise TTSUnavailableError("Cloudflare TTS credentials are incomplete")

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        _ = voice
        lang = (language or "en").split("-")[0].lower()
        url = (
            "https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account_id}/ai/run/{self.model}"
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self.api_token}",
                    "Accept": "audio/mpeg",
                },
                json={"prompt": text, "lang": lang},
                timeout=self.timeout,
            )
            response.raise_for_status()
            audio = _extract_audio(response)
            if not audio:
                raise TTSUnavailableError("Cloudflare TTS returned empty audio")
            return audio


class GeminiTTSService:
    def __init__(self, api_key: str, model: str, voice: str, timeout: float) -> None:
        self.api_key = api_key
        self.model = model
        self.voice = voice
        self.timeout = timeout

    async def health(self) -> None:
        """Validate that the adapter has a key without consuming speech quota."""
        if not self.api_key:
            raise TTSUnavailableError("Gemini TTS API key is missing")

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        language_hint = language or "en-GB"
        payload = {
            "model": self.model,
            "input": [
                {
                    "type": "user_input",
                    "content": [
                        {
                            "type": "text",
                            "text": text,
                            "annotations": [
                                {
                                    "type": "speech_metadata",
                                    "style": (
                                        "Clear, natural language-learning pronunciation in "
                                        f"{language_hint}"
                                    ),
                                }
                            ],
                        }
                    ],
                }
            ],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": voice or self.voice}]},
        }
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://generativelanguage.googleapis.com/v1beta/interactions",
                headers={"x-goog-api-key": self.api_key},
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            wav_audio = _extract_audio(response)
        if not wav_audio:
            raise TTSUnavailableError("Gemini TTS returned empty audio")
        return await _wav_to_mp3(wav_audio)


class OpenAITTSService:
    def __init__(
        self,
        api_key: str,
        model: str,
        voice: str,
        speed: float = 1.0,
        timeout: float | None = None,
    ) -> None:
        self._client = openai.AsyncOpenAI(api_key=api_key)
        self.model = model
        self.voice = voice
        self.speed = speed
        self.timeout = timeout

    async def health(self) -> None:
        """Raise if OpenAI TTS is unreachable (lightweight models list call)."""
        await self._client.models.list()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        """Call OpenAI TTS API and return MP3 audio bytes."""
        _ = language
        text = text.strip()
        if not text:
            logger.warning("[tts-openai] Empty text received for synthesis")
            return b""

        req_voice = (voice or self.voice).strip()
        input_len = len(text)
        start_t = time.perf_counter()
        logger.info(
            "[tts-openai] request_start model=%s voice=%s chars=%d",
            self.model,
            req_voice,
            input_len,
        )
        request_payload: dict[str, Any] = {
            "model": self.model,
            "voice": req_voice,
            "input": text,
            "response_format": "mp3",
            "speed": self.speed,
        }
        if self.timeout is not None:
            request_payload["timeout"] = self.timeout

        response = await self._client.audio.speech.create(**request_payload)
        audio = response.content
        if not audio:
            raise RuntimeError("OpenAI TTS returned empty audio payload")
        elapsed_ms = (time.perf_counter() - start_t) * 1000
        request_id = getattr(response, "request_id", None)
        if request_id is None:
            headers = getattr(response, "headers", None)
            if headers is not None:
                request_id = headers.get("x-request-id")
        logger.info(
            "[tts-openai] request_ok model=%s voice=%s chars=%d bytes=%d ms=%.1f request_id=%s",
            self.model,
            req_voice,
            input_len,
            len(audio),
            round(elapsed_ms, 1),
            request_id,
        )
        return audio


class FallbackTTSService:
    model = "chain"

    def __init__(self, providers: list[tuple[str, TTSService]], cooldown_seconds: int) -> None:
        if not providers:
            raise ValueError("At least one TTS provider is required")
        self.providers = providers
        self.cooldown_seconds = cooldown_seconds
        self._cooldown_until: dict[str, float] = {}

    async def health(self) -> None:
        failures: list[str] = []
        for name, provider in self.providers:
            if self._is_cooling_down(name):
                continue
            try:
                await provider.health()
                return
            except OPERATIONAL_TTS_ERRORS as exc:
                failures.append(f"{name}: {type(exc).__name__}")
        raise TTSUnavailableError("No TTS provider is healthy: " + ", ".join(failures))

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        failures: list[str] = []
        for name, provider in self.providers:
            if self._is_cooling_down(name):
                continue
            started = time.perf_counter()
            try:
                audio = await provider.synthesize(text, voice, language)
                if not audio:
                    raise TTSUnavailableError(f"{name} returned empty audio")
                logger.info(
                    "tts_provider_ok",
                    provider=name,
                    bytes=len(audio),
                    ms=round((time.perf_counter() - started) * 1000, 1),
                )
                return audio
            except asyncio.CancelledError:
                raise
            except OPERATIONAL_TTS_ERRORS as exc:
                failures.append(f"{name}: {type(exc).__name__}")
                if _should_cool_down(exc):
                    self._cooldown_until[name] = time.monotonic() + self.cooldown_seconds
                logger.warning("tts_provider_failed", provider=name, error=type(exc).__name__)
        raise TTSUnavailableError("All TTS providers failed: " + ", ".join(failures))

    def _is_cooling_down(self, name: str) -> bool:
        return self._cooldown_until.get(name, 0.0) > time.monotonic()


class CachedTTSService:
    model = "chain"

    def __init__(self, service: TTSService, cache_path: str) -> None:
        self.service = service
        self.cache_path = Path(cache_path)

    async def health(self) -> None:
        await self.service.health()

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        normalized = text.strip()
        cache_key = hashlib.sha256(
            json.dumps(
                {"text": normalized, "voice": voice or "", "language": language or ""},
                sort_keys=True,
            ).encode()
        ).hexdigest()
        path = self.cache_path / f"{cache_key}.mp3"
        if path.is_file():
            return await asyncio.to_thread(path.read_bytes)

        audio = await self.service.synthesize(normalized, voice, language)
        await asyncio.to_thread(self._write_atomic, path, audio)
        return audio

    @staticmethod
    def _write_atomic(path: Path, audio: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(f".tmp-{os.getpid()}")
        tmp_path.write_bytes(audio)
        os.replace(tmp_path, path)


def _extract_audio(response: httpx.Response) -> bytes:
    content_type = response.headers.get("content-type", "").lower()
    if not content_type.startswith("application/json"):
        return response.content
    encoded = _find_audio_data(response.json())
    if not encoded:
        return b""
    return base64.b64decode(encoded)


def _find_audio_data(value: Any) -> str | None:
    if isinstance(value, dict):
        output_audio = value.get("output_audio")
        if isinstance(output_audio, dict) and isinstance(output_audio.get("data"), str):
            return output_audio["data"]
        if value.get("type") == "audio" and isinstance(value.get("data"), str):
            return value["data"]
        for child in value.values():
            found = _find_audio_data(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_audio_data(child)
            if found:
                return found
    return None


async def _wav_to_mp3(wav_audio: bytes) -> bytes:
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-f",
        "mp3",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate(wav_audio)
    if process.returncode != 0 or not stdout:
        message = stderr.decode(errors="replace").strip()
        raise TTSUnavailableError(f"Gemini audio conversion failed: {message[:200]}")
    return stdout


def _should_cool_down(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in {401, 402, 403, 429}
    return False
