from __future__ import annotations

import asyncio
import base64
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, RoleAppearance, ScriptBundle  # noqa: E402
from autodrama.providers.base import ImageGenerationResult, VideoGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class RecordingImageProvider:
    name = "recording_image"
    model = "recording-image-model"
    supports_reference_images = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_image(self, prompt: str, refs=None, *, size=None, metadata=None):
        metadata = metadata or {}
        refs = refs or []
        self.calls.append(
            {
                "prompt": prompt,
                "refs": refs,
                "size": size,
                "metadata": metadata,
            }
        )
        asset_id = str(metadata.get("asset_id") or "asset")
        return ImageGenerationResult(
            provider=self.name,
            model=self.model,
            image_data=[base64.b64encode(f"image:{asset_id}".encode("utf-8")).decode("ascii")],
            request_id=f"image-request-{asset_id}",
            raw_response={"asset_id": asset_id},
        )


class RecordingVideoProvider:
    name = "recording_video"
    model = "recording-video-model"

    async def generate_video(self, prompt: str, refs=None, *, duration=None, wait=False, metadata=None):
        metadata = metadata or {}
        asset_id = str(metadata.get("asset_id") or "video")
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            video_data=base64.b64encode(f"video:{asset_id}".encode("utf-8")).decode("ascii"),
            request_id=f"video-request-{asset_id}",
            raw_response={"asset_id": asset_id, "duration": duration, "wait": wait},
        )


class Router:
    def __init__(self, image_provider: RecordingImageProvider) -> None:
        self.image_provider = image_provider
        self.video_provider = RecordingVideoProvider()

    def image(self, _purpose: str) -> RecordingImageProvider:
        return self.image_provider

    def video(self, _purpose: str) -> RecordingVideoProvider:
        return self.video_provider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "role_appearance_portrait_sequence"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    settings = Settings()
    settings.output.root_dir = tmp_root
    repo = ProjectRepository(settings)
    image_provider = RecordingImageProvider()
    workflow = PregenWorkflow(repo=repo, router=Router(image_provider))  # type: ignore[arg-type]
    project_dir = tmp_root / "project"

    role = Role(id="role_hero", name="Hero", intro="Hero intro")
    role.appearances["base"] = RoleAppearance(
        id="role_hero_appearance_base",
        role_id=role.id,
        name="base",
        desc="stable hero look",
        prompt="draw the original role design sheet",
        portrait_prompt="draw the close portrait",
        intro_video_prompt="show the hero",
    )
    state = ProjectState(
        project_id="role-appearance-portrait-sequence",
        title="Role Appearance Portrait Sequence",
        raw_script="",
        script=ScriptBundle(raw_script=""),
        roles={role.id: role},
    )

    state = await workflow._run_role_appearance_generation(project_dir, state)
    appearance = state.roles[role.id].appearances["base"]

    require(len(image_provider.calls) == 2, f"Expected portrait and design image calls, got {len(image_provider.calls)}")
    require(
        image_provider.calls[0]["metadata"]["asset_id"] == "role_hero_appearance_base_portrait",
        f"Portrait should be generated first: {image_provider.calls}",
    )
    require(
        image_provider.calls[1]["metadata"]["asset_id"] == "role_hero_appearance_base",
        f"Design sheet should be generated second: {image_provider.calls}",
    )
    design_refs = image_provider.calls[1]["refs"]
    require(len(design_refs) == 1, "Design sheet generation should receive the portrait as one reference")
    require(
        Path(str(design_refs[0].path)).name == "role_hero_appearance_base_portrait.png",
        f"Unexpected design reference path: {design_refs[0].path}",
    )
    require(
        appearance.portrait_image_asset_path == "assets/images/roles/role_hero_appearance_base_portrait.png",
        f"Unexpected portrait path: {appearance.portrait_image_asset_path}",
    )
    require(
        appearance.design_image_asset_path == "assets/images/roles/role_hero_appearance_base.png",
        f"Unexpected design path: {appearance.design_image_asset_path}",
    )

    output_path = project_dir / "assets" / "json" / "nodes" / "role_appearance_generation.json"
    require(output_path.is_file(), "role_appearance_generation node output was not written")
    print("role_appearance_portrait_sequence_smoke=ok")
    print("image_call_order=portrait,design")
    print(f"portrait_path={appearance.portrait_image_asset_path}")
    print(f"design_path={appearance.design_image_asset_path}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
