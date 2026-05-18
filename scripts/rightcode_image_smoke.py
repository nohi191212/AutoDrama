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
from autodrama.providers.rightcode import RightCodeImageProvider  # noqa: E402


DEFAULT_PROMPT = (
    "一张电影感写实风格的测试图：夜晚的现代办公室，桌上有一杯咖啡和打开的笔记本电脑，"
    "窗外城市霓虹灯，构图干净，光影自然，无文字。"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test RightCode GPT Image generation.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--provider", default="rightcode", help="Provider key in config.yaml.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Image generation prompt.")
    parser.add_argument("--model", default=None, help="Override image model.")
    parser.add_argument("--size", default=None, help="Override image size, e.g. 1024x1024.")
    parser.add_argument("--n", type=int, default=None, help="Override image count.")
    parser.add_argument("--api-key", default=None, help="API key. Prefer env/config instead of this argument.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override HTTP timeout.")
    parser.add_argument("--output-dir", default=None, help="Directory for response JSON and generated image.")
    parser.add_argument("--output-format", default=None, help="Optional output_format request parameter, e.g. png/webp.")
    parser.add_argument("--response-format", default=None, help="Optional response_format request parameter.")
    parser.add_argument("--dry-run", action="store_true", help="Print sanitized request details without calling RightCode.")
    return parser


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / "outputs" / "_smoke" / "rightcode_image_smoke" / stamp


def sanitize_response(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if key in {"b64_json", "image_base64"} and isinstance(nested, str):
                sanitized[key] = f"<base64 omitted; chars={len(nested)}>"
            elif key in {"data", "image"} and isinstance(nested, str) and not nested.startswith(("http://", "https://")):
                sanitized[key] = f"<base64 omitted; chars={len(nested)}>"
            else:
                sanitized[key] = sanitize_response(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize_response(item) for item in value]
    return value


def write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize_response(body), ensure_ascii=False, indent=2), encoding="utf-8")


def extension_from_data_uri(data: str) -> str | None:
    match = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,", data)
    if not match:
        return None
    extension = match.group(1).lower()
    if extension == "jpeg":
        return "jpg"
    if extension in {"png", "jpg", "webp", "gif"}:
        return extension
    return "bin"


def extension_from_response(response: httpx.Response, url: str) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    content_type_extensions = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    if content_type in content_type_extensions:
        return content_type_extensions[content_type]

    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix in {"png", "jpg", "jpeg", "webp", "gif"}:
        return "jpg" if suffix == "jpeg" else suffix
    return "png"


def normalize_base64(data: str) -> tuple[str, str]:
    extension = extension_from_data_uri(data) or "png"
    if data.startswith("data:") and ";base64," in data:
        data = data.split(";base64,", 1)[1]
    return data, extension


async def save_first_image(
    provider: RightCodeImageProvider,
    *,
    image_data: list[str],
    image_urls: list[str],
    output_dir: Path,
) -> Path:
    if image_data:
        data, extension = normalize_base64(image_data[0])
        output_path = output_dir / f"image.{extension}"
        output_path.write_bytes(base64.b64decode(data))
        return output_path

    if not image_urls:
        raise RuntimeError("RightCode result has no image data or URL")

    image_url = image_urls[0]
    async with httpx.AsyncClient(timeout=provider.runtime.request_timeout_seconds) as client:
        response = await client.get(image_url)
    if response.status_code >= 400:
        raise RuntimeError(f"Failed to download generated image HTTP {response.status_code}: {response.text[:500]}")

    extension = extension_from_response(response, image_url)
    output_path = output_dir / f"image.{extension}"
    output_path.write_bytes(response.content)
    return output_path


async def main_async(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.config))
    provider_settings = settings.providers.get(args.provider)
    if provider_settings is None:
        print(f"generation_failed=provider '{args.provider}' not found in config")
        return 1

    if args.timeout_seconds is not None:
        settings.runtime.request_timeout_seconds = args.timeout_seconds

    provider = RightCodeImageProvider(provider_settings, settings.runtime)
    if args.api_key:
        provider.api_key = args.api_key
    if args.model:
        provider.model = args.model

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    if not output_dir.is_absolute():
        output_dir = (ROOT_DIR / output_dir).resolve()

    metadata: dict[str, Any] = {}
    if args.model:
        metadata["model"] = args.model
    if args.n is not None:
        metadata["n"] = args.n
    if args.output_format:
        metadata["output_format"] = args.output_format
    if args.response_format:
        metadata["response_format"] = args.response_format

    print(f"provider={provider.name}")
    print(f"endpoint={provider.endpoint}")
    print(f"model={provider.model}")
    print(f"key_present={bool(provider.api_key)}")
    print(f"size={args.size or provider.settings.options.get('size') or provider.settings.options.get('image_size') or '-'}")
    print(f"n={metadata.get('n', provider.settings.options.get('n', '-'))}")
    print(f"output_dir={output_dir}")

    if not provider.api_key:
        print("generation_failed=missing RightCode API key")
        return 1

    if args.dry_run:
        payload = provider._build_chat_payload(args.prompt, size=args.size, metadata=metadata)
        payload = {key: value for key, value in payload.items() if value is not None}
        print("dry_run=true")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = await provider.generate_image(args.prompt, size=args.size, metadata=metadata)
        image_path = await save_first_image(
            provider,
            image_data=result.image_data,
            image_urls=result.image_urls,
            output_dir=output_dir,
        )
    except Exception as exc:
        print(f"generation_failed={exc}")
        return 1

    response_path = output_dir / "response.json"
    write_json(response_path, result.raw_response)

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
