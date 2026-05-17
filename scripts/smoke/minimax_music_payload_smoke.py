from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


DEFAULT_PROMPT = (
    "cinematic instrumental background score, suspenseful but restrained, "
    "low strings, soft piano pulses, no vocals, suitable for a short drama scene"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dry-run MiniMax Music 2.6 BGM payload through the configured router.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    settings = load_settings(ROOT_DIR / args.config)
    provider = ProviderRouter(settings).music("bgm")

    if getattr(provider, "name", None) != "minimax":
        raise AssertionError(f"Expected BGM music provider minimax, got {getattr(provider, 'name', 'unknown')}")
    if getattr(provider, "model", None) != "music-2.6":
        raise AssertionError(f"Expected MiniMax model music-2.6, got {getattr(provider, 'model', '-')}")

    build_payload = getattr(provider, "build_generation_payload", None)
    if not callable(build_payload):
        raise AssertionError("MiniMax provider does not expose build_generation_payload")

    payload = build_payload(args.prompt, metadata={"asset_id": "bgm_smoke"})
    assert payload["model"] == "music-2.6"
    assert payload["output_format"] == "url"
    assert payload["is_instrumental"] is True
    assert payload["audio_setting"]["format"] == "mp3"
    assert payload["audio_setting"]["sample_rate"] == 44100
    assert payload["audio_setting"]["bitrate"] == 256000

    url, data = provider._extract_audio({"data": {"audio": "https://example.com/generated.mp3"}})
    assert url == "https://example.com/generated.mp3"
    assert data is None

    hex_audio = "00" * 16
    url, data = provider._extract_audio({"data": {"audio": hex_audio}})
    assert url is None
    assert data == base64.b64encode(bytes.fromhex(hex_audio)).decode("ascii")
    assert provider._extract_duration_seconds({"data": {"extra_info": {"music_duration": 12345}}}) == 12.345

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "minimax_music_payload"
    output_dir.mkdir(parents=True, exist_ok=True)
    payload_path = output_dir / "request_payload.json"
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print("minimax_music_payload_smoke=ok")
    print(f"provider={provider.name}")
    print(f"endpoint={provider.endpoint}")
    print(f"model={provider.model}")
    print(f"api_key_present={bool(getattr(provider, 'api_key', None))}")
    print(f"payload_path={payload_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
