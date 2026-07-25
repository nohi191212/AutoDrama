"""Validate every shipped config exposes only the shot-video binding."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402


CONFIGS = (
    "config.yaml.example",
    "config.chonghui_jiuba.yaml",
    "config.saodi_bashinian.yaml",
    "config.yushou_xianchao.yaml",
)
REMOVED = {
    "clip_prompt",
    "clip_storyboard_prompt",
    "clip_storyboard_prompt_audit",
    "clip_storyboard_image_generation",
    "clip_storyboard_keyframe_generation",
    "clip_manifest_generation",
    "clip_video_generation",
}


def main() -> None:
    for name in CONFIGS:
        path = ROOT / name
        if "storyboard" in path.read_text(encoding="utf-8").lower():
            raise AssertionError(f"{name} still exposes legacy storyboard configuration")
        settings = load_settings(path)
        leaked = sorted(REMOVED.intersection(settings.nodes))
        if leaked:
            raise AssertionError(f"{name} exposes removed node binding(s): {leaked}")
        if "shot_video_generation" not in settings.nodes:
            raise AssertionError(f"{name} is missing nodes.shot_video_generation")
        if "shot_video_generation_concurrency" not in settings.nodes["shot_video_generation"].params:
            raise AssertionError(f"{name} lacks shot video concurrency configuration")
    print("shot_config_contract_smoke: ok")


if __name__ == "__main__":
    main()
