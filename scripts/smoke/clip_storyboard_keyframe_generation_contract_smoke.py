from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (
    StoryboardKeyframeGenerationItem,
    StoryboardKeyframeGenerationOutput,
    StoryboardSheetGenerationItem,
)
from autodrama.workflows.nodes.storyboard_asset_nodes import (
    ClipManifestGenerationNode,
    StoryboardKeyframeGenerationNode,
)


def _keyframe(clip_id: str, frame_role: str, panel_ref: str) -> StoryboardKeyframeGenerationItem:
    return StoryboardKeyframeGenerationItem(
        episode_key="episode_001",
        clip_id=clip_id,
        frame_role=frame_role,
        panel_ref=panel_ref,
        source_storyboard_asset_id=f"{clip_id}_storyboard",
        prompt=f"{clip_id} {frame_role}",
        asset_id=StoryboardKeyframeGenerationNode.keyframe_asset_id(clip_id, frame_role),
        asset_path=f"assets/images/storyboard_keyframes/{clip_id}_{frame_role}_frame.png",
        provider="fake",
        model="fake-image",
    )


def main() -> None:
    output = StoryboardKeyframeGenerationOutput(
        generated_keyframes=[
            _keyframe("episode_001_clip_001", "start", "P01"),
            _keyframe("episode_001_clip_001", "end", "P12"),
            _keyframe("episode_001_clip_002", "end", "P12"),
        ]
    )
    by_key = ClipManifestGenerationNode._keyframes_by_clip_role(output)
    if ("episode_001", "episode_001_clip_001", "start") not in by_key:
        raise AssertionError("first clip start frame is missing")
    if ("episode_001", "episode_001_clip_002", "start") in by_key:
        raise AssertionError("non-first clip should not generate its own start frame")
    ClipManifestGenerationNode._require_keyframe(
        by_key,
        episode_key="episode_001",
        clip_id="episode_001_clip_001",
        frame_role="end",
    )
    ClipManifestGenerationNode._require_keyframe(
        by_key,
        episode_key="episode_001",
        clip_id="episode_001_clip_002",
        frame_role="end",
    )

    node = ClipManifestGenerationNode.__new__(ClipManifestGenerationNode)
    inputs, warnings = node._clip_video_inputs_for_shot(
        state=SimpleNamespace(roles={}, layouts={}, props={}),
        storyboard_sheet=StoryboardSheetGenerationItem(
            episode_key="episode_001",
            clip_id="episode_001_clip_002",
            asset_id="episode_001_clip_002_storyboard",
            prompt="storyboard",
            asset_path="assets/images/storyboards/episode_001_clip_002_storyboard.png",
            provider="fake",
            model="fake-image",
        ),
        role_ids=[],
        role_appearance_ids=[],
        layout_ids=[],
        prop_ids=[],
    )
    if warnings:
        raise AssertionError(f"unexpected warnings: {warnings}")
    if [item.asset_type for item in inputs] != ["storyboard"]:
        raise AssertionError("manual keyframes must not enter shot video inputs")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "clip_storyboard_keyframe_generation_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("clip_storyboard_keyframe_generation_contract_smoke: ok")


if __name__ == "__main__":
    main()
