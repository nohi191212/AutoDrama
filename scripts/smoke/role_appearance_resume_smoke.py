from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT_DIR / "autodrama" / "src"))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, RoleAppearance, ScriptBundle  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class NoCallImageProvider:
    name = "no_call_image"
    model = "no_call_image_model"

    async def generate_image(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Existing role appearance image should be reused")


class NoCallVideoProvider:
    name = "no_call_video"
    model = "no_call_video_model"

    async def generate_video(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Existing role appearance intro video should be reused")


class NoCallRouter:
    def image(self, _purpose: str) -> NoCallImageProvider:
        return NoCallImageProvider()

    def video(self, _purpose: str) -> NoCallVideoProvider:
        return NoCallVideoProvider()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main() -> None:
    tmp_root = ROOT_DIR / ".tmp" / "role_appearance_resume_smoke"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    project_dir = tmp_root / "project"
    portrait_path = project_dir / "assets" / "images" / "roles" / "role_hero_appearance_base_portrait.png"
    image_path = project_dir / "assets" / "images" / "roles" / "role_hero_appearance_base.png"
    video_path = project_dir / "assets" / "videos" / "roles" / "role_hero_appearance_base_intro_video.mp4"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    video_path.parent.mkdir(parents=True, exist_ok=True)
    portrait_path.write_bytes(b"fake portrait")
    image_path.write_bytes(b"fake image")
    video_path.write_bytes(b"fake video")

    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = tmp_root
    repo = ProjectRepository(settings)
    workflow = PregenWorkflow(repo=repo, router=NoCallRouter())  # type: ignore[arg-type]

    role = Role(id="role_hero", name="Hero", intro="Hero intro")
    role.appearances["base"] = RoleAppearance(
        id="role_hero_appearance_base",
        role_id=role.id,
        name="base",
        desc="stable hero look",
        prompt="draw the hero",
        intro_video_prompt="show the hero",
    )
    state = ProjectState(
        project_id="role-appearance-resume-smoke",
        title="Role Appearance Resume Smoke",
        raw_script="",
        script=ScriptBundle(raw_script=""),
        roles={role.id: role},
    )

    state = await workflow._run_role_appearance_generation(project_dir, state)
    appearance = state.roles[role.id].appearances["base"]
    require(
        appearance.portrait_image_asset_path == "assets/images/roles/role_hero_appearance_base_portrait.png",
        f"Unexpected portrait path: {appearance.portrait_image_asset_path}",
    )
    require(
        appearance.design_image_asset_path == "assets/images/roles/role_hero_appearance_base.png",
        f"Unexpected image path: {appearance.design_image_asset_path}",
    )
    require(
        appearance.intro_video_asset_path == "assets/videos/roles/role_hero_appearance_base_intro_video.mp4",
        f"Unexpected video path: {appearance.intro_video_asset_path}",
    )
    output_path = project_dir / "assets" / "json" / "nodes" / "role_appearance_generation.json"
    require(output_path.is_file(), "role_appearance_generation node output was not written")
    print("role_appearance_resume_smoke=ok")


if __name__ == "__main__":
    asyncio.run(main())
