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
from autodrama.core.model_catalog import ModelBinding, ModelSpec  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    Role,
    RoleAppearance,
    RoleSubjectVideoIntroTextOutput,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.providers.base import AssetRef, VideoGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def binding(node_name: str, params: dict[str, Any]) -> ModelBinding:
    spec = ModelSpec(
        id="kling:kling-v3-omni",
        provider="kling",
        capability="video",
        input_modalities=["text", "image", "element"],
        output_modalities=["video"],
        params_schema={},
    )
    return ModelBinding(
        node_name=node_name,
        model_id=spec.id,
        provider="kling",
        capability="video",
        params=params,
        spec=spec,
    )


class IntroTextProvider:
    name = "fake-gpt"
    model = "fake-gpt-model"

    async def generate_json(
        self,
        prompt: str,
        schema: type[RoleSubjectVideoIntroTextOutput],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
        refs: list[AssetRef] | None = None,
    ) -> RoleSubjectVideoIntroTextOutput:
        del prompt, temperature, metadata, refs
        require(schema is RoleSubjectVideoIntroTextOutput, f"unexpected schema: {schema}")
        return RoleSubjectVideoIntroTextOutput(intro_text="我是角色，我会守住真相。")


class ConcurrentSubjectVideoProvider:
    name = "kling_omni"
    model = "kling-v3-omni"
    supports_subject_elements = True

    def __init__(self, *, requested_concurrency: int) -> None:
        self.settings = SimpleNamespace(options={"subject_video_duration_seconds": 5})
        self.model_binding = binding(
            "role_subject_video_generation",
            {"role_subject_video_generation_concurrency": requested_concurrency},
        )
        self.active = 0
        self.max_active = 0
        self.generate_count = 0

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        del prompt, refs, duration, wait
        self.generate_count += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.05)
        self.active -= 1
        asset_id = str((metadata or {}).get("asset_id") or "subject")
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=f"subject-task-{asset_id}",
            task_status="succeeded",
            video_data=base64.b64encode(f"subject video {asset_id}".encode("utf-8")).decode("ascii"),
            request_id=f"subject-request-{asset_id}",
            raw_response={"task_id": f"subject-task-{asset_id}", "task_status": "succeeded"},
        )


class SubjectRouter:
    def __init__(self, video_provider: ConcurrentSubjectVideoProvider, text_provider: IntroTextProvider) -> None:
        self.video_provider = video_provider
        self.text_provider = text_provider

    def video(self, purpose: str, *, node_name: str | None = None) -> ConcurrentSubjectVideoProvider:
        del purpose, node_name
        return self.video_provider

    def text(self, purpose: str, *, node_name: str | None = None) -> IntroTextProvider:
        del purpose
        require(node_name == "role_subject_video_intro_text", f"unexpected text node: {node_name}")
        return self.text_provider


class ConcurrentShotVideoProvider:
    name = "concurrent-shot-video"
    model = "concurrent-shot-video-model"
    _TERMINAL_SUCCESS = {"succeeded"}
    _TERMINAL_FAILURE = {"failed", "expired", "cancelled"}

    def __init__(self, *, requested_concurrency: int, reference_mode: str = "full") -> None:
        self.max_polls = 1
        self.poll_interval_seconds = 0
        self.settings = SimpleNamespace(options={"video_reference_mode": reference_mode})
        self.model_binding = binding(
            "shot_video_generation",
            {"shot_video_generation_concurrency": requested_concurrency},
        )
        self.submit_active = 0
        self.query_active = 0
        self.max_submit_active = 0
        self.max_query_active = 0
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
        task_id = f"shot-task-{metadata.get('asset_id', 'shot')}"
        self.submit_active += 1
        self.max_submit_active = max(self.max_submit_active, self.submit_active)
        await asyncio.sleep(0.05)
        self.submit_active -= 1
        self.submissions.append(
            {
                "task_id": task_id,
                "shot_id": metadata.get("shot_id"),
                "refs": refs or [],
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
        self.query_active += 1
        self.max_query_active = max(self.max_query_active, self.query_active)
        await asyncio.sleep(0.05)
        self.query_active -= 1
        video_bytes = f"shot video {task_id}".encode("utf-8")
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            task_status="succeeded",
            video_data=base64.b64encode(video_bytes).decode("ascii"),
            request_id=f"request-{task_id}",
            raw_response={"id": task_id, "status": "succeeded"},
        )


class ShotRouter:
    def __init__(self, provider: ConcurrentShotVideoProvider) -> None:
        self.provider = provider

    def video(self, purpose: str, *, node_name: str | None = None) -> ConcurrentShotVideoProvider:
        del purpose, node_name
        return self.provider


def add_subject_roles(repo: ProjectRepository, project_dir: Path, *, count: int) -> None:
    state = repo.load_state(project_dir)
    for index in range(1, count + 1):
        role_id = f"role_concurrency_{index:02d}"
        appearance_id = f"{role_id}_appearance_base"
        roleboard_path = project_dir / "assets" / "images" / "roles" / f"{appearance_id}.png"
        roleboard_path.parent.mkdir(parents=True, exist_ok=True)
        roleboard_path.write_bytes(f"roleboard {index}".encode("utf-8"))
        appearance = RoleAppearance(
            id=appearance_id,
            role_id=role_id,
            name="base",
            asset_path=str(roleboard_path.relative_to(project_dir)).replace("\\", "/"),
        )
        role = Role(
            id=role_id,
            name=f"角色{index}",
            intro="用于并发烟测的角色。",
            visual_reuse_required=True,
            appearances={appearance.id: appearance},
        )
        state.roles[role.id] = role
        repo.write_json(
            repo.layout.role_record_path(project_dir, role.id),
            {
                "role_id": role.id,
                "role_name": role.name,
                "state_role": role.model_dump(mode="json"),
            },
        )
    repo.save_state(project_dir, state)


def write_storyboard(workflow: GenerationWorkflow, project_dir: Path, *, shot_count: int) -> None:
    shots = [
        StoryboardShot(
            shot_id=f"episode_001_shot_{index:03d}",
            index=index,
            layout_id="layout_concurrency",
            title=f"并发镜头{index}",
            duration_seconds=6,
            transition="硬切",
            start_frame_source="new_reference_frame",
            start_frame_inheritance_reason="并发烟测独立镜头。",
            dialogue=[],
            role_ids=[],
            role_appearance_ids=[],
            role_audio_ids=[],
            prop_ids=[],
            video_prompt=f"第{index}个独立镜头，画面稳定推进。",
        )
        for index in range(1, shot_count + 1)
    ]
    workflow._save_storyboard_episode(
        project_dir,
        StoryboardEpisodeOutput(episode_key="episode_001", shots=shots),
    )


async def run_subject_concurrency_smoke() -> None:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"subject_concurrency_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Subject Concurrency Smoke",
        raw_script="角色主体视频并发烟测。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    add_subject_roles(repo, project_dir, count=7)

    provider = ConcurrentSubjectVideoProvider(requested_concurrency=7)
    workflow = PregenWorkflow(repo=repo, router=SubjectRouter(provider, IntroTextProvider()))
    await workflow.run(project_dir, only="role_subject_video_generation")

    require(provider.generate_count == 7, f"expected 7 subject videos, got {provider.generate_count}")
    require(provider.max_active == 5, f"subject concurrency should cap at 5, got {provider.max_active}")


async def run_shot_concurrency_smoke() -> None:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"shot_concurrency_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Shot Concurrency Smoke",
        raw_script="镜头视频并发烟测。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    provider = ConcurrentShotVideoProvider(requested_concurrency=9)
    workflow = GenerationWorkflow(repo=repo, router=ShotRouter(provider))
    write_storyboard(workflow, project_dir, shot_count=7)
    await workflow.run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
    )

    require(len(provider.submissions) == 7, f"expected 7 shot submissions, got {len(provider.submissions)}")
    require(provider.max_submit_active == 5, f"shot submit concurrency should cap at 5, got {provider.max_submit_active}")
    require(provider.max_query_active == 5, f"shot query concurrency should cap at 5, got {provider.max_query_active}")


async def run_previous_video_serial_smoke() -> None:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"previous_video_serial_concurrency_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Previous Video Serial Concurrency Smoke",
        raw_script="上一镜视频引用模式串行烟测。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )

    provider = ConcurrentShotVideoProvider(requested_concurrency=5, reference_mode="previous_video")
    workflow = GenerationWorkflow(repo=repo, router=ShotRouter(provider))
    write_storyboard(workflow, project_dir, shot_count=4)
    await workflow.run(
        project_dir,
        until="shot_video_generation",
        only="shot_video_generation",
        episode_keys=parse_episode_keys("1"),
    )

    require(provider.max_submit_active == 1, f"previous_video submit mode must stay serial, got {provider.max_submit_active}")
    require(provider.max_query_active == 1, f"previous_video query mode must stay serial, got {provider.max_query_active}")
    require([item["shot_id"] for item in provider.submissions] == [
        "episode_001_shot_001",
        "episode_001_shot_002",
        "episode_001_shot_003",
        "episode_001_shot_004",
    ], provider.submissions)


async def main_async() -> int:
    await run_subject_concurrency_smoke()
    await run_shot_concurrency_smoke()
    await run_previous_video_serial_smoke()
    print("video_generation_concurrency_smoke=ok")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
