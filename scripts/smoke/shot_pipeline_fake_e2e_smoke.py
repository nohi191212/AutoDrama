"""Run the new reusable-background shot chain with fake providers."""

from __future__ import annotations

import asyncio
import json
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
from autodrama.workflows.pregen import PREGEN_NODES, PregenWorkflow


def main() -> None:
    work_dir = ROOT / ".tmp" / "shot_pipeline_fake_e2e"
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
  episode_duration_seconds: 15
output:
  root_dir: ./outputs
  project_dir_template: "{date}_{slug}"
providers: {}
""".lstrip(),
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    repo = ProjectRepository(settings)
    project_dir = repo.create_project_from_config()
    workflow = PregenWorkflow(repo=repo, router=ProviderRouter(settings, provider_override="fake"))
    # This smoke targets the shot generation contract. Image audit nodes have
    # their own provider fixtures and are intentionally exercised separately.
    for node_name in PREGEN_NODES[: PREGEN_NODES.index("shot_manifest_generation") + 1]:
        if node_name.endswith("_audit"):
            continue
        asyncio.run(workflow.run(project_dir, only=node_name))

    episode_key = "episode_001"
    backgrounds = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_background_image_generation" / f"{episode_key}.json").read_text(encoding="utf-8")
    )
    keyframes = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_keyframe_image_generation" / f"{episode_key}.json").read_text(encoding="utf-8")
    )
    manifest = json.loads((project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8"))
    generated_backgrounds = backgrounds["generated_backgrounds"]
    generated_keyframes = keyframes["generated_keyframes"]
    if not generated_backgrounds or not generated_keyframes:
        raise AssertionError("fake shot pipeline did not generate backgrounds and keyframes")
    if any(item["background_id"].endswith("_shot_background") for item in generated_backgrounds):
        raise AssertionError("background IDs must be layout-scoped, not per-shot aliases")
    if any(item["keyframe_asset_path"] == next(bg["asset_path"] for bg in generated_backgrounds if bg["background_id"] == item["background_id"]) for item in generated_keyframes):
        raise AssertionError("background and keyframe paths must be distinct")
    if any(not shot.get("narrative_angle") or not shot.get("background_asset_id") for shot in manifest["shots"]):
        raise AssertionError("shot manifest is missing narrative angles or backgrounds")
    for asset_type in ("clip_to_shots", "layout_to_background_prompt", "shot_background", "shot_keyframe_prompt", "shot_keyframe"):
        if not any((project_dir / "logs" / "prompts" / asset_type).glob("*.prompt.txt")):
            raise AssertionError(f"missing final prompt audit log for {asset_type}")

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
    if invalidated_prompts["prompts"]:
        raise AssertionError("background regeneration did not invalidate dependent keyframe prompts")
    for node_name in ("shot_keyframe_prompt", "shot_keyframe_image_generation", "shot_manifest_generation"):
        asyncio.run(
            workflow.run(
                project_dir,
                only=node_name,
                episode_keys=[episode_key],
                shot_selectors=["1"],
            )
        )
    generation = GenerationWorkflow(repo=repo, router=ProviderRouter(settings, provider_override="fake"))
    asyncio.run(
        generation.run(
            project_dir,
            only="shot_video_generation",
            episode_keys=[episode_key],
            shot_selectors=["1"],
        )
    )
    generated_manifest = json.loads((project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8"))
    selected_shot = generated_manifest["shots"][0]
    if not selected_shot.get("video_asset_path"):
        raise AssertionError("shot_video_generation did not persist a video asset")
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
    print(f"shot_pipeline_fake_e2e_smoke: ok ({project_dir})")


if __name__ == "__main__":
    main()
