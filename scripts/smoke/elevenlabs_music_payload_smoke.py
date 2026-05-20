from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.providers.elevenlabs.music.compose import ElevenLabsMusicProvider  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    os.environ["ELEVENLABS_API_KEY"] = "test-key"
    provider = ElevenLabsMusicProvider(
        ProviderSettings(
            base_url="https://api.elevenlabs.io",
            api_key_env="ELEVENLABS_API_KEY",
            models={"music": "music_v1"},
            options={"output_format": "mp3_44100_128", "force_instrumental": True},
        ),
        RuntimeSettings(request_timeout_seconds=30),
    )

    payload = provider.build_generation_payload(
        "Instrumental cinematic sound bed. [0.0-3.0s] Cold drone [No cut].",
        metadata={"music_length_ms": 3000},
    )
    require(payload["prompt"].startswith("Instrumental cinematic"), "Prompt was not set")
    require(payload["music_length_ms"] == 3000, f"Unexpected duration: {payload}")
    require(payload["model_id"] == "music_v1", f"Unexpected model: {payload}")
    require(payload["force_instrumental"] is True, "force_instrumental was not true")

    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, *, params, headers, json):
            captured["endpoint"] = endpoint
            captured["params"] = params
            captured["headers"] = headers
            captured["json"] = json
            return httpx.Response(
                200,
                content=b"fake-elevenlabs-audio",
                headers={
                    "content-type": "application/octet-stream",
                    "request-id": "request_123",
                    "song_id": "song_123",
                },
            )

    original_client = httpx.AsyncClient
    httpx.AsyncClient = FakeAsyncClient  # type: ignore[assignment]
    try:
        result = await provider.generate_music(
            "Instrumental cinematic sound bed.",
            metadata={"music_length_ms": 3000},
        )
    finally:
        httpx.AsyncClient = original_client  # type: ignore[assignment]

    require(captured["endpoint"].endswith("/v1/music"), f"Unexpected endpoint: {captured}")
    require(captured["params"] == {"output_format": "mp3_44100_128"}, f"Unexpected params: {captured}")
    require(captured["headers"]["xi-api-key"] == "test-key", "API key header missing")
    require(captured["json"]["music_length_ms"] == 3000, "Request duration missing")
    require(result.audio_data, "Binary response was not converted to base64 audio_data")
    require(result.audio_format == "mp3", f"Unexpected format: {result.audio_format}")
    require(result.request_id == "request_123", f"Unexpected request id: {result.request_id}")
    require(result.audio_id == "song_123", f"Unexpected song id: {result.audio_id}")

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "elevenlabs_music_payload"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "payload.txt").write_text(str(captured["json"]), encoding="utf-8")

    print("elevenlabs_music_payload_smoke=ok")
    print(f"endpoint={captured['endpoint']}")
    print(f"output_dir={output_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
