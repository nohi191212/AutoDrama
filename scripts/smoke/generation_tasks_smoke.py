from __future__ import annotations

import asyncio
import base64
import json
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
from autodrama.core.errors import ProviderError  # noqa: E402
from autodrama.providers.base import AssetRef, VideoGenerationResult  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402
from smoke_storyboard_fixture import write_fake_storyboard_episode  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class QueueVideoProvider:
    name = "queue-video"
    model = "queue-video-model"
    _TERMINAL_SUCCESS = {"succeeded"}
    _TERMINAL_FAILURE = {"failed", "expired", "cancelled"}

    def __init__(self, *, complete: bool) -> None:
        self.complete = complete
        self.max_polls = 1
        self.poll_interval_seconds = 0
        self.submit_count = 0
        self.query_count = 0

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        del refs
        metadata = metadata or {}
        self.submit_count += 1
        task_id = f"queue-task-{metadata.get('asset_id', 'shot')}"
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            task_status="queued",
            request_id=f"request-{task_id}",
            usage={"duration": duration},
            raw_response={"id": task_id, "status": "queued", "prompt": prompt},
        )

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        self.query_count += 1
        if not self.complete:
            return VideoGenerationResult(
                provider=self.name,
                model=self.model,
                task_id=task_id,
                task_status="running",
                request_id=f"request-{task_id}",
                raw_response={"id": task_id, "status": "running"},
            )

        video_bytes = f"queue video: {task_id}".encode("utf-8")
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
    def __init__(self, provider: QueueVideoProvider) -> None:
        self.provider = provider

    def video(self, purpose: str, *, node_name: str | None = None) -> QueueVideoProvider:
        del purpose, node_name
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    project_id = f"generation_tasks_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Generation Tasks Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    fake_router = ProviderRouter(settings, provider_override="fake")
    await PregenWorkflow(repo=repo, router=fake_router).run(project_dir, until="role_voice_select", force=True)
    fixture_workflow = GenerationWorkflow(repo=repo, router=fake_router)
    write_fake_storyboard_episode(fixture_workflow, project_dir, "episode_001", shot_count=2)

    timeout_provider = QueueVideoProvider(complete=False)
    try:
        await GenerationWorkflow(repo=repo, router=VideoRouter(timeout_provider)).run(
            project_dir,
            until="shot_video_generation",
            only="shot_video_generation",
            episode_keys=parse_episode_keys("1"),
            shot_selectors=parse_shot_selectors("1"),
        )
    except ProviderError:
        pass
    else:
        raise AssertionError("Expected timeout ProviderError")

    task_path = project_dir / "generation_tasks.json"
    require(task_path.exists(), "generation_tasks.json was not created")
    registry = json.loads(task_path.read_text(encoding="utf-8"))
    require(len(registry["tasks"]) == 1, f"Expected one task, got {len(registry['tasks'])}")
    task = registry["tasks"][0]
    require(task["task_status"] == "timeout", f"Unexpected timeout task status: {task['task_status']}")
    require(task["task_id"].startswith("queue-task-"), f"Unexpected task id: {task['task_id']}")
    require(timeout_provider.submit_count == 1, f"Expected one submit, got {timeout_provider.submit_count}")

    complete_provider = QueueVideoProvider(complete=True)
    await GenerationWorkflow(repo=repo, router=VideoRouter(complete_provider)).run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
        shot_selectors=parse_shot_selectors("1"),
    )
    require(complete_provider.submit_count == 0, "Resume should query existing task without submitting a new task")
    require(complete_provider.query_count == 1, f"Expected one query, got {complete_provider.query_count}")

    registry = json.loads(task_path.read_text(encoding="utf-8"))
    task = registry["tasks"][0]
    require(task["task_status"] == "succeeded", f"Unexpected completed task status: {task['task_status']}")
    require(task.get("completed_at"), "Completed task missing completed_at")
    require((project_dir / task["asset_path"]).exists(), f"Generated video missing: {task['asset_path']}")

    shot = json.loads((project_dir / "shots" / "episode_001.json").read_text(encoding="utf-8"))
    require(shot["shots"][0]["video_asset_path"] == task["asset_path"], "shot video path was not updated")

    skip_provider = QueueVideoProvider(complete=True)
    await GenerationWorkflow(repo=repo, router=VideoRouter(skip_provider)).run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
        shot_selectors=parse_shot_selectors("1"),
    )
    require(skip_provider.submit_count == 0, "Completed video should be reused without --force")
    require(skip_provider.query_count == 0, "Completed video should not be polled without --force")

    missing_asset = project_dir / task["asset_path"]
    missing_asset.unlink()
    stale_provider = QueueVideoProvider(complete=True)
    await GenerationWorkflow(repo=repo, router=VideoRouter(stale_provider)).run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
        shot_selectors=parse_shot_selectors("1"),
    )
    require(stale_provider.submit_count == 1, "Succeeded task with missing local video should submit a new task")
    require(stale_provider.query_count == 1, "Resubmitted stale video task should be polled once")
    registry = json.loads(task_path.read_text(encoding="utf-8"))
    task = registry["tasks"][0]
    require(task["task_status"] == "succeeded", f"Unexpected stale rerun task status: {task['task_status']}")
    require("stale_at" not in task, "Completed replacement task should not retain stale_at")
    require((project_dir / task["asset_path"]).exists(), f"Replacement video missing: {task['asset_path']}")

    force_provider = QueueVideoProvider(complete=True)
    await GenerationWorkflow(repo=repo, router=VideoRouter(force_provider)).run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
        shot_selectors=parse_shot_selectors("1"),
        force=True,
    )
    require(force_provider.submit_count == 1, "Forced video generation should submit a new task")
    require(force_provider.query_count == 1, f"Forced video generation should poll once, got {force_provider.query_count}")

    print("generation_tasks_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"task_id={task['task_id']} video={task['asset_path']}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
