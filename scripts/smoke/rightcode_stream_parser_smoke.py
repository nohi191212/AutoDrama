from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.errors import ProviderBadResponseError
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider


class _AsyncBytes(httpx.AsyncByteStream):
    def __init__(self, body: bytes) -> None:
        self.body = body

    async def __aiter__(self):
        yield self.body


def _sse(*events: dict[str, object] | str) -> bytes:
    lines: list[str] = []
    for event in events:
        data = event if isinstance(event, str) else json.dumps(event)
        lines.extend((f"data: {data}", ""))
    return "\n".join(lines).encode("utf-8")


async def _read(body: bytes) -> dict[str, object] | None:
    response = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        stream=_AsyncBytes(body),
    )
    return await RightCodeTextProvider._read_stream_response(response)


async def main() -> None:
    standard = await _read(
        _sse(
            {"type": "response.output_text.delta", "delta": '{"answer":'},
            {"type": "response.output_text.delta", "delta": '"ok"}'},
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": []},
            },
            "[DONE]",
        )
    )
    assert standard is not None
    assert RightCodeTextProvider._message_content(standard) == '{"answer":"ok"}'

    completed = await _read(
        _sse(
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "message",
                            "content": [{"type": "output_text", "text": '{"answer":"full"}'}],
                        }
                    ],
                },
            }
        )
    )
    assert completed is not None
    assert RightCodeTextProvider._message_content(completed) == '{"answer":"full"}'

    chat = await _read(
        _sse(
            {"choices": [{"delta": {"content": '{"answer":'}}]},
            {"choices": [{"delta": {"content": '"chat"}'}}]},
            "[DONE]",
        )
    )
    assert chat is not None
    assert RightCodeTextProvider._message_content(chat) == '{"answer":"chat"}'

    empty = await _read(_sse({"type": "response.created"}, "[DONE]"))
    assert empty is None

    completed_without_output = await _read(
        _sse(
            {
                "type": "response.completed",
                "response": {"status": "completed", "output": []},
            }
        )
    )
    assert completed_without_output == {"status": "completed", "output": []}
    try:
        RightCodeTextProvider._message_content(completed_without_output)
    except ProviderBadResponseError as exc:
        assert "'choices'" in str(exc)
    else:
        raise AssertionError("completed response without output should not be accepted as text")

    try:
        await _read(
            _sse(
                {
                    "type": "response.failed",
                    "response": {"error": {"code": "upstream_error", "message": "boom"}},
                }
            )
        )
    except ProviderBadResponseError as exc:
        assert "response.failed" in str(exc)
        assert "boom" in str(exc)
    else:
        raise AssertionError("response.failed should raise ProviderBadResponseError")

    print("rightcode stream parser smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())
