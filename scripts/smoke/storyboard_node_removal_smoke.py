from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import StoryboardShot
from autodrama.workflows.nodes import AVAILABLE_PREGEN_NODE_NAMES, PREGEN_NODE_NAMES, STORYBOARD_ASSET_NODE_NAMES


EXPECTED_STORYBOARD_NODES = ["storyboard_prompt", "storyboard_generation", "shot_manifest_generation"]


def main() -> None:
    if STORYBOARD_ASSET_NODE_NAMES != EXPECTED_STORYBOARD_NODES:
        raise AssertionError(f"unexpected storyboard node chain: {STORYBOARD_ASSET_NODE_NAMES!r}")
    for node_name in STORYBOARD_ASSET_NODE_NAMES:
        if node_name not in PREGEN_NODE_NAMES or node_name not in AVAILABLE_PREGEN_NODE_NAMES:
            raise AssertionError(f"storyboard node is not available through pregen: {node_name}")

    payload = {
        "shot_id": "episode_001_shot_001",
        "index": 1,
        "layout_id": "layout_office",
        "title": "test",
        "duration_seconds": 5.0,
        "video_prompt": "test video prompt",
        "storyboard_asset_id": "storyboard_id",
        "storyboard_asset_path": "assets/images/storyboards/storyboard.png",
    }
    shot = StoryboardShot.model_validate(payload)
    if shot.storyboard_asset_id != "storyboard_id":
        raise AssertionError("storyboard asset id was not loaded")
    if shot.storyboard_asset_path != "assets/images/storyboards/storyboard.png":
        raise AssertionError("storyboard asset path was not loaded")

    dumped = shot.model_dump()
    if "storyboard_asset_id" not in dumped or "storyboard_asset_path" not in dumped:
        raise AssertionError("storyboard asset fields were not serialized")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "storyboard_node_removal_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("storyboard_node_removal_smoke: ok")


if __name__ == "__main__":
    main()
