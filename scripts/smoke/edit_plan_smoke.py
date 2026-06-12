from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.editing import EditingWorkflow  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"edit_plan_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Edit Plan Smoke",
        raw_script="A short office proof-reveal scene.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    pregen_workflow = PregenWorkflow(repo=repo, router=router)
    state = await pregen_workflow.run(project_dir, until="role_voice_select", force=True)

    generation_workflow = GenerationWorkflow(repo=repo, router=router)
    state = await generation_workflow.run(project_dir, until="dynamic_asset_solidification")

    editing_workflow = EditingWorkflow(repo=repo, router=router)
    state = await editing_workflow.run(
        project_dir,
        until="edit_plan_generation",
        episode_keys=["episode_001"],
        force=True,
    )

    if "edit_plan_generation" not in state.completed_nodes:
        raise AssertionError("edit_plan_generation was not marked completed")

    plan_path = project_dir / "assets" / "json" / "edit_plans" / "episode_001.json"
    node_path = project_dir / "assets" / "json" / "nodes" / "edit_plan_generation.json"
    if not plan_path.exists():
        raise AssertionError(f"Edit plan missing: {plan_path}")
    if not node_path.exists():
        raise AssertionError(f"Node output missing: {node_path}")

    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not plan["clips"]:
        raise AssertionError("Edit plan has no clips")
    if not plan["subtitle_cues"]:
        raise AssertionError("Edit plan has no subtitle cues")
    if any(item.get("required") for item in plan["missing_assets"]):
        raise AssertionError(f"Edit plan has required missing assets: {plan['missing_assets']}")
    if any(
        layer.get("metadata", {}).get("source_node") == "shot_bgm_generation"
        for layer in plan["audio_layers"]
    ):
        raise AssertionError("Edit plan should not include deprecated shot_bgm_generation audio layers")
    if plan["output_video_path"] != "outputs/videos/episode_001.mp4":
        raise AssertionError(f"Unexpected output path: {plan['output_video_path']}")

    print("edit_plan_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"clips={len(plan['clips'])} subtitles={len(plan['subtitle_cues'])}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
