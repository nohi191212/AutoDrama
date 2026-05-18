from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings, load_settings  # noqa: E402
from autodrama.providers.volcengine.audio.seed_tts import VolcengineSeedTTSProvider  # noqa: E402


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml")
    provider = VolcengineSeedTTSProvider(settings.providers["volcengine"], settings.runtime)
    assert provider.api_key == settings.api_keys["SEED_TTS_API_KEY"]

    fallback_settings = ProviderSettings(
        api_key_env="VOLCENGINE_API_KEY",
        options={},
        api_keys={"VOLCENGINE_API_KEY": "fallback-key"},
    )
    fallback_provider = VolcengineSeedTTSProvider(fallback_settings, RuntimeSettings())
    assert fallback_provider.api_key == "fallback-key"

    override_settings = ProviderSettings(
        api_key_env="VOLCENGINE_API_KEY",
        options={"seed_tts_api_key_env": "SEED_TTS_API_KEY"},
        api_keys={
            "VOLCENGINE_API_KEY": "video-or-default-key",
            "SEED_TTS_API_KEY": "seed-tts-key",
        },
    )
    override_provider = VolcengineSeedTTSProvider(override_settings, RuntimeSettings())
    assert override_provider.api_key == "seed-tts-key"

    print("seed_tts_apikey_smoke=ok")
    print("seed_tts_api_key_present=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
