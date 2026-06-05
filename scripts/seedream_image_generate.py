from __future__ import annotations

import argparse
import asyncio
import base64
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.volcengine.image.seedream import VolcengineSeedreamImageProvider  # noqa: E402


SAMPLE_DIR = ROOT_DIR / ".assets" / "sample"
DEFAULT_REFS = [
    SAMPLE_DIR / "R-C.jpg",
    SAMPLE_DIR / "v2-e1cd3a0b37184d062307374c5a602595_r.jpg",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate one image with Volcengine Seedream references.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--prompt-file", default=str(SAMPLE_DIR / "提示词.txt"))
    parser.add_argument("--reference-image", action="append", default=None)
    parser.add_argument("--prompt", default=None)
    parser.add_argument("--model", default="doubao-seedream-5-0-260128")
    parser.add_argument("--size", default="4096x2048")
    parser.add_argument("--output-format", default="png")
    parser.add_argument("--response-format", default="url")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return SAMPLE_DIR / "seedream_outputs" / stamp


def sanitize_response(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            return f"<base64 image data URL omitted; chars={len(value)}>"
        if len(value) > 500 and re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", value):
            return f"<base64 omitted; chars={len(value)}>"
        return value
    if isinstance(value, list):
        return [sanitize_response(item) for item in value]
    if isinstance(value, dict):
        return {str(key): sanitize_response(nested) for key, nested in value.items()}
    return value


def resolve_path(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = (ROOT_DIR / path).resolve()
    return path


def reference_refs(values: list[str]) -> list[AssetRef]:
    refs: list[AssetRef] = []
    for value in values:
        path = resolve_path(value)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Reference image not found: {path}")
        refs.append(AssetRef(id=path.stem, type="image", path=str(path)))
    return refs


def extension_from_data(data: str) -> str:
    match = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,", data)
    if not match:
        return "png"
    extension = match.group(1).lower()
    if extension == "jpeg":
        return "jpg"
    if extension in {"png", "jpg", "webp", "gif"}:
        return extension
    return "bin"


def base64_payload(data: str) -> str:
    if data.startswith("data:") and ";base64," in data:
        return data.split(";base64,", 1)[1]
    return data


def extension_from_download(response: httpx.Response, url: str, fallback: str) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    mapping = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    if content_type in mapping:
        return mapping[content_type]
    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix in {"png", "jpg", "jpeg", "webp", "gif"}:
        return "jpg" if suffix == "jpeg" else suffix
    return fallback


async def save_image(
    provider: VolcengineSeedreamImageProvider,
    *,
    image_data: list[str],
    image_urls: list[str],
    output_dir: Path,
    output_format: str,
) -> Path:
    if image_data:
        extension = extension_from_data(image_data[0])
        output_path = output_dir / f"seedream_generated.{extension}"
        output_path.write_bytes(base64.b64decode(base64_payload(image_data[0])))
        return output_path

    if not image_urls:
        raise RuntimeError("Seedream result has no image data or URL")

    image_url = image_urls[0]
    async with httpx.AsyncClient(timeout=provider.runtime.request_timeout_seconds) as client:
        response = await client.get(image_url)
    if response.status_code >= 400:
        raise RuntimeError(f"Failed to download generated image HTTP {response.status_code}: {response.text[:500]}")

    extension = extension_from_download(response, image_url, output_format.lower().lstrip(".") or "png")
    output_path = output_dir / f"seedream_generated.{extension}"
    output_path.write_bytes(response.content)
    return output_path


async def main_async(args: argparse.Namespace) -> int:
    settings = load_settings(args.config)
    provider = VolcengineSeedreamImageProvider(settings.providers["volcengine"], settings.runtime)
    prompt_path = resolve_path(args.prompt_file)
    if not prompt_path.exists() or not prompt_path.is_file():
        raise FileNotFoundError(f"Prompt file not found: {prompt_path}")

    refs = reference_refs(args.reference_image or [str(path) for path in DEFAULT_REFS])
    prompt = str(args.prompt).strip() if args.prompt else prompt_path.read_text(encoding="utf-8").strip()
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    if not output_dir.is_absolute():
        output_dir = (ROOT_DIR / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    metadata = {
        "model": args.model,
        "size": args.size,
        "output_format": args.output_format,
        "response_format": args.response_format,
    }
    payload = provider.build_payload(prompt, refs=refs, metadata=metadata)
    request_path = output_dir / "request.json"
    request_path.write_text(json.dumps(sanitize_response(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"provider={provider.name}")
    print(f"model={payload['model']}")
    print(f"size={payload['size']}")
    print(f"reference_images={len(refs)}")
    print(f"key_present={bool(provider.api_key)}")
    print(f"output_dir={output_dir}")
    print(f"request_json={request_path}")

    if not provider.api_key:
        print("generation_failed=missing Volcengine API key")
        return 1
    if args.dry_run:
        print("dry_run=true")
        return 0

    try:
        result = await provider.generate_image(prompt, refs=refs, metadata=metadata)
        image_path = await save_image(
            provider,
            image_data=result.image_data,
            image_urls=result.image_urls,
            output_dir=output_dir,
            output_format=args.output_format,
        )
    except Exception as exc:
        print(f"generation_failed={exc}")
        return 1

    response_path = output_dir / "response.json"
    response_path.write_text(
        json.dumps(sanitize_response(result.raw_response), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"request_id={result.request_id or '-'}")
    print(f"image_urls={len(result.image_urls)}")
    print(f"image_data={len(result.image_data)}")
    print(f"response_json={response_path}")
    print(f"saved_image={image_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
