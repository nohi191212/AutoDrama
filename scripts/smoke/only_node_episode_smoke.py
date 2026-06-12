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
from autodrama.workflows.generation import GENERATION_NODES, GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402
from smoke_storyboard_fixture import write_fake_storyboard_episode  # noqa: E402


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
    await pregen_workflow.run(project_dir, until="role_voice_select", force=True)

    storyboard_sheet_output_before = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "storyboard_generation.json").read_text(encoding="utf-8")
    )
    await pregen_workflow.run(
        project_dir,
        only="storyboard_generation",
        episode_keys=["episode_001"],
        force=True,
    )
    storyboard_sheet_output_after = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "storyboard_generation.json").read_text(encoding="utf-8")
    )
    generated_episodes = {
        item["episode_key"]
        for item in storyboard_sheet_output_after["generated_storyboards"]
    }
    require(generated_episodes == {"episode_001", "episode_002"}, "pregen storyboard output lost an existing episode")
    before_ep2 = [
        item for item in storyboard_sheet_output_before["generated_storyboards"] if item["episode_key"] == "episode_002"
    ][0]
    after_ep2 = [
        item for item in storyboard_sheet_output_after["generated_storyboards"] if item["episode_key"] == "episode_002"
    ][0]
    require(after_ep2 == before_ep2, "episode_002 pregen storyboard changed during episode_001-only rerun")

    generation_workflow = GenerationWorkflow(repo=repo, router=router)
    write_fake_storyboard_episode(generation_workflow, project_dir, "episode_001", shot_count=2)
    write_fake_storyboard_episode(generation_workflow, project_dir, "episode_002", shot_count=2)

    try:
        await generation_workflow.run(
            project_dir,
            until="dynamic_asset_solidification",
            only="storyboard_generation",
            episode_keys=parse_episode_keys("2"),
        )
    except ValueError as exc:
        require("Unsupported generation only node" in str(exc), f"Unexpected error for removed node: {exc}")
    else:
        raise AssertionError("generation storyboard_generation should no longer be supported")

    require(
        GENERATION_NODES == [
            "shot_dialogue_audio_generation",
            "shot_video_generation",
            "dynamic_asset_solidification",
        ],
        f"Unexpected generation node list: {GENERATION_NODES}",
    )

    await generation_workflow.run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=["episode_002"],
    )
    require(
        '"assets/videos/shots/' not in shot_text(project_dir, "episode_001"),
        "episode_001 got a video during episode_002-only generation",
    )
    require(
        '"assets/videos/shots/' in shot_text(project_dir, "episode_002"),
        "episode_002 did not get a video",
    )

    print("only_node_episode_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
