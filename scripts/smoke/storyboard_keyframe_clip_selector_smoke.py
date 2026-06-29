from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import StoryboardPromptClip  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardKeyframeGenerationNode  # noqa: E402
from autodrama.workflows.selection import clip_matches_selectors, parse_clip_selectors  # noqa: E402


def _clip(clip_id: str) -> StoryboardPromptClip:
    return StoryboardPromptClip(
        clip_id=clip_id,
        duration_seconds=12,
        role_ids=[],
        layout_ids=["layout_lab"],
        prop_ids=[],
        camera_shots=[],
        panel_plan={"P01": "start", "P12": "end"},
        video_prompt="video prompt",
    )


def main() -> None:
    parsed = parse_clip_selectors("2,5-7,episode_001_clip_009")
    if parsed != ["2", "5", "6", "7", "episode_001_clip_009"]:
        raise AssertionError(f"unexpected parsed clip selectors: {parsed}")

    if not clip_matches_selectors("episode_001", "episode_001_clip_002", 2, {"2"}):
        raise AssertionError("numeric selector should match clip index")
    if not clip_matches_selectors("episode_001", "episode_001_clip_002", 2, {"clip_002"}):
        raise AssertionError("clip_NNN selector should match clip index")
    if not clip_matches_selectors("episode_001", "episode_001_clip_002", 2, {"episode_001_clip_002"}):
        raise AssertionError("full clip id selector should match")
    if clip_matches_selectors("episode_001", "episode_001_clip_003", 3, {"episode_001_clip_002"}):
        raise AssertionError("full clip id selector should not match a different clip")

    node = object.__new__(StoryboardKeyframeGenerationNode)
    node.workflow = SimpleNamespace(_active_clip_selectors={"2", "clip_004"})
    selectors = node._active_clip_selectors()
    if selectors != {"2", "clip_004"}:
        raise AssertionError(f"unexpected active selectors: {selectors}")
    if not node._clip_matches_active_selectors(
        episode_key="episode_001",
        clip=_clip("episode_001_clip_002"),
        clip_index=2,
        selectors=selectors,
    ):
        raise AssertionError("node selector should match selected clip")
    if node._clip_matches_active_selectors(
        episode_key="episode_001",
        clip=_clip("episode_001_clip_003"),
        clip_index=3,
        selectors=selectors,
    ):
        raise AssertionError("node selector should skip unselected clip")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "storyboard_keyframe_clip_selector_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("storyboard_keyframe_clip_selector_smoke: ok")


if __name__ == "__main__":
    main()
