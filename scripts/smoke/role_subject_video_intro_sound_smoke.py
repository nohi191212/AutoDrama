from __future__ import annotations

import asyncio
import base64
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
from autodrama.core.schemas import Role, RoleAppearance, RoleSubjectVideoIntroTextOutput  # noqa: E402
from autodrama.providers.base import AssetRef, VideoGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class IntroTextProvider:
    name = "fake-gpt"
    model = "fake-gpt-model"

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
        return RoleSubjectVideoIntroTextOutput(intro_text="林舟：我是林舟，我会守住真相。")


class SoundVideoProvider:
    name = "kling_omni"
    model = "kling-v3-omni"
    supports_subject_elements = True

    def __init__(self) -> None:
        self.settings = SimpleNamespace(options={"subject_video_duration_seconds": 5})
        self.generate_count = 0
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
        video_bytes = b"voiced subject video"
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id="voiced-task",
            task_status="succeed",
            video_data=base64.b64encode(video_bytes).decode("ascii"),
            request_id="voiced-request",
            raw_response={"task_id": "voiced-task", "task_status": "succeed"},
        )


class IntroSoundRouter:
    def __init__(self, video_provider: SoundVideoProvider, text_provider: IntroTextProvider) -> None:
        self.video_provider = video_provider
        self.text_provider = text_provider

    def video(self, purpose: str, *, node_name: str | None = None) -> SoundVideoProvider:
        del purpose, node_name
        return self.video_provider

    def text(self, purpose: str, *, node_name: str | None = None) -> IntroTextProvider:
        del purpose
        require(node_name == "role_subject_video_intro_text", f"unexpected text node: {node_name}")
        return self.text_provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"role_subject_video_intro_sound_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Subject Video Intro Sound Smoke",
        raw_script="Lin Zhou records a voiced subject video.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    roleboard_path = project_dir / "assets" / "images" / "roles" / "role_lin_zhou_base.png"
    roleboard_path.parent.mkdir(parents=True, exist_ok=True)
    roleboard_path.write_bytes(b"fake roleboard")

    appearance = RoleAppearance(
        id="role_lin_zhou_base",
        role_id="role_lin_zhou",
        name="base",
        asset_path=str(roleboard_path.relative_to(project_dir)).replace("\\", "/"),
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

    text_provider = IntroTextProvider()
    video_provider = SoundVideoProvider()
    workflow = PregenWorkflow(repo=repo, router=IntroSoundRouter(video_provider, text_provider))

    await workflow.run(project_dir, only="role_subject_video_generation")

    restored_state = repo.load_state(project_dir)
    restored_appearance = restored_state.roles[role.id].appearances[appearance.id]
    require(text_provider.generate_count == 1, "intro text provider should be called once")
    require(video_provider.generate_count == 1, "video provider should be called once")
    require(
        restored_appearance.subject_video_intro_text == "我是林舟，我会守住真相。",
        "intro text should be cleaned and saved",
    )
    require("我是林舟，我会守住真相。" in (video_provider.prompt or ""), "intro text missing from video prompt")
    require("视频必须带角色本人自然口播声音" in (video_provider.prompt or ""), "sound requirement missing")
    require(video_provider.metadata.get("sound") == "on", "video metadata sound must be on")
    require(video_provider.metadata.get("parameters", {}).get("sound") == "on", "payload sound override must be on")
    require(str(video_provider.metadata.get("external_task_id") or "").endswith("_voiced"), "missing voiced task id")
    require(
        (project_dir / str(restored_appearance.subject_video_asset_path)).exists(),
        "voiced subject video file missing",
    )

    node_output_path = project_dir / "assets" / "json" / "nodes" / "role_subject_video_generation.json"
    node_output = json.loads(node_output_path.read_text(encoding="utf-8"))
    generated = node_output["generated_subject_videos"]
    require(generated[0]["intro_text"] == restored_appearance.subject_video_intro_text, "node output intro mismatch")
    require("我是林舟，我会守住真相。" in generated[0]["prompt"], "node output prompt missing intro")

    print("role_subject_video_intro_sound_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"asset_path={restored_appearance.subject_video_asset_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
