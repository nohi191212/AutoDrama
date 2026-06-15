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
from autodrama.core.schemas import Role, RoleAppearance  # noqa: E402
from autodrama.providers.base import AssetRef, VideoGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RestoreVideoProvider:
    name = "restore-video"
    model = "restore-video-model"
    supports_subject_elements = True

    def __init__(self) -> None:
        self.settings = SimpleNamespace(options={"subject_video_duration_seconds": 5})
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
        del prompt, refs, duration, wait, metadata
        self.generate_count += 1
        raise AssertionError("Existing subject video URL should be restored without regeneration")


class RestoreRouter:
    def __init__(self, provider: RestoreVideoProvider) -> None:
        self.provider = provider

    def video(self, purpose: str, *, node_name: str | None = None) -> RestoreVideoProvider:
        del purpose, node_name
        return self.provider


class RestoreMediaStore:
    def __init__(self) -> None:
        self.write_count = 0
        self.urls: list[str] = []

    async def write_generated_video(
        self,
        project_dir: Path,
        output_path: Path,
        result: VideoGenerationResult,
    ) -> str:
        self.write_count += 1
        require(result.video_url == "https://example.invalid/restorable-subject.mp4", "unexpected restored video URL")
        self.urls.append(str(result.video_url))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"restored subject video")
        return str(output_path.relative_to(project_dir)).replace("\\", "/")


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"role_subject_video_restore_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Subject Video Restore Smoke",
        raw_script="Lin Zhou needs a reusable subject video.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    appearance = RoleAppearance(
        id="role_lin_zhou_base",
        role_id="role_lin_zhou",
        name="base",
        asset_path="assets/images/roles/role_lin_zhou_base.png",
        subject_video_asset_id="subject_video_role_lin_zhou_base",
        subject_video_asset_path="assets/videos/roles/subject_video_role_lin_zhou_base.mp4",
        subject_video_asset_url="https://example.invalid/restorable-subject.mp4",
        subject_video_provider="kling",
        subject_video_model="kling-v3-omni",
        subject_video_task_id="task-restorable",
        subject_video_task_status="succeeded",
        subject_video_request_id="request-restorable",
        subject_video_usage={"credits": 1},
        subject_video_raw_response={"video_url": "https://example.invalid/restorable-subject.mp4"},
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

    provider = RestoreVideoProvider()
    media_store = RestoreMediaStore()
    workflow = PregenWorkflow(repo=repo, router=RestoreRouter(provider))
    workflow.media_store = media_store

    missing_path = project_dir / appearance.subject_video_asset_path
    require(not missing_path.exists(), "smoke fixture should start without the local subject video")

    await workflow.run(project_dir, only="role_subject_video_generation")

    restored_state = repo.load_state(project_dir)
    restored_appearance = restored_state.roles[role.id].appearances[appearance.id]
    restored_path = project_dir / restored_appearance.subject_video_asset_path
    require(restored_path.exists(), "subject video was not restored locally")
    require(restored_path.read_bytes() == b"restored subject video", "restored subject video content mismatch")
    require(media_store.write_count == 1, f"expected one restore write, got {media_store.write_count}")
    require(provider.generate_count == 0, "provider.generate_video should not be called during restore")
    require(
        restored_appearance.subject_video_asset_path == "assets/videos/roles/subject_video_role_lin_zhou_base.mp4",
        "appearance subject video path was not retained",
    )

    await workflow.run(project_dir, only="role_subject_video_generation")
    require(media_store.write_count == 1, "second non-force run should reuse the restored local video")
    restored_state = repo.load_state(project_dir)
    restored_appearance = restored_state.roles[role.id].appearances[appearance.id]

    node_output_path = project_dir / "assets" / "json" / "nodes" / "role_subject_video_generation.json"
    node_output = json.loads(node_output_path.read_text(encoding="utf-8"))
    generated = node_output["generated_subject_videos"]
    require(len(generated) == 1, f"expected one generated/restored subject video item, got {len(generated)}")
    require(
        generated[0]["asset_path"] == restored_appearance.subject_video_asset_path,
        "node output asset_path mismatch",
    )

    print("role_subject_video_restore_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"asset_path={restored_appearance.subject_video_asset_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
