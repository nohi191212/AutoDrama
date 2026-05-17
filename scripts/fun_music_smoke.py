from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

import httpx


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


DEFAULT_PROMPT = (
    "uplifting cinematic corporate background music, steady rhythm, "
    "warm piano and light strings, no lyrics, suitable for a short office drama"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test Alibaba Bailian Fun-Music generation.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Music generation prompt.")
    parser.add_argument("--output", default="outputs/_smoke/fun_music_smoke", help="Output file path without extension.")
    return parser


async def download_audio(url: str, output_path: Path, timeout: int) -> None:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url)
    if response.status_code >= 400:
        raise RuntimeError(f"Audio download failed with HTTP {response.status_code}: {response.text[:300]}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)


async def main_async(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.config))
    provider = ProviderRouter(settings).music("bgm")
    endpoint = getattr(provider, "endpoint", "-")
    model = getattr(provider, "model", "fun-music-v1")
    api_key = getattr(provider, "api_key", None)
    audio_format = getattr(provider, "audio_format", "mp3")
    gender = getattr(provider, "gender", "female")

    print(f"provider={getattr(provider, 'name', 'unknown')}")
    print(f"endpoint={endpoint}")
    print(f"model={model}")
    print(f"key_present={bool(api_key)}")

    if not api_key:
        print("generation_failed=missing DashScope/Bailian API key")
        return 1

    payload = {
        "model": model,
        "input": {
            "prompt": args.prompt[:2000],
            "gender": gender,
            "format": audio_format,
            "enable_aigc_watermark": False,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=settings.runtime.request_timeout_seconds) as client:
            response = await client.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except httpx.HTTPError as exc:
        print(f"generation_failed=request error: {exc}")
        return 1

    print(f"http_status={response.status_code}")
    if response.status_code >= 400:
        print(f"generation_failed={response.text[:500]}")
        return 1

    try:
        body = response.json()
    except ValueError as exc:
        print(f"generation_failed=non-JSON response: {exc}")
        return 1

    output = body.get("output") or {}
    audio = output.get("audio") or {}
    extra_info = output.get("extra_info") or {}
    usage = body.get("usage") or {}

    audio_data = audio.get("data") or None
    audio_url = audio.get("url") or None

    resolved_format = (audio_format or "mp3").lower().lstrip(".")
    if resolved_format not in {"mp3", "wav", "m4a", "aac", "ogg"}:
        resolved_format = "mp3"
    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = ROOT_DIR / output_path
    if output_path.suffix:
        final_path = output_path
    else:
        final_path = output_path.with_suffix(f".{resolved_format}")

    if audio_data:
        data = str(audio_data)
        if data.startswith("data:") and ";base64," in data:
            data = data.split(";base64,", 1)[1]
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_bytes(base64.b64decode(data))
    elif audio_url:
        await download_audio(str(audio_url), final_path, settings.runtime.request_timeout_seconds)
    else:
        print("generation_failed=music result has no audio data or URL")
        return 1

    print(f"request_id={body.get('request_id') or body.get('requestId') or '-'}")
    print(f"audio_id={audio.get('id') or '-'}")
    print(f"duration_seconds={usage.get('duration') or '-'}")
    print(f"sample_rate={extra_info.get('sample_rate') or '-'}")
    print(f"saved={final_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
