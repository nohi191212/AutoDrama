from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardEpisodeOutput  # noqa: E402
from autodrama.providers.local.mock.fake import (  # noqa: E402
    FakeImageProvider,
    FakeMusicProvider,
    FakeTextProvider,
    FakeVideoProvider,
    FakeVoiceDesignProvider,
)
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402

T = TypeVar("T", bound=BaseModel)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingTextProvider(FakeTextProvider):
    def __init__(self) -> None:
        self.storyboard_prompts: dict[str, str] = {}

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        metadata = metadata or {}
        if schema is StoryboardEpisodeOutput or metadata.get("node_name") == "storyboard_generation":
            episode_key = str(metadata.get("episode_key"))
            self.storyboard_prompts[episode_key] = prompt
        return await super().generate_json(prompt, schema, temperature=temperature, metadata=metadata)


class RecordingRouter:
    def __init__(self) -> None:
        self.text_provider = RecordingTextProvider()
        self.image_provider = FakeImageProvider()
        self.music_provider = FakeMusicProvider()
        self.video_provider = FakeVideoProvider()
        self.voice_provider = FakeVoiceDesignProvider()

    def text(self, purpose: str):
        del purpose
        return self.text_provider

    def image(self, purpose: str):
        del purpose
        return self.image_provider

    def music(self, purpose: str):
        del purpose
        return self.music_provider

    def video(self, purpose: str):
        del purpose
        return self.video_provider

    def audio(self, purpose: str):
        del purpose
        return self.voice_provider


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

    router = RecordingRouter()
    pregen_workflow = PregenWorkflow(repo=repo, router=router)
    await pregen_workflow.run(project_dir, until="bgm_generation", force=True)

    generation_workflow = GenerationWorkflow(repo=repo, router=router)
    state = await generation_workflow.run(
        project_dir,
        until="dynamic_asset_solidification",
        episode_keys=["episode_001", "episode_002", "episode_003"],
    )

    prompts = router.text_provider.storyboard_prompts
    require("episode_001" in prompts, "episode_001 storyboard prompt was not recorded")
    require("episode_002" in prompts, "episode_002 storyboard prompt was not recorded")
    require("episode_003" in prompts, "episode_003 storyboard prompt was not recorded")
    require("前序分镜历史" in prompts["episode_001"], "storyboard prompt missing history section")
    require("episode_001_shot_001" in prompts["episode_002"], "episode_002 prompt missing episode_001 history")
    require("episode_001_shot_001" in prompts["episode_003"], "episode_003 prompt missing episode_001 history")
    require("episode_002_shot_001" in prompts["episode_003"], "episode_003 prompt missing episode_002 history")

    history_path = project_dir / "assets" / "json" / "storyboard_history.json"
    require(history_path.exists(), "storyboard_history.json was not created")
    history = json.loads(history_path.read_text(encoding="utf-8"))
    require(
        [item["episode_key"] for item in history["episodes"]] == ["episode_001", "episode_002", "episode_003"],
        f"Unexpected storyboard history order: {history['episodes']}",
    )

    storyboard_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "storyboard_generation.json").read_text(encoding="utf-8")
    )
    require(
        storyboard_output["generated_episodes"] == ["episode_001", "episode_002", "episode_003"],
        f"Storyboard output was not aggregated: {storyboard_output}",
    )
    video_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_video_generation.json").read_text(encoding="utf-8")
    )
    generated_video_episodes = {
        item["episode_key"]
        for item in video_output["generated_videos"]
    }
    require(
        generated_video_episodes == {"episode_001", "episode_002", "episode_003"},
        f"Shot video output was not aggregated: {generated_video_episodes}",
    )

    for episode_key in ("episode_001", "episode_002", "episode_003"):
        slot_text = (project_dir / "slots" / f"{episode_key}.json").read_text(encoding="utf-8")
        require('"dialogue_audio_assets"' in slot_text, f"{episode_key} missing dialogue audio assets")
        require('"assets/images/ref_frames/' in slot_text, f"{episode_key} missing ref frame")
        require('"assets/videos/shots/' in slot_text, f"{episode_key} missing shot video")
        require('"solidified_asset_ids"' in slot_text, f"{episode_key} missing solidified asset ids")

    checklist = json.loads((project_dir / "generation_checklist.json").read_text(encoding="utf-8"))
    items = {item["episode_key"]: item for item in checklist["episodes"]}
    for episode_key in ("episode_001", "episode_002", "episode_003"):
        require(not items[episode_key]["generate"], f"{episode_key} generate should be false")
        require(items[episode_key]["generation_status"] == "completed", f"{episode_key} should be completed")

    require("dynamic_asset_solidification" in state.completed_nodes, "state missing final generation node")

    print("episode_serial_generation_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"history_episodes={len(history['episodes'])}")
    print(f"video_items={len(video_output['generated_videos'])}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
