import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from app.core.config import settings
from app.main import app
from app.services.speech_availability import service_is_available, speech_services_status


@pytest.fixture
def speech_services():
    missing = object()
    previous_tts = getattr(app.state, "tts_service", missing)
    previous_stt = getattr(app.state, "stt_service", missing)
    tts = SimpleNamespace(health=AsyncMock())
    stt = SimpleNamespace(health=AsyncMock())
    app.state.tts_service = tts
    app.state.stt_service = stt
    yield tts, stt
    if previous_tts is missing:
        del app.state.tts_service
    else:
        app.state.tts_service = previous_tts
    if previous_stt is missing:
        del app.state.stt_service
    else:
        app.state.stt_service = previous_stt


@pytest.mark.asyncio
async def test_service_is_available_handles_missing_and_operational_failure() -> None:
    request = httpx.Request("GET", "http://speech-service/health")
    unavailable = SimpleNamespace(
        health=AsyncMock(side_effect=httpx.ConnectError("provider detail", request=request))
    )

    assert await service_is_available(None) is False
    assert await service_is_available(unavailable) is False


@pytest.mark.asyncio
async def test_service_is_available_does_not_hide_programming_errors() -> None:
    broken = SimpleNamespace(health=AsyncMock(side_effect=TypeError("implementation bug")))

    with pytest.raises(TypeError, match="implementation bug"):
        await service_is_available(broken)


@pytest.mark.asyncio
async def test_speech_services_status_checks_services_concurrently() -> None:
    tts_started = asyncio.Event()
    stt_started = asyncio.Event()
    release = asyncio.Event()

    async def tts_health() -> None:
        tts_started.set()
        await release.wait()

    async def stt_health() -> None:
        stt_started.set()
        await release.wait()

    status_task = asyncio.create_task(
        speech_services_status(
            SimpleNamespace(health=tts_health),
            SimpleNamespace(health=stt_health),
        )
    )
    await asyncio.wait_for(
        asyncio.gather(tts_started.wait(), stt_started.wait()),
        timeout=0.5,
    )
    release.set()

    assert await status_task == (True, True)


@pytest.mark.asyncio
async def test_speech_status_requires_authentication(client) -> None:
    response = await client.get("/api/speech/status")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_speech_status_reports_provider_availability(
    client,
    test_user,
    speech_services,
) -> None:
    _, headers = test_user
    tts, stt = speech_services
    request = httpx.Request("GET", "http://kokoro:8880/v1/models")
    tts.health.side_effect = httpx.ConnectError("private provider detail", request=request)

    response = await client.get("/api/speech/status", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "tts": {"available": False, "provider": settings.TTS_PROVIDER},
        "stt": {"available": True, "provider": settings.STT_PROVIDER},
        "voice_conversation": {
            "available": False,
            "reason": "speech_services_unavailable",
        },
    }
    assert "private provider detail" not in response.text
    tts.health.assert_awaited_once_with()
    stt.health.assert_awaited_once_with()
