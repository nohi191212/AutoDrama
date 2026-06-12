from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import autodrama.providers.rightcode.text.gpt as rightcode_gpt  # noqa: E402
from autodrama.config import ProviderSettings, RuntimeSettings  # noqa: E402
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider  # noqa: E402


class StreamSmokeOutput(BaseModel):
    ok: bool
    value: str


class FakeStreamResponse:
    status_code = 200

    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    async def __aenter__(self) -> FakeStreamResponse:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aiter_lines(self):
        for line in self.lines:
            if line == "__FAIL_IF_CONSUMED__":
                raise AssertionError("stream reader consumed data after completion marker")
            yield line

    async def aread(self) -> bytes:
        return b""


class FakeAsyncClient:
    requests: list[dict[str, Any]] = []
    lines: list[str] = []

    def __init__(self, *, timeout: int) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> FakeAsyncClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def stream(self, method: str, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> FakeStreamResponse:
        self.requests.append(
            {
                "method": method,
                "url": url,
                "headers": headers,
                "json": json,
                "timeout": self.timeout,
            }
        )
        return FakeStreamResponse(self.lines)

    async def post(self, *args, **kwargs):
        raise AssertionError("streaming provider should not call post() when stream=true")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def run_with_lines(lines: list[str]) -> StreamSmokeOutput:
    FakeAsyncClient.requests = []
    FakeAsyncClient.lines = lines
    original_async_client = rightcode_gpt.httpx.AsyncClient
    rightcode_gpt.httpx.AsyncClient = FakeAsyncClient
    try:
        settings = ProviderSettings(
            base_url="https://www.right.codes/draw",
            api_key_env="RIGHTCODE_API_KEY",
            models={"text": "gpt-5.5"},
            api_keys={"RIGHTCODE_API_KEY": "sk-test"},
        )
        provider = RightCodeTextProvider(settings, RuntimeSettings(request_timeout_seconds=1200))
        result = await provider.generate_json(
            "Return JSON.",
            StreamSmokeOutput,
            temperature=0,
            metadata={"node_name": "rightcode_text_stream_smoke"},
        )
    finally:
        rightcode_gpt.httpx.AsyncClient = original_async_client
    require(FakeAsyncClient.requests, "expected one streaming request")
    payload = FakeAsyncClient.requests[0]["json"]
    require(payload.get("stream") is True, f"expected stream=true, got {payload.get('stream')!r}")
    require(FakeAsyncClient.requests[0]["url"].endswith("/codex/v1/responses"), "unexpected endpoint")
    return result


async def main_async() -> int:
    delta_result = await run_with_lines(
        [
            'event: response.output_text.delta',
            'data: {"type":"response.output_text.delta","delta":"{\\"ok\\": true, "}',
            'data: {"type":"response.output_text.delta","delta":"\\"value\\": \\"stream\\"}"}',
            "data: [DONE]",
            "__FAIL_IF_CONSUMED__",
        ]
    )
    require(delta_result.ok is True, f"unexpected delta ok={delta_result.ok!r}")
    require(delta_result.value == "stream", f"unexpected delta value={delta_result.value!r}")

    done_result = await run_with_lines(
        [
            'data: {"type":"response.output_text.delta","delta":"duplicate-me"}',
            'data: {"type":"response.output_text.done","text":"{\\"ok\\": true, \\"value\\": \\"done\\"}"}',
            "__FAIL_IF_CONSUMED__",
        ]
    )
    require(done_result.ok is True, f"unexpected done ok={done_result.ok!r}")
    require(done_result.value == "done", f"unexpected done value={done_result.value!r}")

    completed_result = await run_with_lines(
        [
            (
                'data: {"type":"response.completed","response":{"output":[{"content":'
                '[{"text":"{\\"ok\\": true, \\"value\\": \\"completed\\"}"}]}]}}'
            ),
            "__FAIL_IF_CONSUMED__",
        ]
    )
    require(completed_result.ok is True, f"unexpected completed ok={completed_result.ok!r}")
    require(completed_result.value == "completed", f"unexpected completed value={completed_result.value!r}")

    print("rightcode_text_stream_smoke=ok")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
