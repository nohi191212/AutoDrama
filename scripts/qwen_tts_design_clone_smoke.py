from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml


DEFAULT_CUSTOMIZATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
DEFAULT_GENERATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation"
DEFAULT_DESIGN_TARGET_MODEL = "qwen3-tts-vd-2026-01-26"
DEFAULT_CLONE_TARGET_MODEL = "qwen3-tts-vc-2026-01-22"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test Qwen-TTS voice design, optional voice cloning, and speech synthesis via DashScope.",
    )
    parser.add_argument("--config", default="config.yaml", help="Project config file.")
    parser.add_argument("--provider", default="aliyun", help="Provider key in config.yaml.")
    parser.add_argument("--output-dir", default=None, help="Directory for JSON and audio outputs.")
    parser.add_argument("--customization-url", default=None, help="DashScope customization endpoint.")
    parser.add_argument("--generation-url", default=None, help="DashScope multimodal generation endpoint.")
    parser.add_argument("--api-key", default=None, help="API key. Prefer env/config instead of this argument.")
    parser.add_argument("--design-target-model", default=DEFAULT_DESIGN_TARGET_MODEL)
    parser.add_argument("--clone-target-model", default=DEFAULT_CLONE_TARGET_MODEL)
    parser.add_argument("--voice-prompt", default="沉稳的青年男性声音，音色低沉克制，语速中等，咬字清晰，适合短剧角色独白。")
    parser.add_argument("--preview-text", default="我是林舟，一个总在办公室熬到深夜的普通职员。")
    parser.add_argument("--synthesis-text", default="我是林舟。越是混乱的时候，我越要盯紧那些不该被忽略的细节。")
    parser.add_argument("--preferred-name", default="addesign", help="Qwen preferred_name, alnum/underscore only, max 16.")
    parser.add_argument("--language", default=None, help="Qwen language hint, usually zh or en.")
    parser.add_argument("--sample-rate", type=int, default=None)
    parser.add_argument("--response-format", default=None)
    parser.add_argument(
        "--clone-audio-path",
        default=None,
        help="Local WAV/MP3/M4A file for Qwen-TTS voice cloning. If omitted, clone can use --clone-audio-url.",
    )
    parser.add_argument(
        "--clone-audio-url",
        default=os.getenv("QWEN_TTS_CLONE_AUDIO_URL"),
        help="Public WAV/MP3/M4A URL for Qwen-TTS voice cloning.",
    )
    parser.add_argument("--clone-preferred-name", default="adclone", help="Qwen clone preferred_name, max 16.")
    parser.add_argument("--clone-synthesis-text", default="这是一段使用千问语音复刻音色合成的测试语音。")
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--skip-design", action="store_true", help="Skip voice design and only run clone if clone input exists.")
    parser.add_argument("--skip-clone", action="store_true", help="Skip voice cloning.")
    parser.add_argument("--skip-synthesis", action="store_true", help="Create voices but do not synthesize audio.")
    parser.add_argument("--dry-run", action="store_true", help="Print sanitized payloads without calling DashScope.")
    parser.add_argument("--no-proxy-env", action="store_true", help="Ignore HTTP(S)_PROXY environment variables.")
    return parser.parse_args()


def load_config(path: str | Path) -> dict[str, Any]:
    config_path = Path(path).expanduser().resolve()
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}


def load_api_keys(config: dict[str, Any], config_path: str | Path) -> dict[str, str]:
    apikeys_path = Path(config.get("apikeys_file") or "apikeys.yaml").expanduser()
    if not apikeys_path.is_absolute():
        apikeys_path = Path(config_path).expanduser().resolve().parent / apikeys_path
    if not apikeys_path.exists():
        return {}
    data = yaml.safe_load(apikeys_path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return {str(key): str(value) for key, value in data.items() if value is not None}


def provider_config(config: dict[str, Any], provider_name: str) -> dict[str, Any]:
    providers = config.get("providers") or {}
    provider = providers.get(provider_name)
    if not isinstance(provider, dict):
        raise KeyError(f"Provider '{provider_name}' not found in config")
    return provider


def resolve_api_key(provider: dict[str, Any], explicit_api_key: str | None, api_keys: dict[str, str]) -> str | None:
    if explicit_api_key:
        return explicit_api_key

    api_key_env = provider.get("api_key_env")
    if isinstance(api_key_env, str) and api_key_env in api_keys:
        return api_keys[api_key_env]
    if isinstance(api_key_env, str) and api_key_env.startswith("sk-"):
        return api_key_env
    if isinstance(api_key_env, str) and api_key_env:
        return os.getenv(api_key_env)
    return api_keys.get("ALIYUN_API_KEY") or os.getenv("ALIYUN_API_KEY")


def resolve_customization_url(provider: dict[str, Any], explicit_url: str | None) -> str:
    if explicit_url:
        return explicit_url.rstrip("/")

    base_url = str(provider.get("base_url") or "").rstrip("/")
    if not base_url:
        return DEFAULT_CUSTOMIZATION_URL
    if base_url.endswith("/services/audio/tts/customization"):
        return base_url
    if base_url.endswith("/api/v1"):
        return f"{base_url}/services/audio/tts/customization"
    return f"{base_url}/api/v1/services/audio/tts/customization"


def resolve_generation_url(provider: dict[str, Any], explicit_url: str | None) -> str:
    if explicit_url:
        return explicit_url.rstrip("/")

    base_url = str(provider.get("base_url") or "").rstrip("/")
    if not base_url:
        return DEFAULT_GENERATION_URL
    if base_url.endswith("/services/aigc/multimodal-generation/generation"):
        return base_url
    if base_url.endswith("/services/audio/tts/customization"):
        return base_url.replace(
            "/services/audio/tts/customization",
            "/services/aigc/multimodal-generation/generation",
        )
    if base_url.endswith("/api/v1"):
        return f"{base_url}/services/aigc/multimodal-generation/generation"
    return DEFAULT_GENERATION_URL


def option(provider: dict[str, Any], key: str, default: Any) -> Any:
    options = provider.get("options") or {}
    return options.get(key, default)


def safe_preferred_name(value: str) -> str:
    preferred_name = re.sub(r"[^A-Za-z0-9_]", "", value)
    return (preferred_name or "voice")[:16]


def make_design_payload(args: argparse.Namespace, provider: dict[str, Any]) -> dict[str, Any]:
    language = args.language or str(option(provider, "language", "zh"))
    sample_rate = args.sample_rate or int(option(provider, "sample_rate", 24000))
    response_format = args.response_format or str(option(provider, "response_format", "wav"))
    return {
        "model": "qwen-voice-design",
        "input": {
            "action": "create",
            "target_model": args.design_target_model,
            "preferred_name": safe_preferred_name(args.preferred_name),
            "voice_prompt": args.voice_prompt[:2048],
            "preview_text": args.preview_text[:1024],
            "language": language,
        },
        "parameters": {
            "sample_rate": sample_rate,
            "response_format": response_format,
        },
    }


def clone_audio_data(args: argparse.Namespace) -> str | None:
    if args.clone_audio_path:
        audio_path = Path(args.clone_audio_path).expanduser().resolve()
        if not audio_path.exists():
            raise FileNotFoundError(f"Clone audio file not found: {audio_path}")
        mime_type = mimetypes.guess_type(audio_path.name)[0] or "audio/wav"
        encoded = base64.b64encode(audio_path.read_bytes()).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    if args.clone_audio_url:
        return args.clone_audio_url
    return None


def make_clone_payload(args: argparse.Namespace) -> dict[str, Any] | None:
    audio_data = clone_audio_data(args)
    if not audio_data:
        return None
    return {
        "model": "qwen-voice-enrollment",
        "input": {
            "action": "create",
            "target_model": args.clone_target_model,
            "preferred_name": safe_preferred_name(args.clone_preferred_name),
            "audio": {"data": audio_data},
        },
    }


def make_synthesis_payload(model: str, voice: str, text: str) -> dict[str, Any]:
    return {
        "model": model,
        "input": {
            "text": text[:1024],
            "voice": voice,
        },
    }


def sanitized_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(payload, ensure_ascii=False))
    audio = (sanitized.get("input") or {}).get("audio")
    if isinstance(audio, dict):
        data = audio.get("data")
        if isinstance(data, str) and data.startswith("data:"):
            audio["data"] = "<base64 audio omitted>"
        elif isinstance(data, str) and len(data) > 120:
            audio["data"] = f"{data[:80]}...{data[-24:]}"
    return sanitized


def sanitize_response(body: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(body, ensure_ascii=False))
    output = sanitized.get("output") or {}
    preview_audio = output.get("preview_audio")
    if isinstance(preview_audio, dict) and "data" in preview_audio:
        preview_audio["data"] = "<base64 preview audio omitted>"
    for key in ("audio", "audio_data", "data", "result"):
        value = output.get(key)
        if isinstance(value, dict):
            for nested_key in ("data", "audio", "content"):
                if nested_key in value:
                    value[nested_key] = "<base64 audio omitted>"
        elif isinstance(value, str) and not value.startswith("http"):
            output[key] = "<base64 audio omitted>"
    return sanitized


def post_json(client: httpx.Client, url: str, api_key: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        response = client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    except httpx.ConnectError as exc:
        raise RuntimeError(f"Connection failed before HTTP response. Check network/proxy/TLS settings: {exc}") from exc

    if response.status_code >= 400:
        raise RuntimeError(f"DashScope HTTP {response.status_code}: {response.text[:1000]}")

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(f"DashScope returned non-JSON response: {exc}; body={response.text[:1000]}") from exc


def extract_voice(body: dict[str, Any]) -> str:
    output = body.get("output") or {}
    voice = output.get("voice") or output.get("voice_id")
    if not voice:
        raise RuntimeError(f"Response missing output.voice/output.voice_id: {body}")
    return str(voice)


def save_json(path: Path, body: dict[str, Any]) -> None:
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def save_preview_audio(path: Path, body: dict[str, Any]) -> Path | None:
    preview_audio = (body.get("output") or {}).get("preview_audio") or {}
    data = preview_audio.get("data")
    if not data:
        return None
    audio_format = str(preview_audio.get("response_format") or "wav").lower()
    output_path = path.with_suffix(f".{audio_format}")
    output_path.write_bytes(base64.b64decode(data))
    return output_path


def extract_audio_ref(body: dict[str, Any], default_format: str) -> tuple[str | None, str | None, str]:
    output = body.get("output") or {}
    for key in ("audio", "audio_data", "result"):
        value = output.get(key)
        if isinstance(value, dict):
            data = value.get("data") or value.get("audio") or value.get("content")
            url = value.get("url") or value.get("audio_url")
            audio_format = value.get("format") or value.get("response_format") or default_format
            return data, url, str(audio_format)
        if isinstance(value, str):
            if value.startswith("http://") or value.startswith("https://"):
                return None, value, str(output.get("format") or output.get("response_format") or default_format)
            return value, None, str(output.get("format") or output.get("response_format") or default_format)

    data = output.get("data")
    url = output.get("url") or output.get("audio_url")
    audio_format = output.get("format") or output.get("response_format") or default_format
    return data, url, str(audio_format)


def write_audio_from_response(
    client: httpx.Client,
    output_path_base: Path,
    body: dict[str, Any],
    default_format: str,
) -> Path:
    data, url, audio_format = extract_audio_ref(body, default_format)
    suffix = audio_format.lower().lstrip(".") or "bin"
    if suffix not in {"wav", "mp3", "pcm", "opus"}:
        suffix = "bin"
    output_path = output_path_base.with_suffix(f".{suffix}")

    if url:
        response = client.get(url)
        if response.status_code >= 400:
            raise RuntimeError(f"Failed to download synthesis audio HTTP {response.status_code}: {response.text[:500]}")
        output_path.write_bytes(response.content)
        return output_path

    if not data:
        raise RuntimeError(f"Synthesis response has no audio data/url: {body}")
    if data.startswith("data:") and ";base64," in data:
        data = data.split(";base64,", 1)[1]
    output_path.write_bytes(base64.b64decode(data))
    return output_path


def synthesize(
    *,
    client: httpx.Client,
    generation_url: str,
    api_key: str,
    model: str,
    voice: str,
    text: str,
    output_dir: Path,
    label: str,
    response_format: str,
) -> Path:
    payload = make_synthesis_payload(model, voice, text)
    body = post_json(client, generation_url, api_key, payload)
    save_json(output_dir / f"{label}_synthesis.json", sanitize_response(body))
    audio_path = write_audio_from_response(client, output_dir / f"{label}_synthesis", body, response_format)
    print(f"{label} synthesis_audio={audio_path}")
    return audio_path


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("outputs") / "qwen_tts_smoke" / stamp


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    api_keys = load_api_keys(config, args.config)
    provider = provider_config(config, args.provider)

    customization_url = resolve_customization_url(provider, args.customization_url)
    generation_url = resolve_generation_url(provider, args.generation_url)
    api_key = resolve_api_key(provider, args.api_key, api_keys)
    response_format = args.response_format or str(option(provider, "response_format", "wav"))
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    design_payload = None if args.skip_design else make_design_payload(args, provider)
    clone_payload = None if args.skip_clone else make_clone_payload(args)

    print(f"design_target_model={args.design_target_model}")
    print(f"clone_target_model={args.clone_target_model}")
    print(f"customization_url={customization_url}")
    print(f"generation_url={generation_url}")
    print(f"api_key={'SET' if api_key else 'MISSING'}")
    print(f"output_dir={output_dir.resolve()}")

    if args.dry_run:
        if design_payload:
            print("design payload:")
            print(json.dumps(sanitized_payload(design_payload), ensure_ascii=False, indent=2))
            print("design synthesis payload:")
            print(
                json.dumps(
                    make_synthesis_payload(args.design_target_model, "YOUR_QWEN_DESIGN_VOICE", args.synthesis_text),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("design payload: SKIPPED")
        if clone_payload:
            print("clone payload:")
            print(json.dumps(sanitized_payload(clone_payload), ensure_ascii=False, indent=2))
            print("clone synthesis payload:")
            print(
                json.dumps(
                    make_synthesis_payload(args.clone_target_model, "YOUR_QWEN_CLONE_VOICE", args.clone_synthesis_text),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print("clone payload: SKIPPED because no clone audio was provided or --skip-clone was set")
        return 0

    if not api_key:
        raise RuntimeError("Missing DashScope API key. Set ALIYUN_API_KEY in apikeys.yaml or configure provider api_key_env.")

    with httpx.Client(timeout=args.timeout_seconds, trust_env=not args.no_proxy_env) as client:
        if design_payload:
            design_body = post_json(client, customization_url, api_key, design_payload)
            design_voice = extract_voice(design_body)
            save_json(output_dir / "design_create.json", sanitize_response(design_body))
            preview_path = save_preview_audio(output_dir / "design_preview", design_body)
            print(f"design voice={design_voice}")
            if preview_path:
                print(f"design preview_audio={preview_path}")
            if not args.skip_synthesis:
                synthesize(
                    client=client,
                    generation_url=generation_url,
                    api_key=api_key,
                    model=args.design_target_model,
                    voice=design_voice,
                    text=args.synthesis_text,
                    output_dir=output_dir,
                    label="design",
                    response_format=response_format,
                )

        if clone_payload:
            clone_body = post_json(client, customization_url, api_key, clone_payload)
            clone_voice = extract_voice(clone_body)
            save_json(output_dir / "clone_create.json", sanitize_response(clone_body))
            print(f"clone voice={clone_voice}")
            if not args.skip_synthesis:
                synthesize(
                    client=client,
                    generation_url=generation_url,
                    api_key=api_key,
                    model=args.clone_target_model,
                    voice=clone_voice,
                    text=args.clone_synthesis_text,
                    output_dir=output_dir,
                    label="clone",
                    response_format=response_format,
                )
        elif not args.skip_clone:
            print("clone: SKIPPED because --clone-audio-path/--clone-audio-url was not provided")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
