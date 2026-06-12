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
from autodrama.core.schemas import StoryboardEpisodeOutput  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"shot_manifest_generation_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Manifest Generation Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    router = ProviderRouter(settings, provider_override="fake")
    state = await PregenWorkflow(repo=repo, router=router).run(
        project_dir,
        until="shot_manifest_generation",
        force=True,
    )

    shot_path = project_dir / "shots" / "episode_001.json"
    require(shot_path.exists(), f"Shot manifest missing: {shot_path}")
    episode = StoryboardEpisodeOutput.model_validate_json(shot_path.read_text(encoding="utf-8"))
    require(len(episode.shots) == 12, f"Expected 12 shots, got {len(episode.shots)}")

    dialogue_body = "这份合同被换过，时间线就在这里"
    dialogue_shots = [shot for shot in episode.shots if shot.dialogue]
    require(dialogue_shots, "Expected at least one dialogue shot")
    dialogue_shot = dialogue_shots[0]
    require(dialogue_body in dialogue_shot.video_prompt, dialogue_shot.video_prompt)
    require("音频参考" not in dialogue_shot.video_prompt, dialogue_shot.video_prompt)
    require("参考音频" not in dialogue_shot.video_prompt, dialogue_shot.video_prompt)
    require(dialogue_shot.storyboard_panel_asset_path, "Dialogue shot missing storyboard panel asset path")
    require(dialogue_shot.storyboard_panel_bbox_1000 is not None, "Dialogue shot missing storyboard panel bbox")

    output_path = project_dir / "assets" / "json" / "nodes" / "shot_manifest_generation.json"
    require(output_path.exists(), f"Shot manifest node output missing: {output_path}")
    output = json.loads(output_path.read_text(encoding="utf-8"))
    require(output["episodes"][0]["shot_path"] == "shots/episode_001.json", output)
    require("shot_manifest_generation" in state.completed_nodes, "state missing completed shot_manifest_generation")

    final_prompt = GenerationWorkflow(repo=repo, router=router)._shot_video_prompt(
        state,
        episode,
        dialogue_shot,
        provider=router.video("shot"),
        project_dir=project_dir,
    )
    require(dialogue_body in final_prompt, final_prompt)
    require("音频参考" not in final_prompt, final_prompt)
    require("音频1" not in final_prompt, final_prompt)

    print("shot_manifest_generation_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"shot_path={shot_path}")
    print(f"dialogue_shot={dialogue_shot.shot_id}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
