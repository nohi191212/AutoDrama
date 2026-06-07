from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import httpx
from PIL import Image


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.toapi.image import gpt_image  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402


REQUEST = httpx.Request("POST", "https://toapis.com/v1/images/generations")


class FakeAsyncClient:
    queue: list[httpx.Response | Exception] = []
    calls: list[str] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.timeout = kwargs.get("timeout")

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        return None

    async def post(self, url: str, *args: Any, **kwargs: Any) -> httpx.Response:
        type(self).calls.append(f"POST {url}")
        return self._next()

    async def get(self, url: str, *args: Any, **kwargs: Any) -> httpx.Response:
        type(self).calls.append(f"GET {url}")
        return self._next()

    @classmethod
    def _next(cls) -> httpx.Response:
        if not cls.queue:
            raise AssertionError("FakeAsyncClient queue is empty")
        item = cls.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def response(status_code: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status_code, json=body, request=REQUEST)


def provider() -> ToAPIImageProvider:
    settings = ProviderSettings(
        base_url="https://toapis.com",
        api_key_env="sk-smoke-test",
        models={"image": "gpt-image-2"},
        options={
            "toapi_max_attempts": 2,
            "toapi_retry_initial_delay_seconds": 0,
            "poll_interval_seconds": 0,
            "max_wait_seconds": 1,
        },
    )
    return ToAPIImageProvider(settings, RuntimeSettings(request_timeout_seconds=1))


def reference_image_path() -> Path:
    output_dir = ROOT_DIR / ".tmp" / "smoke" / "toapi_image_retry"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "reference.png"
    if not path.exists():
        Image.new("RGB", (16, 16), (128, 64, 32)).save(path, format="PNG")
    return path


async def assert_upload_read_error_retries() -> None:
    FakeAsyncClient.queue = [
        httpx.ReadError("upload response interrupted", request=REQUEST),
        response(200, {"data": {"url": "https://example.invalid/reference.png"}}),
        response(200, {"data": [{"url": "https://example.invalid/generated.png"}]}),
    ]
    FakeAsyncClient.calls = []

    result = await provider().generate_image(
        "test prompt",
        refs=[AssetRef(id="ref", type="image", path=str(reference_image_path()))],
        metadata={"node_name": "prop_generation", "asset_id": "prop_retry_upload"},
    )

    if result.image_urls != ["https://example.invalid/generated.png"]:
        raise AssertionError(f"Unexpected image URLs after upload retry: {result.image_urls}")
    if len(FakeAsyncClient.calls) != 3:
        raise AssertionError(f"Expected 3 HTTP calls after upload retry; got {FakeAsyncClient.calls}")


async def assert_failed_task_retries() -> None:
    FakeAsyncClient.queue = [
        response(200, {"id": "tsk_failed", "status": "processing"}),
        response(
            200,
            {
                "id": "tsk_failed",
                "status": "failed",
                "error": {
                    "code": "generation_failed",
                    "message": "call upstream API failed: decode response failed: unexpected end of JSON input",
                },
            },
        ),
        response(200, {"data": [{"url": "https://example.invalid/generated.png"}]}),
    ]
    FakeAsyncClient.calls = []

    result = await provider().generate_image(
        "test prompt",
        metadata={"node_name": "role_multiview_generation", "asset_id": "role_retry_task"},
    )

    if result.image_urls != ["https://example.invalid/generated.png"]:
        raise AssertionError(f"Unexpected image URLs after task retry: {result.image_urls}")
    if len(FakeAsyncClient.calls) != 3:
        raise AssertionError(f"Expected 3 HTTP calls after task retry; got {FakeAsyncClient.calls}")


async def run_smoke() -> None:
    original_async_client = gpt_image.httpx.AsyncClient
    gpt_image.httpx.AsyncClient = FakeAsyncClient
    try:
        await assert_upload_read_error_retries()
        await assert_failed_task_retries()
    finally:
        gpt_image.httpx.AsyncClient = original_async_client


def main() -> int:
    asyncio.run(run_smoke())
    print("toapi_image_retry_smoke=ok")
    print("retryable_upload_error=ReadError")
    print("retryable_task_error=generation_failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
