from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.core.schemas import ShotVideoInput, StoryboardShot
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.storyboard_asset_nodes import ShotManifestGenerationNode


def main() -> None:
    settings = load_settings(ROOT / "config.yaml.example")
    shot_video_params = settings.nodes["shot_video_generation"].params
    if shot_video_params.get("prompt_template") != "shot_video/volcengine":
        raise AssertionError("shot_video_generation prompt_template is not model-bound")
    if not shot_video_params.get("negative_rules"):
        raise AssertionError("shot_video_generation negative_rules is missing")

    inputs = [
        ShotVideoInput(
            slot="image_1",
            asset_type="clip_start_frame",
            asset_id="episode_001_clip_001_start_frame",
            asset_path="assets/images/storyboard_keyframes/episode_001_clip_001_start_frame.png",
            source_node="storyboard_keyframe_generation",
            label="start frame",
            order=1,
        ),
        ShotVideoInput(
            slot="image_2",
            asset_type="clip_end_frame",
            asset_id="episode_001_clip_001_end_frame",
            asset_path="assets/images/storyboard_keyframes/episode_001_clip_001_end_frame.png",
            source_node="storyboard_keyframe_generation",
            label="end frame",
            order=2,
        ),
        ShotVideoInput(
            slot="image_3",
            asset_type="storyboard",
            asset_id="episode_001_clip_001_storyboard",
            asset_path="assets/images/storyboards/episode_001_clip_001_storyboard.png",
            source_node="storyboard_generation",
            label="storyboard",
            order=3,
        ),
        ShotVideoInput(
            slot="image_4",
            asset_type="roleboard",
            asset_id="role_linzhou_base",
            asset_path="assets/images/roles/role_linzhou_base.png",
            source_node="roleboard_generation",
            label="roleboard",
            role_id="role_linzhou",
            order=4,
        ),
        ShotVideoInput(
            slot="image_5",
            asset_type="layout",
            asset_id="layout_office",
            asset_path="assets/images/layouts/layout_office.png",
            source_node="layout_image_generation",
            label="layout",
            layout_id="layout_office",
            order=5,
        ),
    ]
    final_prompt = PromptStore().render(
        "shot_video/volcengine",
        episode_key="episode_001",
        clip_id="episode_001_clip_001",
        shot_id="episode_001_clip_001",
        duration_seconds=10,
        video_prompt="Camera Shot 1（0-4秒）：角色进入办公室。十二宫格面板规划 P01-P03 对应该镜头。",
        shot_video_inputs_json="[{}]",
        clip_start_frame_slot="image_1",
        clip_end_frame_slot="image_2",
        storyboard_input_slot="image_3",
        roleboard_input_slots="image_4",
        layout_input_slots="image_5",
        prop_input_slots="无",
        is_first_clip="true",
        requires_initial_hard_cut="false",
        start_frame_source_clip_id="episode_001_clip_001",
        end_frame_source_clip_id="episode_001_clip_001",
        clip_continuity_instructions="image_1 是当前 clip 的首帧；image_2 是当前 clip 的尾帧。",
        negative_rules="- 不要生成字幕。",
        provider_name="volcengine",
        model_name="doubao-seedance-2-0-260128",
    )
    shot = StoryboardShot(
        clip_id="episode_001_clip_001",
        index=1,
        layout_id="layout_office",
        layout_ids=["layout_office"],
        title="Clip 001",
        duration_seconds=10,
        video_prompt="Camera Shot 1（0-4秒）：角色进入办公室。十二宫格面板规划 P01-P03 对应该镜头。",
        final_video_prompt=final_prompt,
        shot_video_inputs=inputs,
        start_frame_asset_id="episode_001_clip_001_start_frame",
        start_frame_source_clip_id="episode_001_clip_001",
        end_frame_asset_id="episode_001_clip_001_end_frame",
        is_first_clip=True,
    )
    dumped = shot.model_dump(mode="json")
    if dumped.get("clip_id") != "episode_001_clip_001":
        raise AssertionError("clip_id should be serialized")
    legacy = StoryboardShot.model_validate(
        {
            "shot_id": "episode_001_shot_legacy",
            "index": 1,
            "title": "legacy",
            "duration_seconds": 10,
            "video_prompt": "legacy",
        }
    )
    if legacy.clip_id != "episode_001_shot_legacy" or legacy.shot_id != "episode_001_shot_legacy":
        raise AssertionError("legacy shot_id should map to clip_id")
    if "per_second_content" in dumped:
        raise AssertionError("per_second_content should not be serialized")
    expected_order = ["clip_start_frame", "clip_end_frame", "storyboard"]
    if [item.get("asset_type") for item in dumped.get("shot_video_inputs", [])[:3]] != expected_order:
        raise AssertionError("shot video inputs must start with start frame, end frame, storyboard")
    if not dumped.get("final_video_prompt"):
        raise AssertionError("final_video_prompt is missing")

    limiter_node = ShotManifestGenerationNode(
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
    limited_inputs, limit_warnings = limiter_node._limit_shot_video_inputs_for_provider(
        inputs=inputs,
        provider=SimpleNamespace(max_reference_images=3),
        clip_id="episode_001_clip_001",
    )
    if [item.asset_type for item in limited_inputs] != expected_order:
        raise AssertionError("provider input limit should preserve start/end/storyboard first")
    if not limit_warnings or "omitted image_4:roleboard" not in limit_warnings[0]:
        raise AssertionError("provider input limit warning should list omitted lower-priority refs")

    settings_without_negative_rules = load_settings(ROOT / "config.yaml.example")
    params_without_negative_rules = dict(settings_without_negative_rules.nodes["shot_video_generation"].params)
    params_without_negative_rules.pop("negative_rules", None)
    settings_without_negative_rules.nodes["shot_video_generation"].params = params_without_negative_rules
    node = ShotManifestGenerationNode(
        workflow=SimpleNamespace(prompts=PromptStore()),
        repo=SimpleNamespace(settings=settings_without_negative_rules),
        layout=None,
        router=None,
        script_service=None,
        asset_service=None,
        script_contents=None,
        prop_designs=None,
        media_store=None,
        logger=None,
    )
    try:
        node._render_shot_video_prompt_template(
            provider=SimpleNamespace(name="volcengine", model="doubao-seedance-2-0-260128"),
            episode_key="episode_001",
            shot_id="episode_001_clip_001",
            duration_seconds=10,
            video_prompt="Camera Shot 1（0-4秒）：角色进入办公室。",
            shot_video_inputs=inputs,
            is_first_clip=False,
            start_frame_source_clip_id="episode_001_clip_000",
            end_frame_source_clip_id="episode_001_clip_001",
        )
    except ValueError as exc:
        if "nodes.shot_video_generation.params.negative_rules" not in str(exc):
            raise AssertionError(f"negative_rules error message is unclear: {exc}") from exc
    else:
        raise AssertionError("missing negative_rules should fail instead of using a code fallback")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "shot_video_manifest_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("shot_video_manifest_contract_smoke: ok")


if __name__ == "__main__":
    main()
