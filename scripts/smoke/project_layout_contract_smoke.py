from __future__ import annotations

import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke" / "project_layout_contract"
    repo = ProjectRepository(settings)
    layout = repo.layout
    project_dir = settings.output.root_dir / "project"

    expected_paths = {
        "node_output": "assets/json/nodes/script_outline.json",
        "dynamic_assets_index": "assets/json/assets/dynamic_assets.json",
        "script_outline": "assets/json/scripts/outlines/episode_001.json",
        "script_novel_full": "assets/json/scripts/novel_full/episode_001.json",
        "script_novel_extract": "assets/json/scripts/novel_extract/episode_001.json",
        "script_legacy_episode": "assets/json/scripts/episode_001.json",
        "script_legacy_full": "assets/json/scripts/novel/episode_001.json",
        "role_design": "assets/json/roles/role_hero.json",
        "prop_design": "assets/json/props/prop_key.json",
        "shot": "shots/episode_001.json",
        "role_image": "assets/images/roles/role_hero.png",
        "prop_image": "assets/images/props/prop_key.png",
        "layout_image": "assets/images/layouts/layout_room.png",
        "bgm": "assets/audios/bgms/bgm_theme.mp3",
        "shot_dialogue": "assets/audios/shot_dialogues/shot_001.wav",
        "role_video": "assets/videos/roles/role_hero.mp4",
        "shot_video": "assets/videos/shots/shot_001.mp4",
    }
    actual_paths = {
        "node_output": layout.node_output_path(project_dir, "script_outline"),
        "dynamic_assets_index": layout.dynamic_assets_index_path(project_dir),
        "script_outline": layout.script_content_path(project_dir, "outlines", "episode_001"),
        "script_novel_full": layout.script_content_path(project_dir, "novel_full", "episode_001"),
        "script_novel_extract": layout.script_content_path(project_dir, "novel_extract", "episode_001"),
        "script_legacy_episode": layout.script_novel_legacy_episode_path(project_dir, "episode_001"),
        "script_legacy_full": layout.script_novel_legacy_full_path(project_dir, "episode_001"),
        "role_design": layout.role_design_path(project_dir, "role_hero"),
        "prop_design": layout.prop_design_path(project_dir, "prop_key"),
        "shot": layout.shot_path(project_dir, "episode_001"),
        "role_image": layout.image_asset_path(project_dir, "roles", "role_hero"),
        "prop_image": layout.image_asset_path(project_dir, "props", "prop_key"),
        "layout_image": layout.image_asset_path(project_dir, "layouts", "layout_room"),
        "bgm": layout.music_asset_path(project_dir, "bgm_theme", "mp3"),
        "shot_dialogue": layout.audio_asset_path(project_dir, "shot_dialogues", "shot_001", "wav"),
        "role_video": layout.video_asset_path(project_dir, "roles", "role_hero"),
        "shot_video": layout.video_asset_path(project_dir, "shots", "shot_001"),
    }
    for key, expected in expected_paths.items():
        require(
            layout.project_relative(project_dir, actual_paths[key]) == expected,
            f"{key} path drifted: {actual_paths[key]}",
        )

    print("project_layout_contract_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
