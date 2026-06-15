from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def unwrap_provider(provider):
    return getattr(provider, "_provider", provider)


def main() -> int:
    settings = load_settings(ROOT_DIR / "huyao.yaml")

    require("kling" in settings.providers, "providers.kling missing")
    require(settings.provider_for("video", "shot") == "kling", "routing.video.shot must be kling")
    require(
        settings.nodes["role_subject_video_generation"].model == "kling:kling-v3-omni",
        "role_subject_video_generation must bind to kling-v3-omni",
    )
    require(
        settings.nodes["role_subject_element_generation"].model == "kling:kling-v3-omni",
        "role_subject_element_generation must bind to kling-v3-omni",
    )
    require(
        settings.nodes["shot_video_generation"].model == "kling:kling-v3-omni",
        "shot_video_generation must bind to kling-v3-omni",
    )
    require(
        settings.providers["kling"].options.get("video_reference_mode") == "subject_storyboard_key_vision",
        "Kling video_reference_mode must use subject/storyboard/key-vision refs",
    )

    router = ProviderRouter(settings)
    provider = router.video("shot", node_name="role_subject_video_generation")
    require(
        isinstance(unwrap_provider(provider), KlingOmniVideoProvider),
        f"unexpected subject video provider: {type(provider)}",
    )

    provider = router.video("shot", node_name="shot_video_generation")
    require(
        isinstance(unwrap_provider(provider), KlingOmniVideoProvider),
        f"unexpected shot video provider: {type(provider)}",
    )

    print("huyao_kling_config_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
