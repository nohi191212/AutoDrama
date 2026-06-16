from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    for config_path in (Path("config.yaml.example"), Path("huyao.yaml")):
        settings = load_settings(config_path)
        require("roleboard_prompt" in settings.nodes, f"{config_path} missing nodes.roleboard_prompt")
        require("roleboard_generation" in settings.nodes, f"{config_path} missing nodes.roleboard_generation")
        require("storyboard_prompt" in settings.nodes, f"{config_path} missing nodes.storyboard_prompt")
        require(
            "storyboard_sheet_generation" in settings.nodes,
            f"{config_path} missing nodes.storyboard_sheet_generation",
        )
        require("role_voice_select" in settings.nodes, f"{config_path} missing nodes.role_voice_select")
        require("role_voice_select_audio_judge" in settings.nodes, f"{config_path} missing audio judge node")
        require("role" in settings.routing.get("image", {}), f"{config_path} missing image.role route")
        require(
            settings.routing.get("text", {}).get("role_voice_select"),
            f"{config_path} missing routing.text.role_voice_select",
        )
        require(
            settings.routing.get("judge", {}).get("role_voice_select"),
            f"{config_path} missing routing.judge.role_voice_select",
        )
    print("config_roleboard_load_smoke=ok")


if __name__ == "__main__":
    main()
