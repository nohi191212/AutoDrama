from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

import httpx


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef, ImageGenerationResult, VideoGenerationResult  # noqa: E402
from autodrama.providers.toapi.image.gpt_image import ToAPIImageProvider  # noqa: E402
from autodrama.providers.volcengine.video.seedance import VolcengineSeedanceVideoProvider  # noqa: E402


DEFAULT_ROLE_JSON = ROOT_DIR / "outputs" / "xcj-2" / "assets" / "json" / "roles" / "role_韩默.json"
DEFAULT_OUTPUT_DIR = ROOT_DIR / ".tmp" / "provider_test" / "seedance_role_style_probe"

DEFAULT_STYLE_PROMPTS: dict[str, str] = {
    # "anime2d": "二维国漫动画角色风格，干净线稿，赛璐璐上色，明确非真人摄影，面部为动画化比例。",
    "cg": "国漫CG动画电影角色风格，电影级布光，皮肤为CG材质，明显非真实人脸，无皮肤毛孔细节。",
    "toon3d": "三维卡通动画电影风格，略夸张五官比例，柔和材质，明显非真人脸。",
    # "ink": "东方水墨插画角色风格，宣纸纹理，墨色层次，人物五官概括化，非写实摄影。",
    "game": "游戏角色概念设计风格，半写实但保留插画笔触，非照片，脸部为美术设定图质感。",
    # "photo": "摄影级写真人像风格，虚构人物，非名人，真实自然光，真实皮肤质感。这个风格用于测试 Seedance 输入审核风险。",
}

DEFAULT_VIDEO_PROMPT = (
    "全程使用图片1作为首帧，并严格保持图片1中的人物身份、服装、道具和美术风格。"
    "{role_name}站在山间药草小径旁，腰间药鼎轻微晃动，胸前赤金小剑有微弱光泽。"
    "0-2秒：镜头缓慢推进，人物抬眼看向前方；"
    "2-4秒：一阵微风掠过衣袖和发丝，人物右手轻扶腰间药鼎。"
    "保持同一张脸和同一套服装，不要变成真人照片，不要字幕，不要水印。"
)


@dataclass
class ImageProbeRecord:
    style: str
    status: str
    style_prompt: str
    prompt_path: str | None = None
    image_path: str | None = None
    image_url: str | None = None
    result_json_path: str | None = None
    task_id: str | None = None
    task_status: str | None = None
    request_id: str | None = None
    error: str | None = None


@dataclass
class VideoProbeRecord:
    style: str
    status: str
    video_path: str | None = None
    video_url: str | None = None
    result_json_path: str | None = None
    task_id: str | None = None
    task_status: str | None = None
    request_id: str | None = None
    error: str | None = None


async def generate_style_images(
    style_prompts: Mapping[str, str],
    *,
    role_json_path: Path,
    output_dir: Path,
    image_provider: ToAPIImageProvider,
    image_size: str = "9:16",
    image_resolution: str = "1K",
    timeout_seconds: float = 120.0,
) -> list[ImageProbeRecord]:
    """Generate one GPT-Image-2 role image per style prompt.

    The style key is used as the image filename stem, for example cg.png.
    """

    role_payload = load_json(role_json_path)
    role_name = str(role_payload.get("role_name") or role_payload.get("name") or "角色")
    role_profile = build_role_profile(role_payload)

    images_dir = output_dir / "images"
    prompts_dir = output_dir / "prompts"
    results_dir = output_dir / "json" / "images"
    for path in (images_dir, prompts_dir, results_dir):
        path.mkdir(parents=True, exist_ok=True)

    records: list[ImageProbeRecord] = []
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for raw_style, style_prompt in style_prompts.items():
            style = sanitize_style_key(raw_style)
            prompt = build_image_prompt(
                role_name=role_name,
                role_profile=role_profile,
                style_prompt=style_prompt,
            )
            prompt_path = prompts_dir / f"{style}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")

            record = ImageProbeRecord(
                style=style,
                status="pending",
                style_prompt=style_prompt,
                prompt_path=str(prompt_path),
            )
            try:
                result = await image_provider.generate_image(
                    prompt,
                    size=image_size,
                    metadata={
                        "asset_id": f"seedance_role_style_probe:{style}",
                        "node_name": "provider_test_seedance_role_style_probe",
                        "model": "gpt-image-2",
                        "size": image_size,
                        "resolution": image_resolution,
                        "response_format": "url",
                    },
                )
                result_json_path = results_dir / f"{style}.json"
                write_json(result_json_path, result.model_dump(mode="json"))
                image_path, image_url = await save_first_image(
                    client,
                    result,
                    output_stem=images_dir / style,
                )

                record.status = "generated"
                record.image_path = str(image_path)
                record.image_url = image_url
                record.result_json_path = str(result_json_path)
                record.task_id = result.task_id
                record.task_status = result.task_status
                record.request_id = result.request_id
            except Exception as exc:
                record.status = "failed"
                record.error = format_exception(exc)
            records.append(record)
            print_image_record(record)

    return records


async def generate_seedance_videos(
    image_records: Sequence[ImageProbeRecord],
    *,
    output_dir: Path,
    video_provider: VolcengineSeedanceVideoProvider,
    role_json_path: Path,
    duration_seconds: int = 4,
    video_resolution: str = "480p",
    video_ratio: str = "9:16",
    timeout_seconds: float = 120.0,
) -> list[VideoProbeRecord]:
    """Use generated role images as Seedance first frames and save one video per style."""

    role_payload = load_json(role_json_path)
    role_name = str(role_payload.get("role_name") or role_payload.get("name") or "角色")
    videos_dir = output_dir / "videos"
    prompts_dir = output_dir / "prompts" / "videos"
    results_dir = output_dir / "json" / "videos"
    for path in (videos_dir, prompts_dir, results_dir):
        path.mkdir(parents=True, exist_ok=True)

    records: list[VideoProbeRecord] = []
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for image_record in image_records:
            style = sanitize_style_key(image_record.style)
            record = VideoProbeRecord(style=style, status="pending")
            if image_record.status != "generated":
                record.status = "skipped"
                record.error = f"image status is {image_record.status}"
                records.append(record)
                print_video_record(record)
                continue

            prompt = DEFAULT_VIDEO_PROMPT.format(role_name=role_name)
            prompt_path = prompts_dir / f"{style}.txt"
            prompt_path.write_text(prompt, encoding="utf-8")
            refs = [
                AssetRef(
                    id=f"{style}_first_frame",
                    type="image",
                    path=image_record.image_path,
                    url=image_record.image_url,
                    metadata={"seedance_role": "first_frame"},
                )
            ]

            try:
                result = await video_provider.generate_video(
                    prompt,
                    refs=refs,
                    duration=duration_seconds,
                    wait=True,
                    metadata={
                        "asset_id": f"seedance_role_style_probe:{style}",
                        "resolution": video_resolution,
                        "video_ratio": video_ratio,
                        "duration": duration_seconds,
                        "generate_audio": False,
                        "watermark": False,
                        "return_last_frame": True,
                    },
                )
                result_json_path = results_dir / f"{style}.json"
                write_json(result_json_path, result.model_dump(mode="json"))
                video_path = await save_video(client, result, output_path=videos_dir / f"{style}.mp4")

                record.status = "generated"
                record.video_path = str(video_path)
                record.video_url = result.video_url
                record.result_json_path = str(result_json_path)
                record.task_id = result.task_id
                record.task_status = result.task_status
                record.request_id = result.request_id
            except Exception as exc:
                record.status = "failed"
                record.error = format_exception(exc)
            records.append(record)
            print_video_record(record)

    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Generate GPT-Image-2 role images in multiple styles, then test each image as "
            "a Seedance 2.0 4s 480p first-frame input."
        )
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"), help="Path to config.yaml.")
    parser.add_argument("--role-json", default=str(DEFAULT_ROLE_JSON), help="Role JSON used as character source.")
    parser.add_argument("--output-dir", default=None, help="Output directory. Defaults to timestamped .tmp provider_test dir.")
    parser.add_argument("--image-size", default="9:16", help="GPT-Image-2 image aspect ratio.")
    parser.add_argument("--image-resolution", default="1K", choices=["1K", "2K", "4K"], help="GPT-Image-2 resolution.")
    parser.add_argument("--video-ratio", default="9:16", help="Seedance video ratio.")
    parser.add_argument("--video-resolution", default="480p", help="Seedance video resolution.")
    parser.add_argument("--duration", type=int, default=4, help="Seedance duration seconds.")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="Download HTTP timeout.")
    parser.add_argument(
        "--style",
        action="append",
        default=[],
        metavar="SHORT=PROMPT",
        help="Add or override one style prompt. Can be repeated.",
    )
    parser.add_argument(
        "--styles-json",
        default=None,
        help="Optional JSON object or list of {short,prompt} style prompts.",
    )
    parser.add_argument(
        "--only-style",
        action="append",
        default=[],
        help="Run only selected style key. Can be repeated.",
    )
    parser.add_argument(
        "--phase",
        choices=["all", "images", "videos"],
        default="all",
        help="Run images only, videos only, or both. videos phase reads summary.json from output-dir.",
    )
    return parser


async def main_async() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args.config)
    role_json_path = resolve_path(args.role_json)
    output_dir = resolve_output_dir(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    style_prompts = load_style_prompts(args)
    if args.only_style:
        selected = {sanitize_style_key(value) for value in args.only_style}
        style_prompts = {
            style: prompt
            for style, prompt in style_prompts.items()
            if sanitize_style_key(style) in selected
        }
    if not style_prompts and args.phase != "videos":
        raise ValueError("No style prompts selected")

    image_records: list[ImageProbeRecord] = []
    video_records: list[VideoProbeRecord] = []

    if args.phase in {"all", "images"}:
        image_provider_settings = settings.providers["toapi"].model_copy(deep=True)
        image_provider_settings.models["image"] = "gpt-image-2"
        image_provider_settings.options["size"] = args.image_size
        image_provider_settings.options["resolution"] = args.image_resolution
        image_provider_settings.options["response_format"] = "url"
        image_provider = ToAPIImageProvider(image_provider_settings, settings.runtime)
        print(f"image_provider=toapi model=gpt-image-2 size={args.image_size} resolution={args.image_resolution}")
        image_records = await generate_style_images(
            style_prompts,
            role_json_path=role_json_path,
            output_dir=output_dir,
            image_provider=image_provider,
            image_size=args.image_size,
            image_resolution=args.image_resolution,
            timeout_seconds=args.timeout_seconds,
        )
    else:
        image_records = load_image_records(output_dir / "summary.json")

    if args.phase in {"all", "videos"}:
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
        video_records = await generate_seedance_videos(
            image_records,
            output_dir=output_dir,
            video_provider=video_provider,
            role_json_path=role_json_path,
            duration_seconds=args.duration,
            video_resolution=args.video_resolution,
            video_ratio=args.video_ratio,
            timeout_seconds=args.timeout_seconds,
        )

    summary = {
        "role_json": str(role_json_path),
        "output_dir": str(output_dir),
        "image_size": args.image_size,
        "image_resolution": args.image_resolution,
        "video_ratio": args.video_ratio,
        "video_resolution": args.video_resolution,
        "duration": args.duration,
        "images": [asdict(record) for record in image_records],
        "videos": [asdict(record) for record in video_records],
    }
    write_json(output_dir / "summary.json", summary)
    print(f"summary={output_dir / 'summary.json'}")
    return 0


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_role_profile(role_payload: dict[str, Any]) -> str:
    design = role_payload.get("design") if isinstance(role_payload.get("design"), dict) else {}
    extract = role_payload.get("extract") if isinstance(role_payload.get("extract"), dict) else {}
    appearances = design.get("appearances") if isinstance(design, dict) else None
    base_appearance: dict[str, Any] = {}
    if isinstance(appearances, list) and appearances:
        first = appearances[0]
        if isinstance(first, dict):
            base_appearance = first

    bound_props = base_appearance.get("role_bound_props")
    if not isinstance(bound_props, list):
        bound_props = design.get("props") if isinstance(design, dict) else []
    prop_lines: list[str] = []
    if isinstance(bound_props, list):
        for item in bound_props[:6]:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            desc = str(item.get("desc") or item.get("description") or "").strip()
            if name or desc:
                prop_lines.append(f"- {name}: {desc}" if name and desc else f"- {name or desc}")

    fields = [
        ("角色简介", design.get("intro") or extract.get("brief")),
        ("性格", design.get("personality")),
        ("外观", base_appearance.get("desc") or "; ".join(str(value) for value in extract.get("appearance_notes", []))),
    ]
    chunks = [f"{label}: {value}" for label, value in fields if value]
    if prop_lines:
        chunks.append("绑定道具:\n" + "\n".join(prop_lines))
    return "\n".join(chunks)


def build_image_prompt(*, role_name: str, role_profile: str, style_prompt: str) -> str:
    return (
        f"请生成角色「{role_name}」的单人竖版全身人物图，用于测试图生视频输入审核。\n\n"
        f"风格要求：{style_prompt}\n\n"
        f"角色资料：\n{role_profile}\n\n"
        "画面要求：单人，全身从头到脚完整入画，站姿自然，脸部清晰但不要过度接近真人照片；"
        "服装、发型、腰间药鼎、胸前赤金小剑等身份特征要明确。"
        "干净背景，不出现其他人物，不出现文字、字幕、水印、logo、签名。"
        "输出应是一张可以作为 Seedance 2.0 首帧参考图的人物图片。"
    )


async def save_first_image(
    client: httpx.AsyncClient,
    result: ImageGenerationResult,
    *,
    output_stem: Path,
) -> tuple[Path, str | None]:
    if result.image_urls:
        image_url = result.image_urls[0]
        response = await client.get(image_url)
        response.raise_for_status()
        extension = image_extension(response=response, url=image_url)
        path = output_stem.with_suffix(f".{extension}")
        path.write_bytes(response.content)
        return path, image_url

    if result.image_data:
        data, extension = decode_image_data(result.image_data[0])
        path = output_stem.with_suffix(f".{extension}")
        path.write_bytes(data)
        return path, None

    raise RuntimeError("Image result does not contain image_urls or image_data")


async def save_video(
    client: httpx.AsyncClient,
    result: VideoGenerationResult,
    *,
    output_path: Path,
) -> Path:
    if result.video_data:
        data, _extension = decode_data_url_or_base64(result.video_data, default_extension="mp4")
        output_path.write_bytes(data)
        return output_path

    if not result.video_url:
        raise RuntimeError(f"Seedance result has no video_url: {result.model_dump(mode='json')}")

    response = await client.get(result.video_url)
    response.raise_for_status()
    output_path.write_bytes(response.content)
    return output_path


def decode_image_data(value: str) -> tuple[bytes, str]:
    return decode_data_url_or_base64(value, default_extension="png")


def decode_data_url_or_base64(value: str, *, default_extension: str) -> tuple[bytes, str]:
    if value.startswith("data:") and ";base64," in value:
        header, encoded = value.split(",", 1)
        mime = header.removeprefix("data:").split(";", 1)[0]
        return base64.b64decode(encoded), extension_for_mime(mime, default=default_extension)
    return base64.b64decode(value), default_extension


def image_extension(*, response: httpx.Response, url: str) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    extension = extension_for_mime(content_type, default="")
    if extension:
        return extension
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix == "jpeg":
        return "jpg"
    if suffix in {"png", "jpg", "webp", "gif"}:
        return suffix
    return "png"


def extension_for_mime(mime: str, *, default: str) -> str:
    return {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
        "video/mp4": "mp4",
    }.get(mime, default)


def load_style_prompts(args: argparse.Namespace) -> dict[str, str]:
    prompts = dict(DEFAULT_STYLE_PROMPTS)
    if args.styles_json:
        loaded = load_styles_json(resolve_path(args.styles_json))
        prompts.update(loaded)
    for item in args.style:
        if "=" not in item:
            raise ValueError(f"--style must use SHORT=PROMPT, got: {item}")
        key, prompt = item.split("=", 1)
        prompts[sanitize_style_key(key)] = prompt.strip()
    return {sanitize_style_key(key): value for key, value in prompts.items() if value.strip()}


def load_styles_json(path: Path) -> dict[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, dict):
        return {sanitize_style_key(str(key)): str(prompt) for key, prompt in value.items()}
    if isinstance(value, list):
        result: dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            key = item.get("short") or item.get("style") or item.get("name")
            prompt = item.get("prompt") or item.get("style_prompt")
            if key and prompt:
                result[sanitize_style_key(str(key))] = str(prompt)
        return result
    raise ValueError(f"Unsupported styles JSON shape: {path}")


def load_image_records(summary_path: Path) -> list[ImageProbeRecord]:
    if not summary_path.exists():
        raise FileNotFoundError(f"videos phase requires existing summary: {summary_path}")
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    records = data.get("images")
    if not isinstance(records, list):
        raise ValueError(f"summary does not contain images list: {summary_path}")
    return [ImageProbeRecord(**record) for record in records if isinstance(record, dict)]


def sanitize_style_key(value: str) -> str:
    text = re.sub(r"[<>:\"/\\|?*\x00-\x1F]+", "_", value.strip())
    text = re.sub(r"\s+", "_", text).strip(" ._")
    if not text:
        raise ValueError(f"Invalid style key: {value!r}")
    if text.upper() in {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        "COM1",
        "COM2",
        "COM3",
        "COM4",
        "COM5",
        "COM6",
        "COM7",
        "COM8",
        "COM9",
        "LPT1",
        "LPT2",
        "LPT3",
        "LPT4",
        "LPT5",
        "LPT6",
        "LPT7",
        "LPT8",
        "LPT9",
    }:
        text = f"{text}_style"
    return text


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


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def format_exception(exc: Exception) -> str:
    text = str(exc).strip()
    if text:
        return f"{exc.__class__.__name__}: {text}"
    return exc.__class__.__name__


def print_image_record(record: ImageProbeRecord) -> None:
    print(
        "image "
        f"style={record.style} status={record.status} "
        f"path={record.image_path or '-'} url={record.image_url or '-'} "
        f"error={record.error or '-'}"
    )


def print_video_record(record: VideoProbeRecord) -> None:
    print(
        "video "
        f"style={record.style} status={record.status} "
        f"path={record.video_path or '-'} task_id={record.task_id or '-'} "
        f"error={record.error or '-'}"
    )


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
