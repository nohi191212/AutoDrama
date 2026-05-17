from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import yaml


DEFAULT_CUSTOMIZATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/audio/tts/customization"
DEFAULT_WEBSOCKET_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_TARGET_MODEL = "cosyvoice-v3.5-plus"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Smoke test CosyVoice voice design and voice cloning via DashScope.",
    )
    parser.add_argument("--config", default="config.yaml", help="Project config file.")
    parser.add_argument("--provider", default="aliyun", help="Provider key in config.yaml.")
    parser.add_argument("--output-dir", default=None, help="Directory for JSON and audio outputs.")
    parser.add_argument("--target-model", default=None, help="CosyVoice synthesis model.")
    parser.add_argument("--customization-url", default=None, help="DashScope customization endpoint.")
    parser.add_argument("--websocket-url", default=None, help="DashScope TTS websocket endpoint.")
    parser.add_argument("--api-key", default=None, help="API key. Prefer env/config instead of this argument.")
    parser.add_argument("--voice-prompt", default="沉稳的青年男性声音，音色低沉克制，语速中等，咬字清晰，适合短剧角色独白。")
    parser.add_argument("--preview-text", default="我是林舟，一个总在办公室熬到深夜的普通职员。")
    parser.add_argument("--synthesis-text", default="<|NEUTRAL|>我是林舟。越是混乱的时候，我越要盯紧那些不该被忽略的细节。")
    parser.add_argument("--design-prefix", default="addesign", help="CosyVoice design prefix, alnum only, max 10.")
    parser.add_argument(
        "--clone-audio-url",
        default=os.getenv("COSYVOICE_CLONE_AUDIO_URL"),
        help="Public WAV/MP3/M4A URL for CosyVoice cloning. If omitted, clone test is skipped.",
    )
    parser.add_argument("--clone-prefix", default="adclone", help="CosyVoice clone prefix, alnum only, max 10.")
    parser.add_argument("--clone-synthesis-text", default="<|CALM|>这是一段使用复刻音色合成的测试语音。")
    parser.add_argument("--language", default=None, help="language_hints first value, usually zh or en.")
    parser.add_argument("--sample-rate", type=int, default=None)
    parser.add_argument("--response-format", default=None)
    parser.add_argument("--poll-interval-seconds", type=float, default=3.0)
    parser.add_argument("--max-polls", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    parser.add_argument("--skip-synthesis", action="store_true", help="Create voices but do not synthesize audio.")
    parser.add_argument("--dry-run", action="store_true", help="Print sanitized payloads without calling DashScope.")
    parser.add_argument("--no-proxy-env", action="store_true", help="Ignore HTTP(S)_PROXY environment variables for HTTP calls.")
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


def resolve_websocket_url(provider: dict[str, Any], explicit_url: str | None) -> str:
    if explicit_url:
        return explicit_url.rstrip("/")

    options = provider.get("options") or {}
    configured = options.get("websocket_url")
    if configured:
        return str(configured).rstrip("/")

    base_url = str(provider.get("base_url") or "")
    if "dashscope-intl.aliyuncs.com" in base_url:
        return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
    return DEFAULT_WEBSOCKET_URL


def target_model(provider: dict[str, Any], explicit_model: str | None) -> str:
    if explicit_model:
        return explicit_model
    models = provider.get("models") or {}
    return str(models.get("target_model") or models.get("tts") or DEFAULT_TARGET_MODEL)


def option(provider: dict[str, Any], key: str, default: Any) -> Any:
    options = provider.get("options") or {}
    return options.get(key, default)


def safe_prefix(value: str) -> str:
    prefix = re.sub(r"[^A-Za-z0-9]", "", value)
    return (prefix or "voice")[:10]


def make_design_payload(args: argparse.Namespace, provider: dict[str, Any], model: str) -> dict[str, Any]:
    language = args.language or str(option(provider, "language", "zh"))
    sample_rate = args.sample_rate or int(option(provider, "sample_rate", 24000))
    response_format = args.response_format or str(option(provider, "response_format", "wav"))
    return {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": model,
            "voice_prompt": args.voice_prompt[:500],
            "preview_text": args.preview_text[:200],
            "prefix": safe_prefix(args.design_prefix),
            "language_hints": [language],
        },
        "parameters": {
            "sample_rate": sample_rate,
            "response_format": response_format,
        },
    }


def make_clone_payload(args: argparse.Namespace, model: str) -> dict[str, Any] | None:
    if not args.clone_audio_url:
        return None
    return {
        "model": "voice-enrollment",
        "input": {
            "action": "create_voice",
            "target_model": model,
            "prefix": safe_prefix(args.clone_prefix),
            "url": args.clone_audio_url,
        },
    }


def sanitized_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(payload, ensure_ascii=False))
    input_data = sanitized.get("input") or {}
    url = input_data.get("url")
    if isinstance(url, str) and len(url) > 120:
        input_data["url"] = f"{url[:80]}...{url[-24:]}"
    return sanitized


def sanitize_response(body: dict[str, Any]) -> dict[str, Any]:
    sanitized = json.loads(json.dumps(body, ensure_ascii=False))
    output = sanitized.get("output") or {}
    preview_audio = output.get("preview_audio")
    if isinstance(preview_audio, dict) and "data" in preview_audio:
        preview_audio["data"] = "<base64 preview audio omitted>"
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


def extract_voice_id(body: dict[str, Any]) -> str:
    output = body.get("output") or {}
    voice_id = output.get("voice_id") or output.get("voice")
    if not voice_id:
        raise RuntimeError(f"Response missing output.voice_id/output.voice: {body}")
    return str(voice_id)


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


def query_voice(client: httpx.Client, url: str, api_key: str, voice_id: str) -> dict[str, Any]:
    payload = {
        "model": "voice-enrollment",
        "input": {
            "action": "query_voice",
            "voice_id": voice_id,
        },
    }
    return post_json(client, url, api_key, payload)


def wait_for_voice(
    client: httpx.Client,
    url: str,
    api_key: str,
    voice_id: str,
    output_dir: Path,
    label: str,
    max_polls: int,
    interval_seconds: float,
) -> dict[str, Any] | None:
    last_body: dict[str, Any] | None = None
    for poll_index in range(1, max_polls + 1):
        body = query_voice(client, url, api_key, voice_id)
        last_body = body
        save_json(output_dir / f"{label}_query_{poll_index:02d}.json", sanitize_response(body))
        output = body.get("output") or {}
        status = output.get("status")
        if status in (None, "OK"):
            print(f"{label}: voice query status={status or 'NO_STATUS'}, usable")
            return body
        print(f"{label}: voice query status={status}, poll {poll_index}/{max_polls}")
        if status == "UNDEPLOYED":
            raise RuntimeError(f"{label}: voice was rejected/undeployed: {body}")
        time.sleep(interval_seconds)
    raise RuntimeError(f"{label}: voice did not become OK after {max_polls} polls; last={last_body}")


def synthesize_with_cosyvoice_sdk(
    *,
    api_key: str,
    websocket_url: str,
    model: str,
    voice_id: str,
    text: str,
    output_path: Path,
) -> dict[str, Any]:
    try:
        import dashscope
        from dashscope.audio.tts_v2 import SpeechSynthesizer
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependency 'dashscope'. Install it with: "
            "D:/miniforge3/envs/autodrama/python.exe -m pip install dashscope"
        ) from exc

    dashscope.api_key = api_key
    dashscope.base_websocket_api_url = websocket_url
    synthesizer = SpeechSynthesizer(model=model, voice=voice_id)
    audio = synthesizer.call(text)
    if not isinstance(audio, (bytes, bytearray)):
        raise RuntimeError(f"Unexpected CosyVoice SDK audio result type: {type(audio)}")

    output_path.write_bytes(bytes(audio))
    request_id = None
    if get_request_id := getattr(synthesizer, "get_last_request_id", None):
        request_id = get_request_id()
    first_package_delay_ms = None
    if get_delay := getattr(synthesizer, "get_first_package_delay", None):
        first_package_delay_ms = get_delay()

    return {
        "model": model,
        "voice_id": voice_id,
        "text": text,
        "audio_path": str(output_path),
        "request_id": request_id,
        "first_package_delay_ms": first_package_delay_ms,
        "websocket_url": websocket_url,
    }


def default_output_dir() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path("outputs") / "cosyvoice_smoke" / stamp


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    api_keys = load_api_keys(config, args.config)
    provider = provider_config(config, args.provider)

    model = target_model(provider, args.target_model)
    customization_url = resolve_customization_url(provider, args.customization_url)
    websocket_url = resolve_websocket_url(provider, args.websocket_url)
    api_key = resolve_api_key(provider, args.api_key, api_keys)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    design_payload = make_design_payload(args, provider, model)
    clone_payload = make_clone_payload(args, model)

    print(f"target_model={model}")
    print(f"customization_url={customization_url}")
    print(f"websocket_url={websocket_url}")
    print(f"api_key={'SET' if api_key else 'MISSING'}")
    print(f"output_dir={output_dir.resolve()}")

    if args.dry_run:
        print("design payload:")
        print(json.dumps(sanitized_payload(design_payload), ensure_ascii=False, indent=2))
        if clone_payload:
            print("clone payload:")
            print(json.dumps(sanitized_payload(clone_payload), ensure_ascii=False, indent=2))
        else:
            print("clone payload: SKIPPED because --clone-audio-url was not provided")
        return 0

    if not api_key:
        raise RuntimeError("Missing DashScope API key. Set ALIYUN_API_KEY in apikeys.yaml or configure provider api_key_env.")

    with httpx.Client(timeout=args.timeout_seconds, trust_env=not args.no_proxy_env) as client:
        design_body = post_json(client, customization_url, api_key, design_payload)
        design_voice_id = extract_voice_id(design_body)
        save_json(output_dir / "design_create.json", sanitize_response(design_body))
        design_preview_path = save_preview_audio(output_dir / "design_preview", design_body)
        print(f"design voice_id={design_voice_id}")
        if design_preview_path:
            print(f"design preview_audio={design_preview_path}")

        wait_for_voice(
            client,
            customization_url,
            api_key,
            design_voice_id,
            output_dir,
            "design",
            args.max_polls,
            args.poll_interval_seconds,
        )

        if not args.skip_synthesis:
            design_synthesis = synthesize_with_cosyvoice_sdk(
                api_key=api_key,
                websocket_url=websocket_url,
                model=model,
                voice_id=design_voice_id,
                text=args.synthesis_text,
                output_path=output_dir / "design_synthesis.mp3",
            )
            save_json(output_dir / "design_synthesis.json", design_synthesis)
            print(f"design synthesis_audio={design_synthesis['audio_path']}")

        if not clone_payload:
            print("clone: SKIPPED because --clone-audio-url was not provided")
            return 0

        clone_body = post_json(client, customization_url, api_key, clone_payload)
        clone_voice_id = extract_voice_id(clone_body)
        save_json(output_dir / "clone_create.json", sanitize_response(clone_body))
        print(f"clone voice_id={clone_voice_id}")

        wait_for_voice(
            client,
            customization_url,
            api_key,
            clone_voice_id,
            output_dir,
            "clone",
            args.max_polls,
            args.poll_interval_seconds,
        )

        if not args.skip_synthesis:
            clone_synthesis = synthesize_with_cosyvoice_sdk(
                api_key=api_key,
                websocket_url=websocket_url,
                model=model,
                voice_id=clone_voice_id,
                text=args.clone_synthesis_text,
                output_path=output_dir / "clone_synthesis.mp3",
            )
            save_json(output_dir / "clone_synthesis.json", clone_synthesis)
            print(f"clone synthesis_audio={clone_synthesis['audio_path']}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
