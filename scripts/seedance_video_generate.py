from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.volcengine.video.seedance import VolcengineSeedanceVideoProvider  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate one Seedance video and download it.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--reference-image-url", action="append", default=[])
    parser.add_argument("--duration", type=float, default=10)
    parser.add_argument("--ratio", default="16:9")
    parser.add_argument("--resolution", default="720p")
    parser.add_argument("--model", default="doubao-seedance-2-0-260128")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--filename", default="seedance_generated.mp4")
    parser.add_argument("--generate-audio", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / ".assets" / "sample" / "seedance_outputs" / stamp


async def download(url: str, path: Path, timeout_seconds: int) -> None:
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(url)
    response.raise_for_status()
    path.write_bytes(response.content)


async def main_async(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    provider_settings = settings.providers["volcengine"].model_copy(deep=True)
    provider_settings.options["video_resolution"] = args.resolution
    provider_settings.options["video_ratio"] = args.ratio
    provider_settings.options["generate_audio"] = args.generate_audio
    provider_settings.options["video_max_duration_seconds"] = max(
        int(provider_settings.options.get("video_max_duration_seconds", 15)),
        round(args.duration),
    )
    provider = VolcengineSeedanceVideoProvider(provider_settings, settings.runtime)

    refs = [
        AssetRef(
            id=f"reference_image_{index}",
            type="image",
            url=url,
            metadata={"asset_type": "reference_image"},
        )
        for index, url in enumerate(args.reference_image_url, start=1)
    ]
    metadata = {
        "model": args.model,
        "resolution": args.resolution,
        "video_ratio": args.ratio,
        "generate_audio": args.generate_audio,
        "return_last_frame": True,
        "asset_id": "manual_seedance_video",
    }
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    if not output_dir.is_absolute():
        output_dir = (ROOT_DIR / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    payload = provider.build_payload(args.prompt, refs=refs, duration=args.duration, metadata=metadata)
    request_path = output_dir / "request.json"
    prompt_path = output_dir / "prompt.txt"
    result_path = output_dir / "result.json"
    video_path = output_dir / args.filename
    prompt_path.write_text(args.prompt, encoding="utf-8")
    request_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"provider={provider.name}")
    print(f"model={payload['model']}")
    print(f"duration={payload.get('duration')}")
    print(f"ratio={payload['ratio']}")
    print(f"resolution={payload['resolution']}")
    print(f"reference_images={len(refs)}")
    print(f"generate_audio={payload['generate_audio']}")
    print(f"key_present={bool(provider.api_key)}")
    print(f"output_dir={output_dir}")
    print(f"prompt_path={prompt_path}")
    print(f"request_json={request_path}")

    if args.dry_run:
        print("dry_run=true")
        return 0
    if not provider.api_key:
        print("generation_failed=missing Volcengine API key")
        return 1

    try:
        result = await provider.generate_video(
            args.prompt,
            refs=refs,
            duration=args.duration,
            wait=True,
            metadata=metadata,
        )
        result_path.write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2), encoding="utf-8")
        if not result.video_url:
            raise RuntimeError(f"Seedance result has no video_url: {result.model_dump(mode='json')}")
        await download(result.video_url, video_path, int(settings.runtime.request_timeout_seconds))
    except Exception as exc:
        print(f"generation_failed={exc}")
        return 1

    print(f"task_id={result.task_id or '-'}")
    print(f"task_status={result.task_status or '-'}")
    print(f"request_id={result.request_id or '-'}")
    print(f"result_json={result_path}")
    print(f"saved_video={video_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
