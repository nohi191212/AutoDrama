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

from autodrama.cli import parse_shot_selectors  # noqa: E402
from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardNextShotOutput  # noqa: E402
from autodrama.providers.local.mock.fake import (  # noqa: E402
    FakeAudioJudgeProvider,
    FakeImageProvider,
    FakeMusicProvider,
    FakeTextProvider,
    FakeVideoProvider,
    FakeVoiceDesignProvider,
)
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402

T = TypeVar("T", bound=BaseModel)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingTextProvider(FakeTextProvider):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        result = await super().generate_json(prompt, schema, temperature=temperature, metadata=metadata)
        metadata = metadata or {}
        node_name = metadata.get("node_name")
        if schema is StoryboardNextShotOutput or node_name == "storyboard_generation":
            self.events.append(f"storyboard:{metadata.get('generation_step')}")
        elif node_name == "ref_frame_spatial_planning":
            self.events.append(f"spatial:{metadata.get('shot_id')}")
        return result


class RecordingImageProvider(FakeImageProvider):
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def generate_image(
        self,
        prompt: str,
        refs: list[Any] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        metadata = metadata or {}
        self.events.append(f"ref_frame:{metadata.get('shot_id')}")
        return await super().generate_image(prompt, refs=refs, size=size, metadata=metadata)


class RecordingVideoProvider(FakeVideoProvider):
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.task_shot_ids: dict[str, str] = {}

    async def submit_video(
        self,
        prompt: str,
        refs: list[Any] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        metadata = metadata or {}
        shot_id = str(metadata.get("shot_id") or "")
        self.events.append(f"video_submit:{shot_id}")
        result = await super().submit_video(prompt, refs=refs, duration=duration, metadata=metadata)
        if result.task_id:
            self.task_shot_ids[result.task_id] = shot_id
        return result

    async def query_video_task(self, task_id: str):
        self.events.append(f"video_query:{self.task_shot_ids.get(task_id, task_id)}")
        return await super().query_video_task(task_id)


class RecordingRouter:
    def __init__(self, events: list[str]) -> None:
        self.text_provider = RecordingTextProvider(events)
        self.image_provider = RecordingImageProvider(events)
        self.video_provider = RecordingVideoProvider(events)
        self.music_provider = FakeMusicProvider()
        self.voice_provider = FakeVoiceDesignProvider()
        self.audio_judge_provider = FakeAudioJudgeProvider()

    def text(self, purpose: str):
        del purpose
        return self.text_provider

    def image(self, purpose: str):
        del purpose
        return self.image_provider

    def video(self, purpose: str):
        del purpose
        return self.video_provider

    def music(self, purpose: str):
        del purpose
        return self.music_provider

    def audio(self, purpose: str):
        del purpose
        return self.voice_provider

    def audio_judge(self, purpose: str):
        del purpose
        return self.audio_judge_provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"selected_shot_serial_pipeline_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Selected Shot Serial Pipeline Smoke",
        raw_script="林舟发现合同被调包。苏晚递来旧邮件截图。林舟准备在会议室反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    fake_router = ProviderRouter(settings, provider_override="fake")
    await PregenWorkflow(repo=repo, router=fake_router).run(project_dir, until="bgm_generation", force=True)

    events: list[str] = []
    await GenerationWorkflow(repo=repo, router=RecordingRouter(events)).run(
        project_dir,
        until="dynamic_asset_solidification",
        episode_keys=["episode_001"],
        shot_selectors=parse_shot_selectors("1-3"),
    )

    shot_path = project_dir / "shots" / "episode_001.json"
    before = json.loads(shot_path.read_text(encoding="utf-8"))
    shot_1_before = before["shots"][0]
    shot_1_ref = shot_1_before["ref_frame_asset_path"]
    shot_1_video = shot_1_before["video_asset_path"]
    shot_1_solidified = list(shot_1_before["solidified_asset_ids"])

    events.clear()
    await GenerationWorkflow(repo=repo, router=RecordingRouter(events)).run(
        project_dir,
        until="dynamic_asset_solidification",
        episode_keys=["episode_001"],
        shot_selectors=parse_shot_selectors("2-3"),
    )

    require("storyboard:1" not in events, f"Shot 1 storyboard should be preserved as prefix, not regenerated: {events}")
    shot_2 = "episode_001_shot_002"
    shot_3 = "episode_001_shot_003"
    expected_events = [
        "storyboard:2",
        f"spatial:{shot_2}",
        f"ref_frame:{shot_2}",
        f"video_submit:{shot_2}",
        f"video_query:{shot_2}",
        "storyboard:3",
        f"spatial:{shot_3}",
        f"ref_frame:{shot_3}",
        f"video_submit:{shot_3}",
        f"video_query:{shot_3}",
    ]
    for event in expected_events:
        require(event in events, f"Missing event {event}; events={events}")
    positions = {event: events.index(event) for event in expected_events}
    require(
        positions["storyboard:2"]
        < positions[f"ref_frame:{shot_2}"]
        < positions[f"video_submit:{shot_2}"]
        < positions[f"video_query:{shot_2}"]
        < positions["storyboard:3"]
        < positions[f"ref_frame:{shot_3}"]
        < positions[f"video_submit:{shot_3}"]
        < positions[f"video_query:{shot_3}"],
        f"Selected shots did not run as storyboard big loop: {events}",
    )

    after = json.loads(shot_path.read_text(encoding="utf-8"))
    shot_1_after = after["shots"][0]
    require(shot_1_after["ref_frame_asset_path"] == shot_1_ref, "shot 1 ref frame changed")
    require(shot_1_after["video_asset_path"] == shot_1_video, "shot 1 video changed")
    require(shot_1_after["solidified_asset_ids"] == shot_1_solidified, "shot 1 solidified ids changed")
    require((project_dir / shot_1_ref).exists(), "shot 1 ref frame file was deleted")
    require((project_dir / shot_1_video).exists(), "shot 1 video file was deleted")

    node_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "shot_video_generation.json").read_text(encoding="utf-8")
    )
    generated_shots = [item["shot_id"] for item in node_output["generated_videos"]]
    require(generated_shots == [shot_2, shot_3], f"Unexpected generated videos: {generated_shots}")

    dynamic_assets = json.loads(
        (project_dir / "assets" / "json" / "assets" / "dynamic_assets.json").read_text(encoding="utf-8")
    )
    indexed_keys = [(item["shot_id"], item["asset_id"]) for item in dynamic_assets["assets"]]
    require(len(indexed_keys) == len(set(indexed_keys)), f"Duplicate dynamic asset entries: {indexed_keys}")
    indexed_shots = {shot_id for shot_id, _ in indexed_keys}
    require(indexed_shots == {"episode_001_shot_001", shot_2, shot_3}, f"Unexpected indexed shots: {indexed_shots}")

    print("selected_shot_serial_pipeline_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"events={events}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
