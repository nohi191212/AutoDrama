"""Ensure the persisted shot manifest cannot accept legacy storyboard JSON."""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ShotManifestEpisodeOutput, ShotManifestItem, ShotVideoInput  # noqa: E402


def _must_reject(factory, payload: dict[str, object], label: str) -> None:
    try:
        factory.model_validate(payload)
    except ValidationError:
        return
    raise AssertionError(f"{label} unexpectedly accepted legacy data")


def main() -> None:
    shot = ShotManifestItem(
        shot_id="episode_001_clip_001_shot_001",
        clip_id="episode_001_clip_001",
        index=1,
        title="test",
        duration_seconds=5.0,
        video_prompt="test video prompt",
        video_inputs=[
            ShotVideoInput(
                slot="image_1",
                asset_type="shot_keyframe",
                source_node="shot_keyframe_image_generation",
                order=0,
            )
        ],
    )
    dumped = shot.model_dump()
    if "shot_id" not in dumped or "video_inputs" not in dumped:
        raise AssertionError("shot-first manifest fields were not serialized")
    if any(key in dumped for key in ("storyboard_asset_id", "storyboard_asset_path", "clip_video_inputs")):
        raise AssertionError("legacy storyboard fields were serialized")

    _must_reject(
        ShotManifestItem,
        {
            "clip_id": "episode_001_clip_001",
            "index": 1,
            "title": "test",
            "duration_seconds": 5.0,
            "video_prompt": "test",
        },
        "legacy clip identity",
    )
    _must_reject(
        ShotManifestEpisodeOutput,
        {
            "episode_key": "episode_001",
            "manifest_kind": "shot",
            "clips": [],
        },
        "legacy episode manifest",
    )
    _must_reject(
        ShotManifestItem,
        {
            "shot_id": "episode_001_clip_001_shot_001",
            "index": 1,
            "title": "test",
            "duration_seconds": 5.0,
            "video_prompt": "test",
            "storyboard_asset_path": "assets/images/old.png",
        },
        "legacy storyboard asset",
    )
    print("shot_manifest_schema_contract_smoke: ok")


if __name__ == "__main__":
    main()
