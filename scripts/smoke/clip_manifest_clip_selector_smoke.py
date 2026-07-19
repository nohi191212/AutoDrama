from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import StoryboardEpisodeOutput, StoryboardPromptClip, StoryboardPromptEpisode, StoryboardShot
from autodrama.workflows.pregen import CLIP_SCOPED_PREGEN_ONLY_NODES
from autodrama.workflows.nodes.storyboard_asset_nodes import ClipManifestGenerationNode
from autodrama.workflows.selection import clip_matches_selectors


def _prompt_clip(index: int) -> StoryboardPromptClip:
    return StoryboardPromptClip(
        clip_id=f"episode_001_clip_{index:03d}",
        duration_seconds=10,
        video_prompt=f"clip {index}",
    )


def _shot(index: int, marker: str) -> StoryboardShot:
    return StoryboardShot(
        clip_id=f"episode_001_clip_{index:03d}",
        index=index,
        title=f"Clip {index:03d}",
        duration_seconds=10,
        video_prompt=marker,
        video_task_id=f"task-{index}",
    )


def main() -> None:
    if "clip_manifest_generation" not in CLIP_SCOPED_PREGEN_ONLY_NODES:
        raise AssertionError("pregen --clips does not allow clip_manifest_generation")

    storyboard = StoryboardPromptEpisode(
        episode_key="episode_001",
        clips=[_prompt_clip(index) for index in range(1, 5)],
    )
    selected_ids = {
        clip.clip_id
        for index, clip in enumerate(storyboard.clips, start=1)
        if clip_matches_selectors(storyboard.episode_key, clip.clip_id, index, {"2"})
    }
    existing = StoryboardEpisodeOutput(
        episode_key=storyboard.episode_key,
        clips=[_shot(index, f"old-{index}") for index in (1, 2, 4)],
    )
    rebuild_ids = ClipManifestGenerationNode._manifest_rebuild_clip_ids(storyboard, selected_ids, existing)
    expected_rebuild_ids = {
        "episode_001_clip_002",
        "episode_001_clip_003",
    }
    if rebuild_ids != expected_rebuild_ids:
        raise AssertionError(f"unexpected manifest rebuild ids: {sorted(rebuild_ids)}")

    rebuilt = StoryboardEpisodeOutput(
        episode_key=storyboard.episode_key,
        clips=[_shot(2, "new-2"), _shot(3, "new-3")],
    )
    merged = ClipManifestGenerationNode._merge_partial_episode_manifest(
        storyboard=storyboard,
        existing_episode=existing,
        rebuilt_episode=rebuilt,
    )
    if [clip.clip_id for clip in merged.clips] != [clip.clip_id for clip in storyboard.clips]:
        raise AssertionError("partial manifest merge did not preserve storyboard clip order")
    if [clip.video_prompt for clip in merged.clips] != ["old-1", "new-2", "new-3", "old-4"]:
        raise AssertionError("partial manifest merge replaced an unselected existing clip")
    if merged.clips[0].video_task_id != "task-1" or merged.clips[3].video_task_id != "task-4":
        raise AssertionError("partial manifest merge lost existing dynamic fields")

    fresh_rebuild_ids = ClipManifestGenerationNode._manifest_rebuild_clip_ids(storyboard, selected_ids, None)
    if fresh_rebuild_ids != {clip.clip_id for clip in storyboard.clips}:
        raise AssertionError("partial run without an existing manifest must build a complete episode")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "clip_manifest_clip_selector_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("clip_manifest_clip_selector_smoke: ok")


if __name__ == "__main__":
    main()
