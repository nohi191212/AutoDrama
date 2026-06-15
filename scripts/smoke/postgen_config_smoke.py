from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.google.text.gemini import GeminiTextProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def unwrap_provider(provider):
    return getattr(provider, "_provider", provider)


def check_config(config_name: str) -> None:
    settings = load_settings(ROOT_DIR / config_name)
    require("google" in settings.providers, f"{config_name}: providers.google missing")
    require(settings.provider_for("text", "postgen_edit_plan") == "google", f"{config_name}: postgen routing missing")
    require(
        settings.nodes["postgen_edit_plan_generation"].model == "google:gemini-3.5-flash",
        f"{config_name}: postgen node model mismatch",
    )
    provider = ProviderRouter(settings).text("postgen_edit_plan", node_name="postgen_edit_plan_generation")
    require(isinstance(unwrap_provider(provider), GeminiTextProvider), f"{config_name}: provider is not Gemini")


def main() -> int:
    check_config("config.yaml.example")
    check_config("huyao.yaml")
    print("postgen_config_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
