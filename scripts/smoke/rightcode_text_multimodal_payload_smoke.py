from __future__ import annotations

import argparse
import asyncio
import base64
import json
import shutil
import sys
import wave
from pathlib import Path

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.rightcode.text.gpt import RightCodeTextProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


class MultimodalSmokeOutput(BaseModel):
    image_seen: bool = False
    video_seen: bool = False
    audio_seen: bool = False
    summary: str = ""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def write_tiny_png(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="
    )
    path.write_bytes(data)


def write_tiny_wav(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 1600)


def existing_sample_video() -> Path | None:
    candidates = [
        ROOT_DIR / ".tmp" / "seedance_4s_720p_smoke" / "seedance_4s_720p.mp4",
        ROOT_DIR / ".tmp" / "sample_videos" / "roleboard_motion_ref_a.mp4",
        ROOT_DIR / ".tmp" / "sample_videos" / "roleboard_motion_ref_b.mp4",
    ]
    for path in candidates:
        if path.exists() and path.is_file():
            return path
    return None


def prepare_refs(tmp_dir: Path) -> list[AssetRef]:
    image_path = ROOT_DIR / "docs" / "assets" / "autodrama-logo.png"
    audio_path = tmp_dir / "media" / "tiny.wav"
    if not image_path.exists():
        image_path = tmp_dir / "media" / "tiny.png"
        write_tiny_png(image_path)
    write_tiny_wav(audio_path)

    refs = [
        AssetRef(id="smoke_image", type="image", path=str(image_path)),
        AssetRef(id="smoke_audio", type="audio", path=str(audio_path), metadata={"format": "wav"}),
    ]
    video_path = existing_sample_video()
    if video_path is not None:
        refs.insert(1, AssetRef(id="smoke_video", type="video", path=str(video_path)))
    return refs


def select_refs(refs: list[AssetRef], media: str) -> list[AssetRef]:
    if media == "none":
        return []
    if media == "all":
        return refs
    return [ref for ref in refs if ref.type == media]


def content_types(payload: dict) -> list[str]:
    return [
        str(part.get("type"))
        for message in payload.get("input", [])
        for part in message.get("content", [])
        if isinstance(part, dict)
    ]


async def live_call(provider: RightCodeTextProvider, refs: list[AssetRef], *, media: str) -> MultimodalSmokeOutput:
    return await provider.generate_json(
        (
            f"请检查我传入的 {media} 多模态素材。只返回 JSON："
            "image_seen 表示是否收到图片，video_seen 表示是否收到视频，"
            "audio_seen 表示是否收到音频，summary 用一句中文概括，不要输出 Markdown。"
        ),
        MultimodalSmokeOutput,
        refs=refs,
        temperature=0,
        metadata={
            "node_name": "rightcode_text_multimodal_payload_smoke",
            "parameters": {"max_output_tokens": 300, "reasoning": {"effort": "low"}},
        },
    )


async def main_async() -> int:
    parser = argparse.ArgumentParser(description="Build or live-test RightCode Responses multimodal text payload.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--live", action="store_true", help="Actually call RightCode. Default only validates payload.")
    parser.add_argument("--media", choices=["none", "image", "video", "audio", "all"], default="all")
    args = parser.parse_args()

    tmp_dir = ROOT_DIR / ".tmp" / "smoke" / "rightcode_text_multimodal_payload"
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    settings = load_settings(args.config)
    provider = ProviderRouter(settings).text("storyboard")
    require(isinstance(provider, RightCodeTextProvider), f"Expected RightCode text provider, got {type(provider)}")

    refs = select_refs(prepare_refs(tmp_dir), args.media)
    payload = provider.build_payload(
        "Return JSON describing received image, video, and audio refs.",
        MultimodalSmokeOutput,
        refs=refs,
        temperature=0,
        metadata={"node_name": "rightcode_text_multimodal_payload_smoke"},
    )
    types = content_types(payload)
    require(provider.endpoint.endswith("/codex/v1/responses"), f"Unexpected RightCode endpoint: {provider.endpoint}")
    require(types[0] == "input_text", f"First content part should be text: {types}")
    if any(ref.type == "image" for ref in refs):
        require("input_image" in types, f"Payload missing input_image: {types}")
    if any(ref.type == "video" for ref in refs):
        require("input_file" in types, f"Payload missing video input_file: {types}")
    if any(ref.type == "audio" for ref in refs):
        require("input_file" in types, f"Payload missing audio input_file: {types}")

    payload_path = tmp_dir / "rightcode_multimodal_payload.json"
    payload_path.write_text(json.dumps(provider._safe_payload(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    print("rightcode_text_multimodal_payload_smoke=ok")
    print(f"endpoint={provider.endpoint}")
    print(f"media={args.media}")
    print(f"content_types={','.join(types)}")
    print(f"payload_path={payload_path}")

    if args.live:
        result = await live_call(provider, refs, media=args.media)
        result_path = tmp_dir / f"rightcode_multimodal_{args.media}_live_result.json"
        result_path.write_text(
            json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print("rightcode_text_multimodal_live=ok")
        print(f"live_result_path={result_path}")
        print(f"live_result={result.model_dump(mode='json')}")

    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
