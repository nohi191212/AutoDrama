from __future__ import annotations

import asyncio
import base64
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.cli import parse_episode_keys, parse_shot_selectors  # noqa: E402
from autodrama.config import load_settings  # noqa: E402
from autodrama.providers.base import AssetRef, VideoGenerationResult  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


PNG_1X1 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class InheritanceVideoProvider:
    name = "inheritance-video"
    model = "inheritance-video-model"
    _TERMINAL_SUCCESS = {"succeeded"}
    _TERMINAL_FAILURE = {"failed", "expired", "cancelled"}

    def __init__(self) -> None:
        self.max_polls = 1
        self.poll_interval_seconds = 0
        self.submissions: list[dict[str, Any]] = []

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        metadata = metadata or {}
        refs = refs or []
        task_id = f"inheritance-task-{metadata.get('asset_id', 'shot')}"
        self.submissions.append(
            {
                "task_id": task_id,
                "shot_id": metadata.get("shot_id"),
                "refs": refs,
                "prompt": prompt,
                "duration": duration,
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
        video_bytes = f"inheritance video: {task_id}".encode("utf-8")
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            task_status="succeeded",
            video_data=base64.b64encode(video_bytes).decode("ascii"),
            last_frame_data=PNG_1X1,
            request_id=f"request-{task_id}",
            raw_response={"id": task_id, "status": "succeeded"},
        )


class VideoRouter:
    def __init__(self, provider: InheritanceVideoProvider) -> None:
        self.provider = provider

    def video(self, purpose: str) -> InheritanceVideoProvider:
        del purpose
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"shot_video_inheritance_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Video Inheritance Smoke",
        raw_script="林舟在会议前发现合同被调包，随即在会议室承接上一动作公开反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    fake_router = ProviderRouter(settings, provider_override="fake")
    await PregenWorkflow(repo=repo, router=fake_router).run(project_dir, until="bgm_generation", force=True)
    storyboard_workflow = GenerationWorkflow(repo=repo, router=fake_router)
    await storyboard_workflow.run(
        project_dir,
        until="storyboard_generation",
        only="storyboard_generation",
        episode_keys=parse_episode_keys("1"),
    )

    episode = storyboard_workflow._load_storyboard_episode(project_dir, "episode_001")
    require(len(episode.shots) >= 2, "Smoke storyboard must contain at least two shots")
    episode.shots[1].start_frame_source = "previous_shot_last_frame"
    episode.shots[1].start_frame_inheritance_reason = "第二镜承接第一镜末尾的手部动作和证据位置，必须无缝延续。"
    storyboard_workflow._save_storyboard_episode(project_dir, episode)

    provider = InheritanceVideoProvider()
    await GenerationWorkflow(repo=repo, router=VideoRouter(provider)).run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
        shot_selectors=parse_shot_selectors("1-2"),
    )

    require(len(provider.submissions) == 2, f"Expected two submissions, got {len(provider.submissions)}")
    require(provider.submissions[0]["shot_id"] == "episode_001_shot_001", "First shot should submit first")
    require(provider.submissions[1]["shot_id"] == "episode_001_shot_002", "Inherited shot should submit second")
    inherited_refs = provider.submissions[1]["refs"]
    require(len(inherited_refs) == 1, f"Inherited shot should use exactly one first-frame ref, got {len(inherited_refs)}")
    inherited_ref = inherited_refs[0]
    require(inherited_ref.metadata.get("seedance_role") == "first_frame", "Inherited ref must be marked first_frame")
    require(inherited_ref.metadata.get("asset_type") == "previous_shot_last_frame", "Inherited ref asset type mismatch")
    require(Path(str(inherited_ref.path)).exists(), f"Inherited first-frame path missing: {inherited_ref.path}")

    episode = storyboard_workflow._load_storyboard_episode(project_dir, "episode_001")
    require(episode.shots[0].video_last_frame_asset_path, "First shot last frame was not saved")
    require(episode.shots[1].video_asset_path, "Second shot video was not saved")
    require(episode.shots[1].video_last_frame_asset_path, "Second shot last frame was not saved")

    print("shot_video_inheritance_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"first_frame_ref={inherited_ref.path}")
    print(f"second_video={episode.shots[1].video_asset_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
