from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402


def main() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml")
    provider_keys = {
        name: bool(provider.secret("api_key_env"))
        for name, provider in sorted(settings.providers.items())
        if provider.api_key_env
    }
    print("apikeys_file_loaded=true" if settings.api_keys else "apikeys_file_loaded=false")
    for name, present in provider_keys.items():
        print(f"{name}_api_key_present={present}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
