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
            asset_type="storyboard",
            asset_id="episode_001_shot_001_storyboard",
            asset_path="assets/images/storyboards/episode_001_shot_001_storyboard.png",
            source_node="storyboard_generation",
            label="storyboard",
            order=1,
        ),
        ShotVideoInput(
            slot="image_2",
            asset_type="roleboard",
            asset_id="role_linzhou_base",
            asset_path="assets/images/roles/role_linzhou_base.png",
            source_node="roleboard_generation",
            label="roleboard",
            role_id="role_linzhou",
            order=2,
        ),
        ShotVideoInput(
            slot="image_3",
            asset_type="layout",
            asset_id="layout_office",
            asset_path="assets/images/layouts/layout_office.png",
            source_node="layout_image_generation",
            label="layout",
            layout_id="layout_office",
            order=3,
        ),
    ]
    final_prompt = PromptStore().render(
        "shot_video/volcengine",
        episode_key="episode_001",
        shot_id="episode_001_shot_001",
        duration_seconds=10,
        video_prompt="0-1秒：角色进入办公室。1-2秒：镜头推近关键道具。",
        shot_video_inputs_json="[{}]",
        storyboard_input_slot="image_1",
        roleboard_input_slots="image_2",
        layout_input_slots="image_3",
        prop_input_slots="无",
        negative_rules="- 不要生成字幕。",
        provider_name="volcengine",
        model_name="doubao-seedance-2-0-260128",
    )
    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id="layout_office",
        layout_ids=["layout_office"],
        title="镜头001",
        duration_seconds=10,
        video_prompt="0-1秒：角色进入办公室。1-2秒：镜头推近关键道具。",
        final_video_prompt=final_prompt,
        shot_video_inputs=inputs,
    )
    dumped = shot.model_dump(mode="json")
    if "per_second_content" in dumped:
        raise AssertionError("per_second_content should not be serialized")
    if dumped.get("shot_video_inputs", [{}])[0].get("asset_type") != "storyboard":
        raise AssertionError("storyboard must be the first shot video input")
    if not dumped.get("final_video_prompt"):
        raise AssertionError("final_video_prompt is missing")

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
            shot_id="episode_001_shot_001",
            duration_seconds=10,
            video_prompt="0-1秒：角色进入办公室。",
            shot_video_inputs=inputs,
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
