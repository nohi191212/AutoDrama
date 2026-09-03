from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.providers.google.text import gemini as gemini_module  # noqa: E402
from autodrama.providers.google.text.gemini import GeminiTextProvider  # noqa: E402


class FakeResponse:
    status_code = 200
    text = "{}"


class FakeAsyncClient:
    calls = 0

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def __aenter__(self) -> "FakeAsyncClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> FakeResponse:
        type(self).calls += 1
        if type(self).calls < 5:
            raise httpx.ConnectError("simulated transient TLS connection failure")
        return FakeResponse()


def main() -> int:
    settings = ProviderSettings(
        base_url="https://api.lk888.ai",
        api_key_env="AIBOX_API_KEY",
        models={"text": "gemini-3.6-flash"},
        api_keys={"AIBOX_API_KEY": "smoke-key"},
    )
    runtime = RuntimeSettings(
        max_text_retry=5,
        text_retry_initial_delay_seconds=0,
        text_retry_max_delay_seconds=0,
    )
    provider = GeminiTextProvider(settings, runtime, provider_name="aibox")
    original_client = gemini_module.httpx.AsyncClient
    gemini_module.httpx.AsyncClient = FakeAsyncClient
    try:
        response = asyncio.run(
            provider._post_with_retries(
                payload={},
                params={},
                headers={},
                metadata={"node_name": "key_vision_image_audit"},
                operation="generate_json",
            )
        )
    finally:
        gemini_module.httpx.AsyncClient = original_client
    assert response.status_code == 200
    assert FakeAsyncClient.calls == 5
    print("gemini text retry smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
