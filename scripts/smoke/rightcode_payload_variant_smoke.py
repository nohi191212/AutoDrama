from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider


SCHEMA_TEXT = (
    'Required json schema:\n{"properties":{"answer":{"type":"string"}},'
    '"required":["answer"],"type":"object"}'
)


def base_input(text: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
    ]


def payload_for(variant: str, *, stream: bool) -> dict[str, Any]:
    if variant == "plain":
        return {"model": "gpt-5.5", "input": base_input("你好"), "stream": stream}

    prompt = f"问题：1+1等于几？只输出 JSON，answer 字段填数字字符串。\n\n{SCHEMA_TEXT}"
    payload: dict[str, Any] = {
        "model": "gpt-5.5",
        "instructions": (
            "You are a structured JSON generation engine. Return only valid JSON that matches the requested schema. "
            "Do not wrap the answer in Markdown."
        ),
        "input": base_input(prompt),
        "temperature": 0,
        "stream": stream,
        "response_format": {"type": "json_object"},
    }
    if variant == "json_xhigh":
        payload["reasoning"] = {"effort": "xhigh"}
    elif variant == "json_medium":
        payload["reasoning"] = {"effort": "medium"}
    elif variant != "json_no_reasoning":
        raise ValueError(f"Unknown variant: {variant}")
    return payload


def summarize_json_body(body: dict[str, Any]) -> dict[str, Any]:
    output = body.get("output")
    output_types: list[str] = []
    text_values: list[str] = []
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            output_types.append(str(item.get("type")))
            content = item.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text_values.append(part["text"])
    return {
        "status": body.get("status"),
        "model": body.get("model"),
        "output_types": output_types,
        "text": "".join(text_values)[:300],
        "error": body.get("error"),
    }


def summarize_sse_body(text: str) -> dict[str, Any]:
    event_types: list[str] = []
    deltas: list[str] = []
    errors: list[Any] = []
    rate_limits: list[Any] = []
    completed_status = ""
    for block in text.split("\n\n"):
        data_lines = [line[5:].strip() for line in block.splitlines() if line.startswith("data:")]
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        try:
            event = json.loads(data)
        except ValueError:
            continue
        event_type = str(event.get("type") or "")
        if event_type:
            event_types.append(event_type)
        if event_type == "error":
            errors.append(event.get("error") or event)
        if event_type == "codex.rate_limits":
            rate_limits.append(event.get("rate_limits") or event)
        delta = event.get("delta")
        if isinstance(delta, str):
            deltas.append(delta)
        response = event.get("response")
        if event_type == "response.completed" and isinstance(response, dict):
            completed_status = str(response.get("status") or "")
    return {
        "event_types": event_types[:30],
        "event_count": len(event_types),
        "has_output_text_delta": bool(deltas),
        "text": "".join(deltas)[:300],
        "completed_status": completed_status,
        "errors": errors[:3],
        "rate_limits": rate_limits[:3],
    }


async def run_variant(config: Path, *, variant: str, stream: bool) -> None:
    settings = load_settings(config)
    provider_settings = settings.providers["rightcode"]
    provider = RightCodeTextProvider(provider_settings, settings.runtime, model_key="script")
    api_key = provider_settings.secret("api_key_env")
    if not api_key:
        raise RuntimeError("RightCode API key is unavailable")

    payload = payload_for(variant, stream=stream)
    async with httpx.AsyncClient(timeout=settings.runtime.request_timeout_seconds) as client:
        response = await client.post(
            provider.endpoint,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
        )

    print(f"variant={variant} stream={stream} endpoint={provider.endpoint}")
    print(f"status_code={response.status_code} content_type={response.headers.get('content-type')}")
    text = response.text
    if "text/event-stream" in str(response.headers.get("content-type") or ""):
        print(json.dumps(summarize_sse_body(text), ensure_ascii=False, indent=2))
        return
    try:
        body = response.json()
    except ValueError:
        print(f"body_prefix={text[:500]!r}")
        return
    print(json.dumps(summarize_json_body(body), ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="huyao.yaml")
    parser.add_argument(
        "--variant",
        choices=["plain", "json_no_reasoning", "json_medium", "json_xhigh"],
        default="json_xhigh",
    )
    parser.add_argument("--stream", action="store_true")
    args = parser.parse_args()
    asyncio.run(run_variant(Path(args.config), variant=args.variant, stream=args.stream))


if __name__ == "__main__":
    main()
