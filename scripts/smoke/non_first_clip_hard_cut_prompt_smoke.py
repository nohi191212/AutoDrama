from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.core.schemas import ShotVideoInput
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.storyboard_asset_nodes import ShotManifestGenerationNode


def main() -> None:
    settings = load_settings(ROOT / "config.yaml.example")
    node = ShotManifestGenerationNode(
        workflow=SimpleNamespace(prompts=PromptStore()),
        repo=SimpleNamespace(settings=settings),
        layout=None,
        router=None,
        script_service=None,
        asset_service=None,
        script_contents=None,
        prop_designs=None,
        media_store=None,
        logger=None,
    )
    inputs = [
        ShotVideoInput(
            slot="image_1",
            asset_type="clip_start_frame",
            asset_id="episode_001_clip_001_end_frame",
            asset_path="assets/images/storyboard_keyframes/episode_001_clip_001_end_frame.png",
            source_node="storyboard_keyframe_generation",
            label="previous end",
            order=1,
        ),
        ShotVideoInput(
            slot="image_2",
            asset_type="clip_end_frame",
            asset_id="episode_001_clip_002_end_frame",
            asset_path="assets/images/storyboard_keyframes/episode_001_clip_002_end_frame.png",
            source_node="storyboard_keyframe_generation",
            label="current end",
            order=2,
        ),
        ShotVideoInput(
            slot="image_3",
            asset_type="storyboard",
            asset_id="episode_001_clip_002_storyboard",
            asset_path="assets/images/storyboards/episode_001_clip_002_storyboard.png",
            source_node="storyboard_generation",
            label="storyboard",
            order=3,
        ),
    ]
    prompt, template = node._render_shot_video_prompt_template(
        provider=SimpleNamespace(name="volcengine", model="doubao-seedance-2-0-260128"),
        episode_key="episode_001",
        shot_id="episode_001_clip_002",
        duration_seconds=10,
        video_prompt=(
            "Camera Shot 1（0-4秒）：从 P01 的门口反应切开始。"
            "十二宫格面板规划 P01 P02 P03 P04 P05 P06 P07 P08 P09 P10 P11 P12。"
        ),
        shot_video_inputs=inputs,
        is_first_clip=False,
        start_frame_source_clip_id="episode_001_clip_001",
        end_frame_source_clip_id="episode_001_clip_002",
    )
    if template != "shot_video/volcengine":
        raise AssertionError(f"unexpected prompt template: {template}")
    required = ["上一条 clip", "必须从 image_1 开始", "立刻硬切", "P01", "不要把上一尾帧丝滑变形"]
    missing = [text for text in required if text not in prompt]
    if missing:
        raise AssertionError(f"hard cut prompt is missing: {missing}")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "non_first_clip_hard_cut_prompt_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("non_first_clip_hard_cut_prompt_smoke: ok")


if __name__ == "__main__":
    main()
