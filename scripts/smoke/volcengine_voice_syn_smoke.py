from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.volcengine.audio.seed_tts import VolcengineSeedTTSProvider  # noqa: E402


DEFAULT_TEXT = "你到底还瞒了我多少事？我已经没有时间再等一个解释了。"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke test Volcengine Seed TTS speech synthesis.")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml.")
    parser.add_argument("--provider", default="volcengine", help="Provider key in config.yaml.")
    parser.add_argument("--speaker", default=None, help="Override speaker/voice_type.")
    parser.add_argument("--emotion", default="tense", help="Role emotion key, e.g. normal/tense/angry/sad/happy/whisper.")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="Text to synthesize.")
    parser.add_argument("--model", default=None, help="Override speech synthesis resource model, e.g. seed-tts-2.0.")
    parser.add_argument("--req-model", default=None, help="Optional req_params.model for TTS 2.0, e.g. seed-tts-2.0-expressive.")
    parser.add_argument("--resource-id", default=None, help="Override X-Api-Resource-Id.")
    parser.add_argument("--instruction-mode", default=None, help="Override instruction mode: text_prefix/additions/req_params/none.")
    parser.add_argument("--api-key", default=None, help="API key. Prefer env/config instead of this argument.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Override HTTP timeout.")
    parser.add_argument("--output-dir", default=None, help="Directory for response JSON and generated audio.")
    parser.add_argument("--dry-run", action="store_true", help="Print request payload without calling Volcengine.")
    return parser


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return ROOT_DIR / ".tmp" / "smoke" / "volcengine_voice_syn_smoke" / stamp


def sanitize_response(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, nested in value.items():
            if key in {"audio", "audio_data", "data", "content"} and isinstance(nested, str):
                if not nested.startswith(("http://", "https://")):
                    sanitized[key] = f"<base64 omitted; chars={len(nested)}>"
                    continue
            sanitized[key] = sanitize_response(nested)
        return sanitized
    if isinstance(value, list):
        return [sanitize_response(item) for item in value]
    return value


def write_json(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sanitize_response(body), ensure_ascii=False, indent=2), encoding="utf-8")


def audio_extension(audio_format: str | None) -> str:
    extension = (audio_format or "mp3").lower().lstrip(".")
    extension = {"ogg_opus": "opus"}.get(extension, extension)
    if extension not in {"mp3", "wav", "pcm", "opus", "ogg", "m4a", "aac"}:
        return "bin"
    return extension


async def main_async(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.config))
    provider_settings = settings.providers.get(args.provider)
    if provider_settings is None:
        print(f"generation_failed=provider '{args.provider}' not found in config")
        return 1

    if args.timeout_seconds is not None:
        settings.runtime.request_timeout_seconds = args.timeout_seconds

    provider = VolcengineSeedTTSProvider(provider_settings, settings.runtime)
    if args.api_key:
        provider.api_key = args.api_key
    if args.model:
        provider.model = args.model
        provider.resource_id = args.model
    if args.resource_id:
        provider.resource_id = args.resource_id
    if args.instruction_mode:
        provider.instruction_mode = args.instruction_mode

    speaker = args.speaker or provider.default_speaker
    plan = provider.resolve_emotion_plan(args.emotion)
    emotion_params = provider.emotion_params_from_plan(plan)
    instruction = str(plan.get("instruction") or "").strip() or None
    metadata: dict[str, Any] = {
        "project_id": "volcengine_voice_syn_smoke",
        "role_id": "smoke_role",
        "role_name": "SmokeRole",
        "audio_id": f"smoke_{args.emotion}",
        "emotion": args.emotion,
        "generation_method": "synthesis",
        "emotion_instruction": instruction,
        "emotion_params": emotion_params,
    }
    if args.model:
        metadata["model"] = args.model
    if args.req_model:
        metadata["req_params_model"] = args.req_model
    if args.resource_id:
        metadata["resource_id"] = args.resource_id
    if args.instruction_mode:
        metadata["instruction_mode"] = args.instruction_mode

    payload = provider.build_synthesis_payload(voice=speaker, text=args.text, metadata=metadata)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    if not output_dir.is_absolute():
        output_dir = (ROOT_DIR / output_dir).resolve()

    print(f"provider={provider.name}")
    print(f"endpoint={provider.endpoint}")
    print(f"model={provider.model}")
    print(f"resource_id={provider.resource_id}")
    print(f"fallback_resource_id={provider.fallback_resource_id}")
    print(f"req_params_model={args.req_model or provider.request_model or '-'}")
    print(f"speaker={speaker}")
    print(f"emotion={args.emotion}")
    print(f"instruction_mode={provider.instruction_mode}")
    print(f"key_present={bool(provider.api_key or (provider.app_key and provider.access_key))}")
    print(f"output_dir={output_dir}")

    if args.dry_run:
        print("dry_run=true")
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    if not (provider.api_key or (provider.app_key and provider.access_key)):
        print("generation_failed=missing Volcengine Speech credentials")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        result = await provider.synthesize_speech(voice=speaker, text=args.text, metadata=metadata)
    except Exception as exc:
        print(f"generation_failed={exc}")
        return 1

    extension = audio_extension(result.audio_format)
    audio_path = output_dir / f"audio.{extension}"
    if not result.audio_data:
        print("generation_failed=response has no audio_data")
        return 1
    audio_path.write_bytes(base64.b64decode(result.audio_data))

    response_path = output_dir / "response.json"
    write_json(response_path, result.raw_response)

    print(f"request_id={result.request_id or '-'}")
    print(f"sample_rate={result.audio_sample_rate or '-'}")
    print(f"response_format={result.audio_format or '-'}")
    print(f"response_json={response_path}")
    print(f"saved_audio={audio_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
