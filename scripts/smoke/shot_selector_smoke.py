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

from autodrama.cli import parse_episode_keys, parse_shot_selectors  # noqa: E402
from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    parsed = parse_shot_selectors("1-2,shot_003,episode_001_shot_4")
    require(
        parsed == ["1", "2", "shot_003", "episode_001_shot_4"],
        f"Unexpected parsed shot selectors: {parsed}",
    )

    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"shot_selector_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Selector Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    pregen_workflow = PregenWorkflow(repo=repo, router=router)
    await pregen_workflow.run(project_dir, until="bgm_generation", force=True)

    generation_workflow = GenerationWorkflow(repo=repo, router=router)
    await generation_workflow.run(
        project_dir,
        until="storyboard_generation",
        only="storyboard_generation",
        episode_keys=parse_episode_keys("1"),
    )
    await generation_workflow.run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
        shot_selectors=parse_shot_selectors("1"),
    )

    slot = json.loads((project_dir / "slots" / "episode_001.json").read_text(encoding="utf-8"))
    shots = slot["shots"]
    require(shots[0]["video_asset_path"], "first shot video was not generated")
    require(not shots[1]["video_asset_path"], "second shot video should not be generated")

    node_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_video_generation.json").read_text(encoding="utf-8")
    )
    generated = node_output["generated_videos"]
    require(len(generated) == 1, f"Expected one generated video, got {len(generated)}")
    require(generated[0]["shot_id"] == "episode_001_shot_001", f"Unexpected shot id: {generated[0]['shot_id']}")

    print("shot_selector_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"generated_shot={generated[0]['shot_id']} video={shots[0]['video_asset_path']}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
