"""Run the new reusable-background shot chain with fake providers."""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.generation import GenerationWorkflow
from autodrama.workflows.postgen import PostgenWorkflow
from autodrama.workflows.pregen import PREGEN_NODES, PregenWorkflow


def main() -> None:
    work_dir = ROOT / ".tmp" / "shot_pipeline_fake_e2e"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    (work_dir / "story.md").write_text("林舟在雨夜办公室发现合同异常，苏晚递来邮件截图。", encoding="utf-8")
    config_path = work_dir / "config.yaml"
    config_path.write_text(
        """
project:
  id: shot_pipeline_fake_e2e_v3
  title: 测试短片
  script_outline_file: ./story.md
  episode_count: 1
  episode_duration_seconds: 60
output:
  root_dir: ./outputs
  project_dir_template: "{date}_{slug}"
generation:
  expected_output_seconds: 30
  visual_style:
    schema_version: 2
    medium: stylized_3d_cg
    render_engine_language: [高质感风格化 CG 短剧]
postgen:
  max_source_clips_per_plan: 3
providers: {}
""".lstrip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config()
    workflow = PregenWorkflow(repo=repo, router=ProviderRouter(settings, provider_override="fake"))
    clip_segment_index = PREGEN_NODES.index("clip_segment")
    for node_name in PREGEN_NODES[: clip_segment_index + 1]:
        asyncio.run(workflow.run(project_dir, only=node_name))

    episode_key = "episode_001"
    segment_path = (
        project_dir
        / "assets"
        / "json"
        / "nodes"
        / "clip_segment"
        / f"{episode_key}.json"
    )
    segment = json.loads(segment_path.read_text(encoding="utf-8"))
    base_clip = next(iter(segment.values()))
    repo.write_json(
        segment_path,
        {
            str(index): {
                **base_clip,
                "text": f"{base_clip['text']} 连续剧情片段{index}。",
            }
            for index in range(1, 11)
        },
    )
    for node_name in PREGEN_NODES[
        clip_segment_index + 1 : PREGEN_NODES.index("shot_manifest_generation") + 1
    ]:
        asyncio.run(workflow.run(project_dir, only=node_name))

    plan = json.loads(
        (
            project_dir
            / "assets"
            / "json"
            / "nodes"
            / "clip_to_shots"
            / f"{episode_key}.json"
        ).read_text(encoding="utf-8")
    )
    selection = json.loads(
        (
            project_dir
            / "assets"
            / "json"
            / "expected_output_selection.json"
        ).read_text(encoding="utf-8")
    )["episodes"][0]
    backgrounds = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_background_image_generation" / f"{episode_key}.json").read_text(encoding="utf-8")
    )
    keyframes = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_keyframe_image_generation" / f"{episode_key}.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8"))
    generated_backgrounds = backgrounds["generated_backgrounds"]
    generated_keyframes = keyframes["generated_keyframes"]
    if len(plan["clips"]) != 10:
        raise AssertionError("clip_to_shots must plan every clip before cost limiting")
    if (
        selection["selected_clip_count"] != 5
        or selection["total_clip_count"] != 10
        or selection["planned_output_seconds"] != 30
    ):
        raise AssertionError(f"unexpected 30-second whole-clip prefix: {selection}")
    if len(generated_keyframes) != selection["selected_shot_count"]:
        raise AssertionError("keyframe generation escaped the automatic output scope")
    if len(manifest["shots"]) != selection["selected_shot_count"]:
        raise AssertionError("shot manifest escaped the automatic output scope")
    if not generated_backgrounds or not generated_keyframes:
        raise AssertionError("fake shot pipeline did not generate backgrounds and keyframes")
    if any(item["background_id"].endswith("_shot_background") for item in generated_backgrounds):
        raise AssertionError("background IDs must be layout-scoped, not per-shot aliases")
    if any(item["keyframe_asset_path"] == next(bg["asset_path"] for bg in generated_backgrounds if bg["background_id"] == item["background_id"]) for item in generated_keyframes):
        raise AssertionError("background and keyframe paths must be distinct")
    if any(not shot.get("narrative_angle") or not shot.get("background_asset_id") for shot in manifest["shots"]):
        raise AssertionError("shot manifest is missing narrative angles or backgrounds")
    if manifest.get("schema_version") != 5:
        raise AssertionError("shot manifest did not upgrade to structured contract v5")
    if any(
        "dialogue_lines" not in shot
        or "entity_state_snapshot" not in shot
        or "requires_exact_text" in shot
        or "overlay_text" in shot
        for shot in manifest["shots"]
    ):
        raise AssertionError("shot manifest contains incomplete or legacy shot contract fields")
    for asset_type in ("clip_to_shots", "layout_to_background_prompt", "shot_background", "shot_keyframe_prompt", "shot_keyframe"):
        if not any((project_dir / "logs" / "prompts" / asset_type).glob("*.prompt.txt")):
            raise AssertionError(f"missing final prompt audit log for {asset_type}")

    generation = GenerationWorkflow(repo=repo, router=ProviderRouter(settings, provider_override="fake"))
    asyncio.run(
        generation.run(
            project_dir,
            only="shot_video_generation",
            episode_keys=[episode_key],
        )
    )
    generated_manifest = json.loads((project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8"))
    selected_shot = generated_manifest["shots"][0]
    if any(not shot.get("video_asset_path") for shot in generated_manifest["shots"]):
        raise AssertionError("shot_video_generation did not persist every selected video asset")
    generated_video_output = json.loads(
        (
            project_dir
            / "assets"
            / "json"
            / "nodes"
            / "shot_video_generation.json"
        ).read_text(encoding="utf-8")
    )
    if len(generated_video_output["generated_videos"]) != selection["selected_shot_count"]:
        raise AssertionError("video generation escaped the automatic output scope")
    if selected_shot.get("video_inputs", [{}])[0].get("asset_type") != "shot_keyframe":
        raise AssertionError("shot video input must begin with the generated shot keyframe")
    role_inputs = [
        item for item in selected_shot.get("video_inputs", [])
        if item.get("asset_type") == "roleboard"
    ]
    if {item.get("role_id") for item in role_inputs} != set(selected_shot.get("role_ids", [])):
        raise AssertionError("shot video inputs must include one character turnaround per involved role")
    if any(
        item.get("asset_type") in {"shot_last_frame", "role_subject_element"}
        for item in selected_shot.get("video_inputs", [])
    ):
        raise AssertionError("shot video inputs must not require last frames or subject elements")
    asyncio.run(
        generation.run(
            project_dir,
            only="shot_video_audit",
            episode_keys=[episode_key],
        )
    )
    postgen = PostgenWorkflow(
        repo=repo,
        router=ProviderRouter(settings, provider_override="fake"),
    )
    asyncio.run(
        postgen.run(
            project_dir,
            only="postgen_source_collect",
            episode_keys=[episode_key],
        )
    )
    postgen_sources = json.loads(
        (
            project_dir
            / "assets"
            / "json"
            / "postgen"
            / "source_clips"
            / f"{episode_key}.json"
        ).read_text(encoding="utf-8")
    )
    if len(postgen_sources["source_clips"]) != selection["selected_shot_count"]:
        raise AssertionError(
            "Postgen silently reapplied max_source_clips_per_plan to the automatic output scope"
        )

    # Expanding the configured scope must append the remaining assets without
    # --force, without rerunning clip segmentation/planning, and without
    # replacing already accepted keyframes.
    prefix_keyframe_paths = {
        item["shot_id"]: item["keyframe_asset_path"]
        for item in generated_keyframes
    }
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "expected_output_seconds: 30",
            "expected_output_seconds: -1",
        ),
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    repo = ProjectRepository(settings)
    workflow = PregenWorkflow(
        repo=repo,
        router=ProviderRouter(settings, provider_override="fake"),
    )
    asyncio.run(workflow.run(project_dir))
    expanded_selection = json.loads(
        (
            project_dir
            / "assets"
            / "json"
            / "expected_output_selection.json"
        ).read_text(encoding="utf-8")
    )["episodes"][0]
    expanded_keyframes = json.loads(
        (
            project_dir
            / "assets"
            / "json"
            / "nodes"
            / "shot_keyframe_image_generation"
            / f"{episode_key}.json"
        ).read_text(encoding="utf-8")
    )["generated_keyframes"]
    expanded_manifest = json.loads(
        (project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8")
    )
    if expanded_selection["mode"] != "all":
        raise AssertionError("expected_output_seconds=-1 did not select the full plan")
    if len(expanded_keyframes) != expanded_selection["total_shot_count"]:
        raise AssertionError("scope expansion did not append every remaining keyframe")
    if len(expanded_manifest["shots"]) != expanded_selection["total_shot_count"]:
        raise AssertionError("scope expansion did not append every remaining manifest shot")
    expanded_keyframe_paths = {
        item["shot_id"]: item["keyframe_asset_path"]
        for item in expanded_keyframes
    }
    if any(
        expanded_keyframe_paths.get(shot_id) != asset_path
        for shot_id, asset_path in prefix_keyframe_paths.items()
    ):
        raise AssertionError("scope expansion replaced an existing prefix keyframe")
    expanded_state = repo.load_state(project_dir)
    if expanded_state.metadata.get("expected_output_scope_pending_shot_ids"):
        raise AssertionError("scope expansion left pending pregen shots after manifest generation")

    generation = GenerationWorkflow(
        repo=repo,
        router=ProviderRouter(settings, provider_override="fake"),
    )
    asyncio.run(
        generation.run(
            project_dir,
            only="shot_video_generation",
            episode_keys=[episode_key],
        )
    )
    expanded_video_manifest = json.loads(
        (project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8")
    )
    if any(not shot.get("video_asset_path") for shot in expanded_video_manifest["shots"]):
        raise AssertionError("scope expansion did not append every remaining shot video")
    asyncio.run(
        generation.run(
            project_dir,
            only="shot_video_audit",
            episode_keys=[episode_key],
        )
    )
    postgen = PostgenWorkflow(
        repo=repo,
        router=ProviderRouter(settings, provider_override="fake"),
    )
    asyncio.run(
        postgen.run(
            project_dir,
            only="postgen_source_collect",
            episode_keys=[episode_key],
        )
    )
    expanded_sources = json.loads(
        (
            project_dir
            / "assets"
            / "json"
            / "postgen"
            / "source_clips"
            / f"{episode_key}.json"
        ).read_text(encoding="utf-8")
    )
    if len(expanded_sources["source_clips"]) != expanded_selection["total_shot_count"]:
        raise AssertionError(
            "expected_output_seconds=-1 was truncated by max_source_clips_per_plan"
        )

    # Re-rendering a background must invalidate only its dependent keyframe
    # records, then permit a targeted rebuild of that shot.
    asyncio.run(
        workflow.run(
            project_dir,
            only="shot_background_image_generation",
            force=True,
            episode_keys=[episode_key],
            shot_selectors=["1"],
        )
    )
    invalidated_prompts = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_keyframe_prompt" / f"{episode_key}.json").read_text(encoding="utf-8")
    )
    if any(
        item["shot_id"] == selected_shot["shot_id"]
        for item in invalidated_prompts["prompts"]
    ):
        raise AssertionError("background regeneration did not invalidate its dependent keyframe prompt")
    for node_name in (
        "shot_background_image_audit",
        "shot_keyframe_prompt",
        "shot_keyframe_image_generation",
        "shot_keyframe_image_audit",
        "shot_manifest_generation",
    ):
        kwargs = (
            {}
            if node_name.endswith("_audit")
            else {"episode_keys": [episode_key], "shot_selectors": ["1"]}
        )
        asyncio.run(workflow.run(project_dir, only=node_name, **kwargs))
    print(f"shot_pipeline_fake_e2e_smoke: ok ({project_dir})")


if __name__ == "__main__":
    main()
