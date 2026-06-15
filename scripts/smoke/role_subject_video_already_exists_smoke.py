from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.core.schemas import Role, RoleAppearance, RoleSubjectVideoIntroTextOutput  # noqa: E402
from autodrama.providers.base import AssetRef, VideoGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class AlreadyExistsVideoProvider:
    name = "kling_omni"
    model = "kling-v3-omni"
    supports_subject_elements = True
    _TERMINAL_SUCCESS = {"succeed"}
    _TERMINAL_FAILURE = {"failed"}

    def __init__(self) -> None:
        self.settings = SimpleNamespace(options={"subject_video_duration_seconds": 5})
        self.max_polls = 1
        self.poll_interval_seconds = 0
        self.generate_count = 0
        self.query_count = 0
        self.external_task_id: str | None = None
        self.prompt: str | None = None
        self.metadata: dict[str, Any] = {}

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        del refs, duration, wait
        self.generate_count += 1
        self.prompt = prompt
        self.metadata = dict(metadata or {})
        self.external_task_id = str(self.metadata.get("external_task_id") or "")
        raise ProviderBadResponseError(
            "Kling omni video submit failed with HTTP 400: "
            f'{{"code":1201,"message":"External_task_id {self.external_task_id} already exists"}}'
        )

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        self.query_count += 1
        require(task_id == self.external_task_id, f"expected external task query, got {task_id}")
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id="system-task-from-external-id",
            task_status="succeed",
            video_url="https://example.invalid/existing-kling-subject.mp4",
            request_id="request-existing",
            raw_response={
                "data": {
                    "task_id": "system-task-from-external-id",
                    "task_status": "succeed",
                    "task_info": {"external_task_id": task_id},
                    "video_url": "https://example.invalid/existing-kling-subject.mp4",
                }
            },
        )


class AlreadyExistsTextProvider:
    name = "fake-text"
    model = "fake-gpt"

    def __init__(self) -> None:
        self.generate_count = 0

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
        self.generate_count += 1
        require(schema is RoleSubjectVideoIntroTextOutput, f"unexpected schema: {schema}")
        return RoleSubjectVideoIntroTextOutput(intro_text="我是林舟，我会守住真相。")


class AlreadyExistsRouter:
    def __init__(self, video_provider: AlreadyExistsVideoProvider, text_provider: AlreadyExistsTextProvider) -> None:
        self.video_provider = video_provider
        self.text_provider = text_provider

    def video(self, purpose: str, *, node_name: str | None = None) -> AlreadyExistsVideoProvider:
        del purpose, node_name
        return self.video_provider

    def text(self, purpose: str, *, node_name: str | None = None) -> AlreadyExistsTextProvider:
        del purpose
        require(node_name == "role_subject_video_intro_text", f"unexpected text node: {node_name}")
        return self.text_provider


class AlreadyExistsMediaStore:
    def __init__(self) -> None:
        self.write_count = 0

    async def write_generated_video(
        self,
        project_dir: Path,
        output_path: Path,
        result: VideoGenerationResult,
    ) -> str:
        self.write_count += 1
        require(
            result.video_url == "https://example.invalid/existing-kling-subject.mp4",
            "unexpected recovered video URL",
        )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"existing kling subject video")
        return str(output_path.relative_to(project_dir)).replace("\\", "/")


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"role_subject_video_already_exists_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Subject Video Already Exists Smoke",
        raw_script="Lin Zhou has an existing Kling external task.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    appearance = RoleAppearance(
        id="role_lin_zhou_base",
        role_id="role_lin_zhou",
        name="base",
        asset_url="https://example.invalid/roleboard.png",
    )
    role = Role(
        id="role_lin_zhou",
        name="Lin Zhou",
        intro="Office worker",
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

    provider = AlreadyExistsVideoProvider()
    text_provider = AlreadyExistsTextProvider()
    media_store = AlreadyExistsMediaStore()
    workflow = PregenWorkflow(repo=repo, router=AlreadyExistsRouter(provider, text_provider))
    workflow.media_store = media_store

    await workflow.run(project_dir, only="role_subject_video_generation")

    restored_state = repo.load_state(project_dir)
    restored_appearance = restored_state.roles[role.id].appearances[appearance.id]
    require(text_provider.generate_count == 1, f"expected one intro text generation, got {text_provider.generate_count}")
    require(provider.generate_count == 1, f"expected one submit attempt, got {provider.generate_count}")
    require(provider.query_count == 1, f"expected one external task query, got {provider.query_count}")
    require(media_store.write_count == 1, f"expected one video write, got {media_store.write_count}")
    require("我是林舟，我会守住真相。" in (provider.prompt or ""), "intro text missing from video prompt")
    require(provider.metadata.get("sound") == "on", "video metadata sound must be on")
    require(provider.metadata.get("parameters", {}).get("sound") == "on", "video payload sound override must be on")
    require(
        str(provider.external_task_id or "").endswith("_voiced"),
        f"voiced external_task_id missing suffix: {provider.external_task_id}",
    )
    require(
        restored_appearance.subject_video_asset_path
        == "assets/videos/roles/role_lin_zhou_base_subject_video.mp4",
        "restored subject video path mismatch",
    )
    require(
        (project_dir / restored_appearance.subject_video_asset_path).exists(),
        "restored subject video file missing",
    )
    require(
        restored_appearance.subject_video_asset_url == "https://example.invalid/existing-kling-subject.mp4",
        "subject video URL was not saved",
    )
    require(
        restored_appearance.subject_video_intro_text == "我是林舟，我会守住真相。",
        "intro text was not saved",
    )
    require(
        restored_appearance.subject_video_task_id == "system-task-from-external-id",
        "system task id was not saved",
    )

    node_output_path = project_dir / "assets" / "json" / "nodes" / "role_subject_video_generation.json"
    node_output = json.loads(node_output_path.read_text(encoding="utf-8"))
    generated = node_output["generated_subject_videos"]
    require(len(generated) == 1, f"expected one recovered subject video item, got {len(generated)}")
    require(generated[0]["asset_url"] == restored_appearance.subject_video_asset_url, "node output URL mismatch")
    require(generated[0]["intro_text"] == restored_appearance.subject_video_intro_text, "node output intro mismatch")

    print("role_subject_video_already_exists_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"external_task_id={provider.external_task_id}")
    print(f"asset_path={restored_appearance.subject_video_asset_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
