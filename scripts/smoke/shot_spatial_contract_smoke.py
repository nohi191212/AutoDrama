from __future__ import annotations

import asyncio
import json
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any

from PIL import Image

from autodrama.config import load_settings
from autodrama.core.schemas import (
    ClipToShotsEpisodeOutput,
    LayoutToBackgroundPromptEpisodeOutput,
    SceneMultiviewImageGenerationEpisodeOutput,
    SceneMultiviewPlanEpisodeOutput,
    ShotBackgroundImageGenerationEpisodeOutput,
    ShotBlockingControlEpisodeOutput,
    ShotBlockingPlanEpisodeOutput,
    ShotKeyframeImageGenerationOutput,
    ShotKeyframePromptOutput,
    ShotKeyframeStageGenerationOutput,
)
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.pregen import PREGEN_NODES, PregenWorkflow


ROOT = Path(__file__).resolve().parents[2]
SMOKE_ROOT = ROOT / ".tmp" / "shot_spatial_contract_smoke"


def write_fixture_config() -> Path:
    if SMOKE_ROOT.exists():
        shutil.rmtree(SMOKE_ROOT)
    SMOKE_ROOT.mkdir(parents=True)
    chapters_dir = SMOKE_ROOT / "chapters"
    chapters_dir.mkdir()
    (chapters_dir / "chap0001_雨夜办公室.txt").write_text(
        "雨夜办公室里，林舟发现合同关键页被调包，苏晚递来旧邮件截图。",
        encoding="utf-8",
    )
    template_source = ROOT / ".assets" / "image_templates" / "roleboard_template.png"
    template_target = SMOKE_ROOT / ".assets" / "image_templates" / "roleboard_template.png"
    template_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(template_source, template_target)
    config_path = SMOKE_ROOT / "config.yaml"
    config_path.write_text(
        """project:
  id: shot_spatial_contract_smoke
  title: Shot Spatial Contract Smoke
  script_chapters_dir: ./chapters
  episode_count: 1
  episode_duration_seconds: 30
output:
  root_dir: ./outputs
  project_dir_template: "{date}_{slug}"
app:
  enable_llm_audit: false
  enable_image_audit: false
generation:
  expected_output_seconds: -1
  visual_style: xuanhuan-v1
routing:
  text:
    script: fake
    role: fake
    shot: fake
    layout: fake
    prop: fake
  image:
    image: fake
    roleboard: fake
    prop: fake
    layout: fake
    shot: fake
  audio:
    speech: fake
providers: {}
""",
        encoding="utf-8",
    )
    return config_path


def _episode_output(repo: ProjectRepository, project_dir: Path, node_name: str, schema: Any):
    path = repo.layout.node_episode_output_path(project_dir, node_name, "episode_001")
    return schema.model_validate_json(path.read_text(encoding="utf-8"))


async def run_fake_pipeline() -> dict[str, object]:
    settings = load_settings(write_fixture_config())
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config()
    router = ProviderRouter(settings, provider_override="fake")

    image_calls: list[dict[str, Any]] = []
    original_generate_image = router._fake_image.generate_image

    async def capture_generate_image(
        prompt: str,
        refs: list[Any] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        image_calls.append(
            {
                "node_name": str((metadata or {}).get("node_name") or ""),
                "asset_name": str((metadata or {}).get("prompt_asset_name") or ""),
                "ref_ids": [str(ref.id) for ref in refs or []],
                "prompt": prompt,
            }
        )
        return await original_generate_image(
            prompt,
            refs=refs,
            size=size,
            metadata=metadata,
        )

    router._fake_image.generate_image = capture_generate_image

    persisted_counts: dict[str, list[int]] = defaultdict(list)
    original_write_json = repo.write_json

    def capture_write_json(path: Path, payload: Any) -> None:
        if isinstance(payload, ShotBackgroundImageGenerationEpisodeOutput):
            persisted_counts["background"].append(len(payload.generated_backgrounds))
        elif isinstance(payload, ShotKeyframeStageGenerationOutput):
            persisted_counts["stage"].append(len(payload.generated_stages))
        elif isinstance(payload, ShotKeyframeImageGenerationOutput):
            persisted_counts["final"].append(len(payload.generated_keyframes))
        original_write_json(path, payload)

    repo.write_json = capture_write_json
    workflow = PregenWorkflow(repo=repo, router=router)
    state = repo.load_state(project_dir)
    for node_name in PREGEN_NODES:
        if node_name.endswith("_audit"):
            continue
        state = await workflow.run(project_dir, only=node_name)

    plan = _episode_output(repo, project_dir, "clip_to_shots", ClipToShotsEpisodeOutput)
    shots = [shot for clip in plan.clips for shot in clip.shots]
    assert len(shots) >= 2
    shot_by_id = {shot.shot_id: shot for shot in shots}
    scene_ids = {shot.scene_id for shot in shots}

    multiview_plan = _episode_output(
        repo, project_dir, "scene_multiview_plan", SceneMultiviewPlanEpisodeOutput
    )
    multiview = _episode_output(
        repo,
        project_dir,
        "scene_multiview_image_generation",
        SceneMultiviewImageGenerationEpisodeOutput,
    )
    assert {item.scene_id for item in multiview_plan.scenes} == scene_ids
    assert {item.scene_id for item in multiview.generated_scenes} == scene_ids
    for scene in multiview_plan.scenes:
        scene_shot_ids = {shot.shot_id for shot in shots if shot.scene_id == scene.scene_id}
        assert len(scene.views) == 4
        assert {view.view_index for view in scene.views} == {1, 2, 3, 4}
        assert {item.shot_id for item in scene.assignments} == scene_shot_ids
    for scene in multiview.generated_scenes:
        assert len(scene.views) == 4
        assert len({view.fingerprint for view in scene.views}) == 4
        assert (project_dir / scene.board_asset_path).is_file()
        assert all((project_dir / view.asset_path).is_file() for view in scene.views)

    background_prompts = _episode_output(
        repo,
        project_dir,
        "layout_to_background_prompt",
        LayoutToBackgroundPromptEpisodeOutput,
    )
    backgrounds = _episode_output(
        repo,
        project_dir,
        "shot_background_image_generation",
        ShotBackgroundImageGenerationEpisodeOutput,
    )
    blocking = _episode_output(
        repo, project_dir, "shot_blocking_plan", ShotBlockingPlanEpisodeOutput
    )
    controls = _episode_output(
        repo,
        project_dir,
        "shot_blocking_control_render",
        ShotBlockingControlEpisodeOutput,
    )
    keyframe_prompts = _episode_output(
        repo, project_dir, "shot_keyframe_prompt", ShotKeyframePromptOutput
    )
    stages = _episode_output(
        repo,
        project_dir,
        "shot_keyframe_stage_generation",
        ShotKeyframeStageGenerationOutput,
    )
    keyframes = _episode_output(
        repo,
        project_dir,
        "shot_keyframe_image_generation",
        ShotKeyframeImageGenerationOutput,
    )
    expected_shot_ids = set(shot_by_id)
    assert {item.shot_id for item in background_prompts.backgrounds} == expected_shot_ids
    assert {item.shot_id for item in backgrounds.generated_backgrounds} == expected_shot_ids
    assert {item.shot_id for item in blocking.shots} == expected_shot_ids
    assert {item.shot_id for item in controls.generated_controls} == expected_shot_ids
    assert {item.shot_id for item in keyframe_prompts.prompts} == expected_shot_ids
    assert {item.shot_id for item in stages.generated_stages} == expected_shot_ids
    assert {item.shot_id for item in keyframes.generated_keyframes} == expected_shot_ids

    background_by_shot = {item.shot_id: item for item in backgrounds.generated_backgrounds}
    control_by_shot = {item.shot_id: item for item in controls.generated_controls}
    blocking_by_shot = {item.shot_id: item for item in blocking.shots}
    prompt_by_shot = {item.shot_id: item for item in keyframe_prompts.prompts}
    stage_by_shot = {item.shot_id: item for item in stages.generated_stages}
    keyframe_by_shot = {item.shot_id: item for item in keyframes.generated_keyframes}
    multiview_by_scene = {item.scene_id: item for item in multiview.generated_scenes}

    for shot in shots:
        background = background_by_shot[shot.shot_id]
        control = control_by_shot[shot.shot_id]
        blocking_item = blocking_by_shot[shot.shot_id]
        prompt = prompt_by_shot[shot.shot_id]
        stage = stage_by_shot[shot.shot_id]
        keyframe = keyframe_by_shot[shot.shot_id]
        with Image.open(project_dir / background.asset_path) as background_image:
            background_size = background_image.size
        with Image.open(project_dir / control.asset_path) as control_image:
            assert control_image.size == background_size
        assert background.scene_multiview_fingerprint == multiview_by_scene[shot.scene_id].fingerprint
        assert [binding.ref_id for binding in prompt.bindings] == shot.ref_ids
        assert prompt.prompt_provenance["binding_map"] == {
            binding.binding_id: binding.ref_id for binding in blocking_item.bindings
        }
        assert stage.blocking_fingerprint == prompt.blocking_fingerprint == control.fingerprint
        assert keyframe.stage_fingerprint == stage.fingerprint
        assert keyframe.blocking_fingerprint == control.fingerprint

    relevant_calls = {
        (item["node_name"], item["asset_name"]): item
        for item in image_calls
        if item["node_name"]
        in {
            "scene_multiview_image_generation",
            "shot_background_image_generation",
            "shot_keyframe_stage_generation",
            "shot_keyframe_image_generation",
        }
    }
    background_prompt_by_shot = {item.shot_id: item for item in background_prompts.backgrounds}
    for shot in shots:
        background_prompt = background_prompt_by_shot[shot.shot_id]
        background = background_by_shot[shot.shot_id]
        prompt = prompt_by_shot[shot.shot_id]
        stage = stage_by_shot[shot.shot_id]
        assert relevant_calls[
            ("shot_background_image_generation", background.background_id)
        ]["ref_ids"] == [
            background_prompt.primary_view_id,
            shot.scene_id,
            *background_prompt.secondary_view_ids,
        ]
        assert relevant_calls[("shot_keyframe_stage_generation", shot.shot_id)][
            "ref_ids"
        ] == [
            background.background_id,
            f"{shot.shot_id}_blocking_control",
            *shot.ref_ids,
        ]
        assert relevant_calls[("shot_keyframe_image_generation", shot.shot_id)][
            "ref_ids"
        ] == [
            stage.stage_asset_id,
            background.background_id,
            *shot.ref_ids,
        ]
        assert prompt.stage_prompt.startswith("Image 1 = 必须锁定的干净背景")
        assert prompt.prompt.startswith("Image 1 = 粗排关键帧")
        assert "每个 binding 已经占据的空间槽位与身份对应关系" in prompt.prompt
        assert "不得交换不同主体的身份、前后景、左右位置" in prompt.prompt
        assert "主体轮廓、头顶、脚点和画框裁切关系必须重合" in prompt.prompt
        assert "不得把近景或半身改成中远景" in prompt.prompt
        assert "这些空间信息一律以 Image 1 为准" in prompt.prompt
        placements_by_binding = {
            placement.binding_id: placement for placement in blocking_item.placements
        }
        role_slots: list[str] = []
        for index, binding in enumerate(prompt.bindings, start=3):
            if binding.asset_kind != "roleboard":
                continue
            placement = placements_by_binding[binding.binding_id]
            slot = json.dumps(
                {
                    "center_x": round(placement.center_x, 4),
                    "ground_y": round(placement.ground_y, 4),
                    "width": round(placement.width, 4),
                    "height": round(placement.height, 4),
                    "depth_rank": placement.depth_rank,
                    "facing": placement.facing,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            assert f"Image {index} = {binding.binding_id}" in prompt.prompt
            assert f"仅允许原位精修 Image 1 中槽位 {slot}" in prompt.prompt
            role_slots.append(slot)
        assert len(role_slots) == len(set(role_slots))

    for key in ("background", "stage", "final"):
        assert set(range(1, len(shots) + 1)).issubset(persisted_counts[key]), (
            key,
            persisted_counts[key],
        )

    manifest = json.loads(repo.layout.shot_path(project_dir, "episode_001").read_text(encoding="utf-8"))
    assert {item["shot_id"] for item in manifest["shots"]} == expected_shot_ids
    for item in manifest["shots"]:
        fingerprints = item["input_fingerprints"]
        assert set(fingerprints) == {
            "keyframe",
            "background",
            "scene_multiview",
            "blocking",
            "keyframe_stage",
        }
        shot_id = item["shot_id"]
        assert fingerprints["scene_multiview"] == background_by_shot[
            shot_id
        ].scene_multiview_fingerprint
        assert fingerprints["blocking"] == control_by_shot[shot_id].fingerprint
        assert fingerprints["keyframe_stage"] == stage_by_shot[shot_id].fingerprint

    selected_shot = shots[0]
    same_scene_shot_ids = {
        shot.shot_id for shot in shots if shot.scene_id == selected_shot.scene_id
    }
    await workflow.run(
        project_dir,
        only="shot_blocking_plan",
        force=True,
        episode_keys=["episode_001"],
        shot_selectors=[selected_shot.shot_id],
    )
    scene_blocking = _episode_output(
        repo, project_dir, "shot_blocking_plan", ShotBlockingPlanEpisodeOutput
    )
    assert same_scene_shot_ids.issubset({item.shot_id for item in scene_blocking.shots})

    await workflow.run(
        project_dir,
        only="scene_multiview_image_generation",
        force=True,
        episode_keys=["episode_001"],
        shot_selectors=[selected_shot.shot_id],
    )
    invalidated_backgrounds = _episode_output(
        repo,
        project_dir,
        "shot_background_image_generation",
        ShotBackgroundImageGenerationEpisodeOutput,
    )
    invalidated_stages = _episode_output(
        repo,
        project_dir,
        "shot_keyframe_stage_generation",
        ShotKeyframeStageGenerationOutput,
    )
    invalidated_keyframes = _episode_output(
        repo,
        project_dir,
        "shot_keyframe_image_generation",
        ShotKeyframeImageGenerationOutput,
    )
    assert same_scene_shot_ids.isdisjoint(
        {item.shot_id for item in invalidated_backgrounds.generated_backgrounds}
    )
    assert same_scene_shot_ids.isdisjoint(
        {item.shot_id for item in invalidated_stages.generated_stages}
    )
    assert same_scene_shot_ids.isdisjoint(
        {item.shot_id for item in invalidated_keyframes.generated_keyframes}
    )

    return {
        "project_dir": str(project_dir),
        "scene_count": len(scene_ids),
        "shot_count": len(shots),
        "multiview_count": len(multiview.generated_scenes),
        "background_count": len(backgrounds.generated_backgrounds),
        "stage_count": len(stages.generated_stages),
        "keyframe_count": len(keyframes.generated_keyframes),
        "reference_order_verified": True,
        "incremental_persistence_verified": True,
        "scene_scope_invalidation_verified": True,
    }


def main() -> None:
    print(json.dumps(asyncio.run(run_fake_pipeline()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
