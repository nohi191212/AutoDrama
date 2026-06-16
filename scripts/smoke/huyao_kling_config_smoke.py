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
        settings.nodes["role_subject_video_generation"].params.get("role_subject_video_generation_concurrency") == 5,
        "role_subject_video_generation_concurrency must be 5",
    )
    require(
        settings.nodes["shot_video_generation"].params.get("shot_video_generation_concurrency") == 5,
        "shot_video_generation_concurrency must be 5",
    )
    require(
        settings.providers["kling"].options.get("video_reference_mode") == "subject_storyboard_key_vision",
        "Kling video_reference_mode legacy name should remain configured for compatibility",
    )
    require(
        settings.nodes["shot_video_generation"].params.get("max_reference_images") == 3,
        "shot_video_generation must allow storyboard + layout + roleboard image refs",
    )
    require(
        "max_reference_elements" not in settings.nodes["shot_video_generation"].params,
        "shot_video_generation must not be configured to pass element refs",
    )
    require(
        settings.providers["kling"].options.get("max_reference_images") == 3,
        "Kling shot video provider defaults must allow three image refs",
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
