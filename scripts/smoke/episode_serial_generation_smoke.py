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
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402
from smoke_storyboard_fixture import write_fake_storyboard_episode  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 3
    repo = ProjectRepository(settings)
    project_id = f"episode_serial_generation_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Episode Serial Generation Smoke",
        raw_script="林舟发现合同被调包，并在连续三集里逐步公开反击赵启。",
        project_id=project_id,
        episode_count=3,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    await PregenWorkflow(repo=repo, router=router).run(project_dir, until="role_voice_generation", force=True)

    generation_workflow = GenerationWorkflow(repo=repo, router=router)
    for episode_key in ("episode_001", "episode_002", "episode_003"):
        write_fake_storyboard_episode(generation_workflow, project_dir, episode_key, shot_count=2)

    state = await generation_workflow.run(
        project_dir,
        until="dynamic_asset_solidification",
        episode_keys=["episode_001", "episode_002", "episode_003"],
    )

    video_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_video_generation.json").read_text(encoding="utf-8")
    )
    generated_video_episodes = {item["episode_key"] for item in video_output["generated_videos"]}
    require(
        generated_video_episodes == {"episode_001", "episode_002", "episode_003"},
        f"Shot video output was not aggregated: {generated_video_episodes}",
    )

    solidification_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "dynamic_asset_solidification.json").read_text(encoding="utf-8")
    )
    solidified_episodes = {item["episode_key"] for item in solidification_output["solidified_assets"]}
    require(
        solidified_episodes == {"episode_001", "episode_002", "episode_003"},
        f"Solidification output was not aggregated: {solidified_episodes}",
    )

    for episode_key in ("episode_001", "episode_002", "episode_003"):
        shot = json.loads((project_dir / "shots" / f"{episode_key}.json").read_text(encoding="utf-8"))
        shot_text = json.dumps(shot, ensure_ascii=False)
        require('"assets/audios/shot_dialogues/' not in shot_text, f"{episode_key} generated dialogue audio")
        require('"assets/videos/shots/' in shot_text, f"{episode_key} missing shot video")
        require('"solidified_asset_ids"' in shot_text, f"{episode_key} missing solidified asset ids")

    checklist = json.loads((project_dir / "generation_checklist.json").read_text(encoding="utf-8"))
    items = {item["episode_key"]: item for item in checklist["episodes"]}
    for episode_key in ("episode_001", "episode_002", "episode_003"):
        require(not items[episode_key]["generate"], f"{episode_key} generate should be false")
        require(items[episode_key]["generation_status"] == "completed", f"{episode_key} should be completed")
        expected_status = {"shot_video": "completed", "solidified": "completed"}
        require(
            items[episode_key]["node_status"] == expected_status,
            f"{episode_key} checklist tracked unexpected nodes: {items[episode_key]['node_status']}",
        )

    require("dynamic_asset_solidification" in state.completed_nodes, "state missing final generation node")

    print("episode_serial_generation_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"video_items={len(video_output['generated_videos'])}")
    print(f"solidified_items={len(solidification_output['solidified_assets'])}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
