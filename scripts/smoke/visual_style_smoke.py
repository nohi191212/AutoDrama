from __future__ import annotations

import asyncio
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
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.visual_style = "cg_animation"
    repo = ProjectRepository(settings)
    project_id = f"visual_style_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Visual Style Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    state = await workflow.run(project_dir, until="role_appearance_design", force=True)
    prompts = [appearance.prompt for role in state.roles.values() for appearance in role.appearances.values()]
    if not prompts:
        raise AssertionError("No role appearance prompts generated")
    for prompt in prompts:
        if "CG动画电影风" not in prompt:
            raise AssertionError(f"Prompt missing CG animation style: {prompt}")
        if "三视图" not in prompt:
            raise AssertionError(f"Prompt missing turnaround sheet requirement: {prompt}")

    print("visual_style_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"appearance_prompts={len(prompts)}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
