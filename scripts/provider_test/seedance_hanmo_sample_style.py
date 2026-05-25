from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import httpx


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
SCRIPT_DIR = Path(__file__).resolve().parent
for path in (SRC_DIR, SCRIPT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402
from autodrama.providers.volcengine.video.seedance import VolcengineSeedanceVideoProvider  # noqa: E402
from seedance_role_style_probe import (  # noqa: E402
    DEFAULT_ROLE_JSON,
    build_role_profile,
    format_exception,
    load_json,
    save_first_image,
    save_video,
    write_json,
)


DEFAULT_SAMPLE_DIR = ROOT_DIR / ".assets" / "sample"
DEFAULT_OUTPUT_DIR = ROOT_DIR / ".tmp" / "provider_test" / "seedance_hanmo_sample_style"
DEFAULT_NAME = "hanmo_sample_style"


async def generate_hanmo_full_body_image(
    *,
    role_json_path: Path,
    style_reference_paths: Sequence[Path],
    output_dir: Path,
    image_provider: ToAPIImageProvider,
    name: str = DEFAULT_NAME,
    image_size: str = "9:16",
    image_resolution: str = "1K",
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Generate Han Mo's full-body image with two local style references."""

    role_payload = load_json(role_json_path)
    role_name = str(role_payload.get("role_name") or role_payload.get("name") or "韩默")
    role_profile = build_role_profile(role_payload)
    prompt = build_full_body_prompt(role_name=role_name, role_profile=role_profile)

    prompts_dir = output_dir / "prompts"
    images_dir = output_dir / "images"
    results_dir = output_dir / "json"
    for path in (prompts_dir, images_dir, results_dir):
        path.mkdir(parents=True, exist_ok=True)

    prompt_path = prompts_dir / f"{name}_image.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    refs = [
        AssetRef(
            id=f"style_ref_{index}",
            type="image",
            path=str(path),
            metadata={"role": "style_reference"},
        )
        for index, path in enumerate(style_reference_paths, start=1)
    ]

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        result = await image_provider.generate_image(
            prompt,
            refs=refs,
            size=image_size,
            metadata={
                "asset_id": f"seedance_hanmo_sample_style:{name}:full_body",
                "node_name": "provider_test_seedance_hanmo_sample_style",
                "model": "gpt-image-2",
                "size": image_size,
                "resolution": image_resolution,
                "response_format": "url",
            },
        )
        result_json_path = results_dir / f"{name}_image_result.json"
        write_json(result_json_path, result.model_dump(mode="json"))
        image_path, image_url = await save_first_image(client, result, output_stem=images_dir / name)

    return {
        "status": "generated",
        "role_name": role_name,
        "prompt_path": str(prompt_path),
        "style_reference_paths": [str(path) for path in style_reference_paths],
        "image_path": str(image_path),
        "image_url": image_url,
        "result_json_path": str(result_json_path),
        "task_id": result.task_id,
        "task_status": result.task_status,
        "request_id": result.request_id,
    }


async def generate_seedance_animation(
    *,
    image_record: dict[str, Any],
    output_dir: Path,
    video_provider: VolcengineSeedanceVideoProvider,
    name: str = DEFAULT_NAME,
    duration_seconds: int = 8,
    video_resolution: str = "720p",
    video_ratio: str = "9:16",
    timeout_seconds: float = 120.0,
) -> dict[str, Any]:
    """Generate an 8-second 720p Seedance animation from the generated image."""

    prompts_dir = output_dir / "prompts"
    videos_dir = output_dir / "videos"
    results_dir = output_dir / "json"
    for path in (prompts_dir, videos_dir, results_dir):
        path.mkdir(parents=True, exist_ok=True)

    role_name = str(image_record.get("role_name") or "韩默")
    prompt = build_video_prompt(role_name=role_name)
    prompt_path = prompts_dir / f"{name}_video.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    image_path = image_record.get("image_path")
    image_url = image_record.get("image_url")
    refs = [
        AssetRef(
            id=f"{name}_first_frame",
            type="image",
            path=str(image_path) if image_path else None,
            url=str(image_url) if image_url else None,
            metadata={"seedance_role": "first_frame"},
        )
    ]

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        result = await video_provider.generate_video(
            prompt,
            refs=refs,
            duration=duration_seconds,
            wait=True,
            metadata={
                "asset_id": f"seedance_hanmo_sample_style:{name}:animation",
                "resolution": video_resolution,
                "video_ratio": video_ratio,
                "duration": duration_seconds,
                "generate_audio": False,
                "watermark": False,
                "return_last_frame": True,
            },
        )
        result_json_path = results_dir / f"{name}_video_result.json"
        write_json(result_json_path, result.model_dump(mode="json"))
        video_path = await save_video(client, result, output_path=videos_dir / f"{name}.mp4")

    return {
        "status": "generated",
        "prompt_path": str(prompt_path),
        "video_path": str(video_path),
        "video_url": result.video_url,
        "result_json_path": str(result_json_path),
        "task_id": result.task_id,
        "task_status": result.task_status,
        "request_id": result.request_id,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Use .assets/sample images as GPT-Image-2 style references to generate Han Mo's "
            "full-body image, then generate an 8s 720p Seedance 2.0 animation."
        )
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"), help="Path to config.yaml.")
    parser.add_argument("--role-json", default=str(DEFAULT_ROLE_JSON), help="Han Mo role JSON.")
    parser.add_argument("--sample-dir", default=str(DEFAULT_SAMPLE_DIR), help="Directory containing two style reference images.")
    parser.add_argument(
        "--sample-image",
        action="append",
        default=[],
        help="Explicit style reference image path. Can be repeated; defaults to the first two files in --sample-dir.",
    )
    parser.add_argument("--output-dir", default=None, help="Output directory. Defaults to timestamped .tmp provider_test dir.")
    parser.add_argument("--name", default=DEFAULT_NAME, help="Output filename stem for image and video.")
    parser.add_argument("--image-size", default="9:16", help="GPT-Image-2 full-body image aspect ratio.")
    parser.add_argument("--image-resolution", default="1K", choices=["1K", "2K", "4K"], help="GPT-Image-2 resolution.")
    parser.add_argument("--video-ratio", default="9:16", help="Seedance video ratio.")
    parser.add_argument("--video-resolution", default="720p", help="Seedance video resolution.")
    parser.add_argument("--duration", type=int, default=8, help="Seedance duration seconds.")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="Download HTTP timeout.")
    parser.add_argument("--image-only", action="store_true", help="Only generate the Image 2 full-body image.")
    return parser


async def main_async() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args.config)
    role_json_path = resolve_path(args.role_json)
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_paths = resolve_style_reference_paths(args.sample_dir, args.sample_image)

    image_provider_settings = settings.providers["toapi"].model_copy(deep=True)
    image_provider_settings.models["image"] = "gpt-image-2"
    image_provider_settings.options["size"] = args.image_size
    image_provider_settings.options["resolution"] = args.image_resolution
    image_provider_settings.options["response_format"] = "url"
    image_provider = ToAPIImageProvider(image_provider_settings, settings.runtime)

    print(f"style_refs={','.join(str(path) for path in sample_paths)}")
    print(f"image_provider=toapi model=gpt-image-2 size={args.image_size} resolution={args.image_resolution}")
    image_record = await generate_hanmo_full_body_image(
        role_json_path=role_json_path,
        style_reference_paths=sample_paths,
        output_dir=output_dir,
        image_provider=image_provider,
        name=args.name,
        image_size=args.image_size,
        image_resolution=args.image_resolution,
        timeout_seconds=args.timeout_seconds,
    )
    print(f"image_path={image_record.get('image_path')}")
    print(f"image_url={image_record.get('image_url') or '-'}")

    video_record: dict[str, Any] | None = None
    if not args.image_only:
        video_provider_settings = settings.providers["volcengine"].model_copy(deep=True)
        video_provider_settings.options["video_resolution"] = args.video_resolution
        video_provider_settings.options["video_ratio"] = args.video_ratio
        video_provider_settings.options["generate_audio"] = False
        video_provider_settings.options["watermark"] = False
        video_provider_settings.options["video_min_duration_seconds"] = args.duration
        video_provider_settings.options["video_max_duration_seconds"] = max(
            int(video_provider_settings.options.get("video_max_duration_seconds", 15)),
            args.duration,
        )
        video_provider = VolcengineSeedanceVideoProvider(video_provider_settings, settings.runtime)
        print(
            "video_provider=volcengine_seedance "
            f"model={video_provider.model} duration={args.duration}s "
            f"resolution={args.video_resolution} ratio={args.video_ratio}"
        )
        video_record = await generate_seedance_animation(
            image_record=image_record,
            output_dir=output_dir,
            video_provider=video_provider,
            name=args.name,
            duration_seconds=args.duration,
            video_resolution=args.video_resolution,
            video_ratio=args.video_ratio,
            timeout_seconds=args.timeout_seconds,
        )
        print(f"video_path={video_record.get('video_path')}")
        print(f"task_id={video_record.get('task_id') or '-'} status={video_record.get('task_status') or '-'}")

    summary = {
        "role_json": str(role_json_path),
        "sample_images": [str(path) for path in sample_paths],
        "output_dir": str(output_dir),
        "name": args.name,
        "image_size": args.image_size,
        "image_resolution": args.image_resolution,
        "duration": args.duration,
        "video_resolution": args.video_resolution,
        "video_ratio": args.video_ratio,
        "image": image_record,
        "video": video_record,
    }
    write_json(output_dir / "summary.json", summary)
    print(f"summary={output_dir / 'summary.json'}")
    return 0


def build_full_body_prompt(*, role_name: str, role_profile: str) -> str:
    return (
        f"使用参考图片1和参考图片2作为整体美术风格、材质质感、光影和色彩参考，"
        f"不要照搬参考图片中的人物身份、脸、服装或构图。请生成角色「{role_name}」的单人竖版全身图。\n\n"
        f"角色资料：\n{role_profile}\n\n"
        "画面要求：角色从头到脚完整入画，单人自然站姿，脸部清晰但保持动画/CG美术质感，"
        "不要生成摄影级真实人脸。保留韩默的关键身份特征：十九岁青年男性、灰布短褐、旧皮袋、"
        "腰间青灰色石质小药鼎、短匕首、胸前麻绳系三寸赤金色小剑、黑发束起、气质沉默坚韧。"
        "背景干净，方便后续作为 Seedance 2.0 首帧。无其他人物、无文字、无字幕、无水印、无logo。"
    )


def build_video_prompt(*, role_name: str) -> str:
    return (
        f"全程使用图片1作为首帧，并严格保持图片1中的{role_name}人物身份、脸部特征、服装、道具和整体美术风格。"
        "生成8秒竖屏动画，镜头和动作连续自然。"
        "0-2秒：山间药草小径的清晨薄雾中，镜头从全身缓慢推进，人物站定抬眼看向前方；"
        "2-4秒：微风掠过发丝和灰布衣袖，腰间青石药鼎轻微晃动；"
        "4-6秒：人物右手轻扶胸前赤金小剑，小剑泛起很弱的暖色光，眼神坚定；"
        "6-8秒：镜头轻微环绕到正面，人物向前迈出半步，衣摆和旧皮袋自然摆动。"
        "不要变成真人照片，不要换脸，不要换衣服，不要出现文字、字幕、水印或logo。"
    )


def resolve_style_reference_paths(sample_dir: str, explicit_paths: Sequence[str]) -> list[Path]:
    if explicit_paths:
        paths = [resolve_path(path) for path in explicit_paths]
    else:
        directory = resolve_path(sample_dir)
        if not directory.exists() or not directory.is_dir():
            raise FileNotFoundError(f"Sample directory not found: {directory}")
        paths = [
            path
            for path in sorted(directory.iterdir(), key=lambda item: item.name.lower())
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        ][:2]
    if len(paths) != 2:
        raise ValueError(f"Expected exactly two style reference images, got {len(paths)}: {paths}")
    for path in paths:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Style reference image not found: {path}")
    return paths


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (ROOT_DIR / path).resolve()


def resolve_output_dir(value: str | None) -> Path:
    if value:
        return resolve_path(value)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return DEFAULT_OUTPUT_DIR / stamp


def main() -> int:
    try:
        return asyncio.run(main_async())
    except Exception as exc:
        print(f"failed={format_exception(exc)}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
