from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.workflows.generation import (  # noqa: E402
    DEFAULT_GENERATION_NODES,
    _enabled_automatic_generation_nodes,
)


def main() -> int:
    without_audit = {
        "shot_dialogue_audio_generation",
        "shot_video_generation",
        "dynamic_asset_solidification",
    }
    selected = _enabled_automatic_generation_nodes(
        DEFAULT_GENERATION_NODES,
        available_node_names=without_audit,
    )
    assert selected == [
        "shot_dialogue_audio_generation",
        "shot_video_generation",
        "dynamic_asset_solidification",
    ]

    with_audit = {*without_audit, "shot_video_audit"}
    assert _enabled_automatic_generation_nodes(
        DEFAULT_GENERATION_NODES,
        available_node_names=with_audit,
    ) == DEFAULT_GENERATION_NODES
    print("generation node selection smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
