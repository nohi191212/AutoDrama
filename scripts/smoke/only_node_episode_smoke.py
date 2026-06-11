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

from autodrama.cli import parse_episode_keys  # noqa: E402
from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def shot_text(project_dir: Path, episode_key: str) -> str:
    return (project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8")


async def main_async() -> int:
    parsed = parse_episode_keys("1,episode_2,episode-003，4,6-7")
    require(
        parsed
        == [
            "episode_001",
            "episode_002",
            "episode_003",
            "episode_004",
            "episode_006",
            "episode_007",
        ],
        f"Unexpected parsed episode keys: {parsed}",
    )

    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 2
    repo = ProjectRepository(settings)
    project_id = f"only_node_episode_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Only Node Episode Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=2,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    pregen_workflow = PregenWorkflow(repo=repo, router=router)
    await pregen_workflow.run(project_dir, until="role_voice_generation", force=True)

    generation_workflow = GenerationWorkflow(repo=repo, router=router)
    await generation_workflow.run(project_dir, until="storyboard_generation", only="storyboard_generation")

    episode_002_before = shot_text(project_dir, "episode_002")
    await generation_workflow.run(
        project_dir,
        until="storyboard_generation",
        only="storyboard_generation",
        episode_keys=["episode_001"],
    )
    episode_002_after = shot_text(project_dir, "episode_002")
    require(episode_002_after == episode_002_before, "episode_002 storyboard changed during episode_001-only pregen")

    storyboard_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "storyboard_generation.json").read_text(encoding="utf-8")
    )
    require(
        storyboard_output["generated_episodes"] == ["episode_001"],
        f"Expected only episode_001 storyboard output, got {storyboard_output['generated_episodes']}",
    )

    try:
        await generation_workflow.run(
            project_dir,
            until="dynamic_asset_solidification",
            only="shot_bgm_generation",
            episode_keys=parse_episode_keys("2"),
        )
    except ValueError as exc:
        require("Unsupported generation only node" in str(exc), f"Unexpected error for removed node: {exc}")
    else:
        raise AssertionError("shot_bgm_generation should no longer be a supported generation node")

    await generation_workflow.run(
        project_dir,
        until="dynamic_asset_solidification",
        only="ref_frame_generation",
        episode_keys=["episode_002"],
    )
    require(
        '"assets/images/ref_frames/' not in shot_text(project_dir, "episode_001"),
        "episode_001 got a ref frame during episode_002-only generation",
    )
    require(
        '"assets/images/ref_frames/' in shot_text(project_dir, "episode_002"),
        "episode_002 did not get a ref frame",
    )
    require(
        '"assets/videos/shots/' not in shot_text(project_dir, "episode_002"),
        "shot_video_generation ran during ref_frame_generation-only run",
    )

    print("only_node_episode_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
