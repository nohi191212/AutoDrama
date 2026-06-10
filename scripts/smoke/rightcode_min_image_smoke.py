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


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.rightcode.image.gpt_image import RightCodeImageProvider  # noqa: E402


DEFAULT_PROMPT = (
    "Clean cinematic CG character portrait of a young swordswoman, neutral gray background, "
    "soft studio lighting, no text, no watermark."
)


def sanitize(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            return f"<base64 image data URL omitted; chars={len(value)}>"
        if len(value) > 500 and not value.startswith(("http://", "https://")):
            return f"<long string omitted; chars={len(value)}>"
        return value
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if key in {"b64_json", "image_base64"} and isinstance(nested, str):
                sanitized[key] = f"<base64 omitted; chars={len(nested)}>"
            else:
                sanitized[key] = sanitize(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize(value), ensure_ascii=False, indent=2), encoding="utf-8")


def load_role_prompt(path: Path, appearance_name: str) -> str:
    payload = json.loads(path.read_text(encoding="utf-8"))
    content = payload.get("content", payload)
    appearances = content.get("appearances") if isinstance(content, dict) else None
    if not isinstance(appearances, list):
        raise ValueError(f"Role JSON has no appearances list: {path}")
    for appearance in appearances:
        if not isinstance(appearance, dict):
            continue
        name = str(appearance.get("name") or "base").strip() or "base"
        if name == appearance_name:
            prompt = str(appearance.get("prompt") or "").strip()
            if prompt:
                return prompt
    raise ValueError(f"Role JSON has no prompt for appearance {appearance_name!r}: {path}")


def extension_from_data_uri(data: str) -> str | None:
    match = re.match(r"^data:image/([a-zA-Z0-9.+-]+);base64,", data)
    if not match:
        return None
    extension = match.group(1).lower()
    if extension == "jpeg":
        return "jpg"
    return extension if extension in {"png", "jpg", "webp", "gif"} else "bin"


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


async def save_first_image(
    *,
    output_dir: Path,
    image_data: list[str],
    image_urls: list[str],
    timeout_seconds: float,
) -> Path:
    if image_data:
        data = image_data[0]
        extension = extension_from_data_uri(data) or "png"
        if data.startswith("data:") and ";base64," in data:
            data = data.split(";base64,", 1)[1]
        output_path = output_dir / f"image.{extension}"
        output_path.write_bytes(base64.b64decode(data))
        return output_path

    if not image_urls:
        raise RuntimeError("RightCode response has no image URL or base64 image data")

    image_url = image_urls[0]
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(image_url)
    response.raise_for_status()
    output_path = output_dir / f"image.{extension_from_response(response, image_url)}"
    output_path.write_bytes(response.content)
    return output_path


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / ".tmp" / "rightcode_min_image_smoke" / stamp


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate one minimal RightCode image and save it under .tmp.")
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--role-json", default=None, help="Optional roleboard JSON; uses content.appearances prompt.")
    parser.add_argument("--appearance", default="base")
    parser.add_argument("--model", default=None, help="Defaults to providers.rightcode.models.roleboard, then image.")
    parser.add_argument("--size", default="1024x1024")
    parser.add_argument("--quality", default="low")
    parser.add_argument("--timeout-seconds", type=float, default=None)
    return parser


async def main_async() -> int:
    args = build_parser().parse_args()
    settings = load_settings(args.config)
    if args.timeout_seconds is not None:
        settings.runtime.request_timeout_seconds = int(args.timeout_seconds)
    provider_settings = settings.providers["rightcode"]
    provider = RightCodeImageProvider(provider_settings, settings.runtime)

    if not provider.api_key:
        print("generation_failed=missing RightCode API key")
        return 1

    prompt = args.prompt
    if args.role_json:
        role_path = Path(args.role_json)
        if not role_path.is_absolute():
            role_path = ROOT_DIR / role_path
        prompt = load_role_prompt(role_path, args.appearance)

    model = args.model or provider_settings.models.get("roleboard") or provider_settings.models.get("image") or provider.model
    metadata = {
        "model": model,
        "quality": args.quality,
        "n": 1,
    }
    payload = provider.build_payload(prompt, size=args.size, metadata=metadata)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    if not output_dir.is_absolute():
        output_dir = ROOT_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "payload.json", payload)

    print(f"endpoint={provider.endpoint}")
    print(f"model={payload.get('model')}")
    print(f"size={payload.get('size')}")
    print(f"quality={payload.get('quality')}")
    print(f"prompt_chars={len(prompt)}")
    print(f"output_dir={output_dir}")

    async with httpx.AsyncClient(timeout=settings.runtime.request_timeout_seconds) as client:
        response = await client.post(
            provider.endpoint,
            headers={
                "Authorization": f"Bearer {provider.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )

    if response.status_code >= 400:
        error_payload = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "text": response.text[:4000],
        }
        write_json(output_dir / "error.json", error_payload)
        print(f"generation_failed=HTTP {response.status_code}")
        print(f"error_json={output_dir / 'error.json'}")
        return 1

    body = response.json()
    write_json(output_dir / "response.json", body)
    image_urls, image_data = provider._extract_images(body)
    image_path = await save_first_image(
        output_dir=output_dir,
        image_data=image_data,
        image_urls=image_urls,
        timeout_seconds=settings.runtime.request_timeout_seconds,
    )
    print("rightcode_min_image_smoke=ok")
    print(f"request_id={provider._request_id(body, response) or '-'}")
    print(f"image_urls={len(image_urls)}")
    print(f"image_data={len(image_data)}")
    print(f"response_json={output_dir / 'response.json'}")
    print(f"saved_image={image_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
