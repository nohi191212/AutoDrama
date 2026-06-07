from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import httpx


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderAuthError  # noqa: E402
from autodrama.providers.aliyun.omni.qwen_omni import QwenOmniAudioJudgeProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


DEFAULT_VIDEO_URL = (
    "https://help-static-aliyun-doc.aliyuncs.com/"
    "file-manage-files/zh-CN/20241115/cqqkru/1.mp4"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate or live-test a Qwen3.5-Omni request with multiple video_url inputs."
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--live", action="store_true", help="Actually call DashScope. Default only writes payload.")
    parser.add_argument("--video-url", action="append", dest="video_urls", help="Public video URL. Pass at least two.")
    parser.add_argument("--model", help="Override model. Default uses project Qwen Omni judge provider model.")
    return parser


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def qwen_omni_provider(config_path: str) -> QwenOmniAudioJudgeProvider:
    settings = load_settings(config_path)
    router = ProviderRouter(settings)
    provider_name = "aliyun_omni" if "aliyun_omni" in settings.providers else "aliyun"
    provider_settings = router._aliyun_omni_settings(provider_name)
    return QwenOmniAudioJudgeProvider(provider_settings, settings.runtime)


def build_payload(model: str, video_urls: list[str]) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {"type": "video_url", "video_url": {"url": url}}
        for url in video_urls
    ]
    content.append(
        {
            "type": "text",
            "text": (
                "请判断这个请求是否同时包含两个视频输入。"
                "只用一句中文回答：先说你收到了几个视频输入，再简要描述视频内容。"
            ),
        }
    )
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": True,
        "stream_options": {"include_usage": True},
        "modalities": ["text"],
    }


def extract_stream_text(event: dict[str, Any]) -> str:
    texts: list[str] = []
    choices = event.get("choices")
    if not isinstance(choices, list):
        return ""
    for choice in choices:
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        if isinstance(delta, dict):
            content = delta.get("content")
            if isinstance(content, str):
                texts.append(content)
            elif isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        texts.append(item["text"])
        message = choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            texts.append(message["content"])
    return "".join(texts)


async def live_call(provider: QwenOmniAudioJudgeProvider, payload: dict[str, Any]) -> dict[str, Any]:
    if not provider.api_key:
        raise ProviderAuthError("Missing ALIYUN_API_KEY/DashScope API key for live Qwen Omni smoke")

    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    events: list[dict[str, Any]] = []
    chunks: list[str] = []
    async with httpx.AsyncClient(timeout=provider.runtime.request_timeout_seconds) as client:
        async with client.stream(
            "POST",
            f"{provider.base_url}/chat/completions",
            headers=headers,
            json=payload,
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                return {
                    "ok": False,
                    "status_code": response.status_code,
                    "body": body.decode("utf-8", errors="replace")[:2000],
                }
            async for raw_line in response.aiter_lines():
                line = raw_line.strip()
                if not line:
                    continue
                if line.startswith("data:"):
                    line = line[5:].strip()
                if not line or line == "[DONE]":
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                events.append(event)
                chunks.append(extract_stream_text(event))
    text = "".join(chunks).strip()
    usage = next((event.get("usage") for event in reversed(events) if event.get("usage")), None)
    return {
        "ok": True,
        "status_code": 200,
        "text": text,
        "usage": usage,
        "event_count": len(events),
        "last_event": events[-1] if events else None,
    }


async def main_async(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    provider = qwen_omni_provider(args.config)
    model = args.model or provider.model
    video_urls = args.video_urls or [DEFAULT_VIDEO_URL, DEFAULT_VIDEO_URL]
    require(len(video_urls) >= 2, "Pass at least two --video-url values for a multi-video test")

    payload = build_payload(model, video_urls)
    content = payload["messages"][0]["content"]
    video_parts = [part for part in content if part.get("type") == "video_url"]
    require(len(video_parts) == len(video_urls), f"payload video count mismatch: {content}")
    require(all(part.get("video_url", {}).get("url") for part in video_parts), f"video URL missing: {content}")
    require(content[-1]["type"] == "text", f"text prompt should be last: {content}")
    require(payload["stream"] is True, "Qwen Omni requires stream=True")
    require(payload["modalities"] == ["text"], payload)

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "qwen_omni_multi_video"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = output_dir / "payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("qwen_omni_multi_video_payload=ok")
    print(f"model={model}")
    print(f"video_count={len(video_parts)}")
    print(f"payload_path={payload_path}")

    if args.live:
        result = await live_call(provider, payload)
        result_path = output_dir / "live_result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        require(result.get("ok") is True, f"live Qwen Omni request failed: {result}")
        print("qwen_omni_multi_video_live=ok")
        print(f"live_result_path={result_path}")
        print(f"live_text={str(result.get('text') or '')[:300]}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
