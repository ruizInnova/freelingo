import base64

import httpx
import pytest

from app.services.tts_service import (
    CachedTTSService,
    FallbackTTSService,
    TTSUnavailableError,
    _extract_audio,
)


class _Provider:
    def __init__(self, result: bytes | Exception) -> None:
        self.result = result
        self.calls = 0

    async def health(self) -> None:
        if isinstance(self.result, Exception):
            raise self.result

    async def synthesize(
        self, text: str, voice: str | None = None, language: str | None = None
    ) -> bytes:
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_fallback_uses_next_provider_after_operational_failure() -> None:
    request = httpx.Request("POST", "https://example.test")
    response = httpx.Response(429, request=request)
    primary = _Provider(httpx.HTTPStatusError("quota", request=request, response=response))
    secondary = _Provider(b"mp3")
    service = FallbackTTSService(
        [("cloudflare", primary), ("kokoro", secondary)], cooldown_seconds=900
    )

    assert await service.synthesize("hello", language="en-GB") == b"mp3"
    assert primary.calls == 1
    assert secondary.calls == 1

    assert await service.synthesize("again", language="en-GB") == b"mp3"
    assert primary.calls == 1
    assert secondary.calls == 2


@pytest.mark.asyncio
async def test_fallback_does_not_hide_programming_errors() -> None:
    primary = _Provider(TypeError("bad adapter call"))
    secondary = _Provider(b"mp3")
    service = FallbackTTSService(
        [("cloudflare", primary), ("kokoro", secondary)], cooldown_seconds=900
    )

    with pytest.raises(TypeError, match="bad adapter call"):
        await service.synthesize("hello")
    assert secondary.calls == 0


@pytest.mark.asyncio
async def test_fallback_raises_operational_error_when_all_fail() -> None:
    service = FallbackTTSService(
        [("cloudflare", _Provider(TTSUnavailableError("down")))], cooldown_seconds=900
    )

    with pytest.raises(TTSUnavailableError, match="All TTS providers failed"):
        await service.synthesize("hello")


@pytest.mark.asyncio
async def test_cache_reuses_persisted_mp3(tmp_path) -> None:
    provider = _Provider(b"mp3-audio")
    service = CachedTTSService(provider, str(tmp_path))

    first = await service.synthesize("hello", language="en-GB")
    second = await service.synthesize("hello", language="en-GB")

    assert first == second == b"mp3-audio"
    assert provider.calls == 1
    assert len(list(tmp_path.glob("*.mp3"))) == 1


def test_extract_audio_from_gemini_rest_steps() -> None:
    expected = b"RIFF-test-wave"
    response = httpx.Response(
        200,
        headers={"content-type": "application/json"},
        json={
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {"type": "audio", "data": base64.b64encode(expected).decode()}
                    ],
                }
            ]
        },
    )

    assert _extract_audio(response) == expected


def test_extract_audio_keeps_binary_cloudflare_response() -> None:
    response = httpx.Response(
        200,
        headers={"content-type": "audio/mpeg"},
        content=b"mp3-binary",
    )

    assert _extract_audio(response) == b"mp3-binary"
