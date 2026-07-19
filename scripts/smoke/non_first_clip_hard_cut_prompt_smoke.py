from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.core.schemas import ClipVideoInput
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.storyboard_asset_nodes import ClipManifestGenerationNode


def main() -> None:
    settings = load_settings(ROOT / "config.yaml.example")
    node = ClipManifestGenerationNode(
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
        ClipVideoInput(
            slot="image_1",
            asset_type="storyboard",
            asset_id="episode_001_clip_002_storyboard",
            asset_path="assets/images/storyboards/episode_001_clip_002_storyboard.png",
            source_node="clip_storyboard_image_generation",
            label="storyboard",
            order=1,
        ),
        ClipVideoInput(
            slot="image_2",
            asset_type="layout",
            asset_id="layout_demo",
            asset_path="assets/images/layouts/layout_demo.png",
            source_node="layout_image_generation",
            label="layout",
            order=2,
        ),
    ]
    prompt, template = node._render_clip_video_prompt_template(
        provider=SimpleNamespace(name="volcengine", model="doubao-seedance-2-0-260128"),
        episode_key="episode_001",
        shot_id="episode_001_clip_002",
        duration_seconds=10,
        video_prompt=(
            "Camera Shot 1（0-4秒）：从 P01 的门口反应切开始。"
            "十二宫格面板规划 P01 P02 P03 P04 P05 P06 P07 P08 P09 P10 P11 P12。"
        ),
        clip_video_inputs=inputs,
    )
    if template != "clip_video/volcengine":
        raise AssertionError(f"unexpected prompt template: {template}")
    required = ["每个 clip", "后期剪辑"]
    missing = [text for text in required if text not in prompt]
    if missing:
        raise AssertionError(f"frame-free prompt is missing: {missing}")
    forbidden = ["必须从 image_1 开始", "上一条 clip", "最终收束到", "首尾帧优先级"]
    leaked = [text for text in forbidden if text in prompt]
    if leaked:
        raise AssertionError(f"frame-anchor prompt leaked: {leaked}")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "non_first_clip_hard_cut_prompt_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("frame-free clip prompt smoke: ok")


if __name__ == "__main__":
    main()
