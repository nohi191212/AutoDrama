from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.providers.rightcode.image import gpt_image  # noqa: E402
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider  # noqa: E402


REQUEST = httpx.Request("POST", "https://www.right.codes/draw/v1/images/generations")


class FakeAsyncClient:
    queue: list[httpx.Response | Exception] = []
    calls: int = 0

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, *args: Any, **kwargs: Any) -> httpx.Response:
        type(self).calls += 1
        if not type(self).queue:
            raise AssertionError("FakeAsyncClient queue is empty")
        item = type(self).queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(status_code: int, *, text: str | None = None, body: dict[str, Any] | None = None) -> httpx.Response:
    if body is not None:
        return httpx.Response(status_code, json=body, request=REQUEST)
    return httpx.Response(status_code, text=text or "", request=REQUEST)


def provider() -> RightCodeImageProvider:
    settings = ProviderSettings(
        base_url="https://www.right.codes/draw",
        api_key_env="sk-smoke-test",
        models={"image": "gpt-image-2"},
        options={
            "rightcode_max_attempts": 2,
            "rightcode_retry_initial_delay_seconds": 0,
        },
    )
    return RightCodeImageProvider(settings, RuntimeSettings(max_media_retry=2, request_timeout_seconds=1))


async def assert_524_retries() -> None:
    FakeAsyncClient.queue = [
        response(524, text="a timeout occurred"),
        response(200, body={"data": [{"b64_json": "ZmFrZV9pbWFnZQ=="}]}),
    ]
    FakeAsyncClient.calls = 0

    result = await provider().generate_image(
        "test prompt",
        metadata={"asset_id": "prop_test"},
    )

    if FakeAsyncClient.calls != 2:
        raise AssertionError(f"Expected HTTP 524 to retry once; got {FakeAsyncClient.calls} call(s)")
    if result.image_data != ["ZmFrZV9pbWFnZQ=="]:
        raise AssertionError(f"Unexpected image data after retry: {result.image_data}")


async def assert_connect_error_retries() -> None:
    FakeAsyncClient.queue = [
        httpx.ConnectError("connect failed", request=REQUEST),
        response(200, body={"data": [{"url": "https://example.invalid/generated.png"}]}),
    ]
    FakeAsyncClient.calls = 0

    result = await provider().generate_image(
        "test prompt",
        metadata={"asset_id": "prop_test"},
    )

    if FakeAsyncClient.calls != 2:
        raise AssertionError(f"Expected ConnectError to retry once; got {FakeAsyncClient.calls} call(s)")
    if result.image_urls != ["https://example.invalid/generated.png"]:
        raise AssertionError(f"Unexpected image URLs after retry: {result.image_urls}")


async def assert_400_does_not_retry() -> None:
    FakeAsyncClient.queue = [
        response(400, text="bad request"),
        response(200, body={"data": [{"b64_json": "should_not_be_used"}]}),
    ]
    FakeAsyncClient.calls = 0

    try:
        await provider().generate_image("test prompt", metadata={"asset_id": "prop_test"})
    except ProviderBadResponseError as exc:
        if "HTTP 400" not in str(exc):
            raise AssertionError(f"Unexpected 400 error message: {exc}") from exc
    else:
        raise AssertionError("Expected HTTP 400 to fail without retry")

    if FakeAsyncClient.calls != 1:
        raise AssertionError(f"Expected HTTP 400 to avoid retry; got {FakeAsyncClient.calls} call(s)")


async def assert_upstream_400_retries() -> None:
    FakeAsyncClient.queue = [
        response(
            400,
            body={
                "error": {
                    "message": "excessive system load",
                    "type": "upstream_error",
                    "param": "",
                    "code": None,
                }
            },
        ),
        response(200, body={"data": [{"b64_json": "ZmFrZV9pbWFnZQ=="}]}),
    ]
    FakeAsyncClient.calls = 0

    result = await provider().generate_image(
        "test prompt",
        metadata={"asset_id": "prop_test"},
    )

    if FakeAsyncClient.calls != 2:
        raise AssertionError(f"Expected upstream_error HTTP 400 to retry once; got {FakeAsyncClient.calls} call(s)")
    if result.image_data != ["ZmFrZV9pbWFnZQ=="]:
        raise AssertionError(f"Unexpected image data after upstream 400 retry: {result.image_data}")


async def run_smoke() -> None:
    original_async_client = gpt_image.httpx.AsyncClient
    gpt_image.httpx.AsyncClient = FakeAsyncClient
    try:
        await assert_524_retries()
        await assert_connect_error_retries()
        await assert_400_does_not_retry()
        await assert_upstream_400_retries()
    finally:
        gpt_image.httpx.AsyncClient = original_async_client


def main() -> int:
    asyncio.run(run_smoke())
    print("rightcode_image_retry_smoke=ok")
    print("retryable_http_status=524")
    print("retryable_transport_error=ConnectError")
    print("non_retryable_http_status=400")
    print("retryable_upstream_error_status=400")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
