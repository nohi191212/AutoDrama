from __future__ import annotations

import asyncio
import base64
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.cli import parse_episode_keys  # noqa: E402
from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardEpisodeOutput, StoryboardShot  # noqa: E402
from autodrama.providers.base import AssetRef, VideoGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class SerialPreviousRefVideoProvider:
    name = "serial-previous-ref-video"
    model = "serial-previous-ref-video-model"
    _TERMINAL_SUCCESS = {"succeeded"}
    _TERMINAL_FAILURE = {"failed", "expired", "cancelled"}

    def __init__(self) -> None:
        self.max_polls = 1
        self.poll_interval_seconds = 0
        self.settings = SimpleNamespace(options={"video_reference_mode": "ref_frame_previous_video"})
        self.submissions: list[dict[str, Any]] = []

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        del prompt, duration
        metadata = metadata or {}
        refs = refs or []
        task_id = f"serial-task-{metadata.get('asset_id', 'shot')}"
        self.submissions.append(
            {
                "task_id": task_id,
                "shot_id": metadata.get("shot_id"),
                "refs": refs,
            }
        )
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            task_status="queued",
            request_id=f"request-{task_id}",
            raw_response={"id": task_id, "status": "queued"},
        )

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        video_bytes = f"serial previous ref video: {task_id}".encode("utf-8")
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            task_status="succeeded",
            video_data=base64.b64encode(video_bytes).decode("ascii"),
            request_id=f"request-{task_id}",
            raw_response={"id": task_id, "status": "succeeded"},
        )


class VideoRouter:
    def __init__(self, provider: SerialPreviousRefVideoProvider) -> None:
        self.provider = provider

    def video(self, purpose: str) -> SerialPreviousRefVideoProvider:
        del purpose
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = (
        f"shot_video_serial_previous_ref_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    project_dir = repo.create_project(
        title="Shot Video Serial Previous Ref Smoke",
        raw_script="林舟在会议室展示合同，随后推近合同细节。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    provider = SerialPreviousRefVideoProvider()
    workflow = GenerationWorkflow(repo=repo, router=VideoRouter(provider))
    workflow._save_storyboard_episode(
        project_dir,
        StoryboardEpisodeOutput(
            episode_key="episode_001",
            shots=[
                StoryboardShot(
                    shot_id="episode_001_shot_001",
                    index=1,
                    layout_id="layout_room",
                    title="展示合同",
                    duration_seconds=6,
                    ref_frame_prompt="林舟站在会议桌边展示合同。",
                    video_prompt="林舟站在会议桌边展示合同，镜头稳定推近他的手部动作。",
                    physical_space_key="layout_room::main_table",
                ),
                StoryboardShot(
                    shot_id="episode_001_shot_002",
                    index=2,
                    layout_id="layout_room",
                    title="合同细节",
                    duration_seconds=6,
                    ref_frame_prompt="同一张会议桌上，合同被推到镜头前。",
                    video_prompt="合同沿着同一张会议桌被推向镜头，延续上一镜的人物站位和桌面方向。",
                    physical_space_key="layout_room::main_table",
                ),
            ],
        ),
    )

    await workflow.run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
    )

    require(len(provider.submissions) == 2, f"Expected two submissions, got {len(provider.submissions)}")
    require(provider.submissions[0]["shot_id"] == "episode_001_shot_001", provider.submissions)
    require(provider.submissions[1]["shot_id"] == "episode_001_shot_002", provider.submissions)
    require(provider.submissions[0]["refs"] == [], "First shot should not have a previous video ref")

    second_refs = provider.submissions[1]["refs"]
    require(len(second_refs) == 1, f"Second shot should have one previous video ref, got {len(second_refs)}")
    previous_ref = second_refs[0]
    require(previous_ref.metadata.get("asset_type") == "previous_shot_video", previous_ref.metadata)
    require(previous_ref.metadata.get("previous_shot_id") == "episode_001_shot_001", previous_ref.metadata)
    require(
        Path(str(previous_ref.path)).exists(),
        f"Previous video ref was not written before second submit: {previous_ref.path}",
    )

    episode = workflow._load_storyboard_episode(project_dir, "episode_001")
    require(episode.shots[0].video_asset_path, "First shot video was not saved")
    require(episode.shots[1].video_asset_path, "Second shot video was not saved")

    print("shot_video_serial_previous_ref_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"previous_video_ref={previous_ref.path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
