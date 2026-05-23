from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402


DEFAULT_BASE_URL = "https://toapis.com"
DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "3:2"
DEFAULT_RESOLUTION = "1K"
DEFAULT_PROMPT = (
    "爱死机写实CG风格，高保真三维角色设定图。左侧为角色三视图：正面、左侧面、背面并排，同一身高比例，统一服装细节。右侧为角色主要物品设计图，展示残雪剑和青霜剑，两剑均与左侧人物保持同一比例尺和地面基线。角色为约17-18岁年轻女性，身量纤细，身高约168cm，头身比7.5头身。面容清丽，肤色白皙，眉宇冰冷，眼神清冽锐利。黑色长发简单束起，额前碎发。身穿白色剑袍，袖口紧束，腰系深蓝丝带，衣摆有轻微磨损和泥灰污渍，左肩缠有绷带。气质冷峻，站姿挺拔，双手自然垂放。右侧物品：左侧一柄残破飞剑（残雪剑），剑身布满细密裂纹，缺一小块刃口，银白色灵光流转；右侧一柄通体青碧的长剑（青霜剑），剑身有冰霜纹路，剑格两侧嵌有青色鳞片。两剑底部与左侧人物脚边地面线对齐，剑长约为左侧人物身高的三分之二。电影级侧逆光布光，柔和阴影，干净中性灰背景，无其他人物、字幕或水印。"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Test ToAPI gpt-image-2 image generation. Defaults to size=3:2 and resolution=1K, "
            "which ToAPI documents as 1536x1024."
        )
    )
    parser.add_argument("--config", default=str(ROOT_DIR / "config.yaml"), help="Path to config.yaml.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="ToAPI base URL.")
    parser.add_argument("--api-key", default=None, help="API key override. Prefer apikeys.yaml or env vars.")
    parser.add_argument(
        "--api-key-name",
        default="TOAPI_API_KEY",
        help="Key name to load from apikeys.yaml/settings or environment.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Image model.")
    parser.add_argument(
        "--size",
        default=DEFAULT_SIZE,
        help="ToAPI aspect ratio. Default 3:2 maps to 1536x1024 at resolution=1K.",
    )
    parser.add_argument(
        "--resolution",
        default=DEFAULT_RESOLUTION,
        choices=["1K", "2K", "4K"],
        help="ToAPI resolution tier. Use 1K with size=3:2 for 1536x1024.",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Generation prompt.")
    parser.add_argument("--n", type=int, default=1, help="Number of images to request.")
    parser.add_argument("--response-format", default="url", help="ToAPI response_format.")
    parser.add_argument("--timeout-seconds", type=float, default=120.0, help="HTTP request timeout.")
    parser.add_argument("--poll-interval-seconds", type=float, default=3.0, help="Seconds between task polls.")
    parser.add_argument("--max-wait-seconds", type=float, default=900.0, help="Maximum task wait time.")
    parser.add_argument("--output-dir", default=None, help="Output directory for JSON and image files.")
    parser.add_argument(
        "--reference-image-url",
        action="append",
        default=[],
        help="Optional public reference image URL. Can be repeated.",
    )
    parser.add_argument(
        "--reference-image",
        action="append",
        default=[],
        help="Optional local reference image. It will be uploaded to /v1/uploads/images first. Can be repeated.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Write request payload without calling ToAPI.")
    return parser


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / ".tmp" / "provider_test" / "toapi_image2" / stamp


def load_api_key(config_path: Path, key_name: str) -> str | None:
    env_value = os.getenv(key_name)
    if env_value:
        return env_value

    for alias in api_key_aliases(key_name):
        value = os.getenv(alias)
        if value:
            return value

    try:
        settings = load_settings(config_path)
    except Exception:
        settings = None

    if settings is not None:
        for alias in api_key_aliases(key_name):
            value = settings.api_keys.get(alias)
            if value:
                return value

    apikeys_path = ROOT_DIR / "apikeys.yaml"
    if settings is not None and settings.apikeys_file:
        apikeys_path = settings.apikeys_file
    if apikeys_path.exists():
        return load_api_key_from_yaml(apikeys_path, key_name)
    return None


def api_key_aliases(key_name: str) -> list[str]:
    aliases = [
        key_name,
        key_name.upper(),
        key_name.lower(),
        "TOAPI_API_KEY",
        "TOAPIS_API_KEY",
        "toapi.api_key",
        "toapis.api_key",
        "toapi_api_key",
        "toapis_api_key",
    ]
    unique: list[str] = []
    for alias in aliases:
        if alias not in unique:
            unique.append(alias)
    return unique


def load_api_key_from_yaml(path: Path, key_name: str) -> str | None:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return None

    flattened = flatten_mapping(data)
    for alias in api_key_aliases(key_name):
        for candidate in (alias, alias.upper(), alias.lower()):
            value = flattened.get(candidate)
            if value:
                return str(value)
    return None


def flatten_mapping(value: Any, *, prefix: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    flattened: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(raw_value, dict):
            flattened.update(flatten_mapping(raw_value, prefix=full_key))
            continue
        if raw_value is None:
            continue
        text_value = str(raw_value)
        flattened[full_key] = text_value
        flattened[full_key.upper()] = text_value
        flattened[full_key.lower()] = text_value
        flattened[full_key.replace(".", "_").upper()] = text_value
        flattened[full_key.replace(".", "_").lower()] = text_value
    return flattened


def normalize_base_url(value: str) -> str:
    base_url = value.strip().rstrip("/")
    for suffix in ("/v1/images/generations", "/images/generations", "/v1"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)].rstrip("/")
            break
    return base_url


def image_generation_url(base_url: str) -> str:
    return f"{normalize_base_url(base_url)}/v1/images/generations"


def upload_image_url(base_url: str) -> str:
    return f"{normalize_base_url(base_url)}/v1/uploads/images"


def task_url(base_url: str, task_id: str) -> str:
    return f"{image_generation_url(base_url)}/{task_id}"


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def sanitize_response(value: Any) -> Any:
    if isinstance(value, str):
        if value.startswith("data:image/") and ";base64," in value:
            return f"<base64 image data URL omitted; chars={len(value)}>"
        if is_probable_base64_image(value):
            return f"<base64 image omitted; chars={len(value)}>"
        return value
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            lowered = str(key).lower()
            if lowered in {"b64_json", "image_base64"} and isinstance(nested, str):
                sanitized[key] = f"<base64 omitted; chars={len(nested)}>"
            else:
                sanitized[key] = sanitize_response(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize_response(item) for item in value]
    return value


def is_probable_base64_image(value: str) -> bool:
    if len(value) < 200:
        return False
    return bool(re.fullmatch(r"[A-Za-z0-9+/=\r\n]+", value))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json_dumps(sanitize_response(value)), encoding="utf-8")


def response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return {"text": response.text}


def error_message(response: httpx.Response, body: Any) -> str:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error)
        if error:
            return str(error)
        if body.get("message"):
            return str(body["message"])
    return response.text[:500]


def require_success(response: httpx.Response, body: Any, *, label: str) -> None:
    if response.status_code < 400:
        if isinstance(body, dict) and body.get("success") is False:
            raise RuntimeError(f"{label} failed: {body.get('message') or body}")
        return
    raise RuntimeError(f"{label} failed with HTTP {response.status_code}: {error_message(response, body)}")


async def upload_reference_image(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    path: Path,
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Reference image not found: {path}")

    with path.open("rb") as file:
        response = await client.post(
            upload_image_url(base_url),
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": (path.name, file, mime_type_for_path(path))},
            data={"purpose": "generation"},
        )
    body = response_body(response)
    require_success(response, body, label=f"upload {path.name}")

    if not isinstance(body, dict):
        raise RuntimeError(f"Upload response is not an object: {body}")
    data = body.get("data")
    if not isinstance(data, dict) or not data.get("url"):
        raise RuntimeError(f"Upload response missing data.url: {body}")
    return {
        "path": str(path),
        "url": str(data["url"]),
        "response": body,
    }


def mime_type_for_path(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return "image/png"


async def create_generation_task(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    response = await client.post(
        image_generation_url(base_url),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
    )
    body = response_body(response)
    require_success(response, body, label="create generation task")
    if not isinstance(body, dict):
        raise RuntimeError(f"Create task response is not an object: {body}")

    task_id = body.get("id") or body.get("task_id")
    if not task_id:
        raise RuntimeError(f"Create task response missing id/task_id: {body}")
    return str(task_id), body


async def wait_for_generation_result(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    api_key: str,
    task_id: str,
    poll_interval_seconds: float,
    max_wait_seconds: float,
) -> dict[str, Any]:
    started_at = asyncio.get_running_loop().time()
    polls = 0
    while True:
        polls += 1
        response = await client.get(
            task_url(base_url, task_id),
            headers={"Authorization": f"Bearer {api_key}"},
        )
        body = response_body(response)
        require_success(response, body, label=f"poll generation task {task_id}")
        if not isinstance(body, dict):
            raise RuntimeError(f"Task response is not an object: {body}")

        status = str(body.get("status") or "").lower()
        progress = body.get("progress", "-")
        print(f"poll={polls} status={status or '-'} progress={progress}")

        if status == "completed":
            return body
        if status == "failed":
            error = body.get("error") or body.get("fail_reason") or body
            raise RuntimeError(f"Generation failed: {error}")

        elapsed = asyncio.get_running_loop().time() - started_at
        if elapsed >= max_wait_seconds:
            raise TimeoutError(f"Task {task_id} did not complete after {max_wait_seconds:g}s; last status={status}")
        await asyncio.sleep(max(0.5, poll_interval_seconds))


def extract_image_urls(body: Any) -> list[str]:
    urls: list[str] = []
    collect_image_urls(body, urls)
    return dedupe_preserve_order(urls)


def collect_image_urls(value: Any, urls: list[str]) -> None:
    if isinstance(value, str):
        if value.startswith(("http://", "https://")) and looks_like_image_url(value):
            urls.append(value)
        return
    if isinstance(value, list):
        for item in value:
            collect_image_urls(item, urls)
        return
    if not isinstance(value, dict):
        return

    for key in ("url", "image_url", "image", "output_url"):
        if key in value:
            collect_image_urls(value[key], urls)
    for key in ("data", "result", "results", "output", "images", "choices"):
        if key in value:
            collect_image_urls(value[key], urls)


def looks_like_image_url(value: str) -> bool:
    path = urlparse(value).path.lower()
    suffix = Path(path).suffix
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".gif"}:
        return True
    return any(host in value for host in ("files.toapis.com", "oaidalleapiprodscus.blob.core.windows.net"))


def dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


async def download_first_image(
    client: httpx.AsyncClient,
    *,
    image_urls: list[str],
    output_dir: Path,
) -> Path:
    if not image_urls:
        raise RuntimeError("Completed task response did not include any image URL")

    image_url = image_urls[0]
    response = await client.get(image_url)
    if response.status_code >= 400:
        raise RuntimeError(f"Image download failed with HTTP {response.status_code}: {response.text[:500]}")

    extension = extension_from_response(response, image_url)
    output_path = output_dir / f"image.{extension}"
    output_path.write_bytes(response.content)
    return output_path


def extension_from_response(response: httpx.Response, url: str) -> str:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    by_content_type = {
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/webp": "webp",
        "image/gif": "gif",
    }
    if content_type in by_content_type:
        return by_content_type[content_type]

    suffix = Path(urlparse(url).path).suffix.lower().lstrip(".")
    if suffix == "jpeg":
        return "jpg"
    if suffix in {"png", "jpg", "webp", "gif"}:
        return suffix
    return "png"


def build_payload(args: argparse.Namespace, reference_image_urls: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": args.model,
        "prompt": args.prompt,
        "n": args.n,
        "size": args.size,
        "resolution": args.resolution,
        "response_format": args.response_format,
    }
    if reference_image_urls:
        payload["reference_images"] = reference_image_urls
        payload["image_urls"] = reference_image_urls
    return payload


async def main_async(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (ROOT_DIR / config_path).resolve()

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    if not output_dir.is_absolute():
        output_dir = (ROOT_DIR / output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    api_key = args.api_key or load_api_key(config_path, args.api_key_name)
    base_url = normalize_base_url(args.base_url)

    print("provider=toapi")
    print(f"base_url={base_url}")
    print(f"generation_endpoint={image_generation_url(base_url)}")
    print(f"model={args.model}")
    print(f"size={args.size}")
    print(f"resolution={args.resolution}")
    print("expected_pixels=1536x1024" if args.size == "3:2" and args.resolution == "1K" else "expected_pixels=see ToAPI size table")
    print(f"key_name={args.api_key_name}")
    print(f"key_present={bool(api_key)}")
    print(f"output_dir={output_dir}")

    if not api_key:
        print("generation_failed=missing ToAPI API key")
        print("Set TOAPI_API_KEY in apikeys.yaml or environment, or pass --api-key.")
        return 1

    timeout = httpx.Timeout(args.timeout_seconds, connect=min(30.0, args.timeout_seconds))
    uploaded_images: list[dict[str, Any]] = []
    reference_image_urls = list(args.reference_image_url)

    async with httpx.AsyncClient(timeout=timeout) as client:
        for raw_path in args.reference_image:
            path = Path(raw_path)
            if not path.is_absolute():
                path = (ROOT_DIR / path).resolve()
            uploaded = await upload_reference_image(client, base_url=base_url, api_key=api_key, path=path)
            uploaded_images.append(uploaded)
            reference_image_urls.append(uploaded["url"])

        if uploaded_images:
            write_json(output_dir / "uploaded_images.json", uploaded_images)

        payload = build_payload(args, reference_image_urls)
        write_json(output_dir / "request_payload.json", payload)

        if args.dry_run:
            print("dry_run=true")
            print(f"request_payload={output_dir / 'request_payload.json'}")
            return 0

        try:
            task_id, create_body = await create_generation_task(
                client,
                base_url=base_url,
                api_key=api_key,
                payload=payload,
            )
            write_json(output_dir / "task_create_response.json", create_body)
            print(f"task_id={task_id}")

            final_body = await wait_for_generation_result(
                client,
                base_url=base_url,
                api_key=api_key,
                task_id=task_id,
                poll_interval_seconds=args.poll_interval_seconds,
                max_wait_seconds=args.max_wait_seconds,
            )
            write_json(output_dir / "task_final_response.json", final_body)

            image_urls = extract_image_urls(final_body)
            write_json(output_dir / "image_urls.json", {"image_urls": image_urls})
            image_path = await download_first_image(client, image_urls=image_urls, output_dir=output_dir)
        except Exception as exc:
            print(f"generation_failed={exc}")
            return 1

    print("generation_succeeded=true")
    print(f"request_payload={output_dir / 'request_payload.json'}")
    print(f"task_create_response={output_dir / 'task_create_response.json'}")
    print(f"task_final_response={output_dir / 'task_final_response.json'}")
    print(f"image_urls={output_dir / 'image_urls.json'}")
    print(f"saved_image={image_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
