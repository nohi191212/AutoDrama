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
from autodrama.providers.base import ImageGenerationResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="


class RecordingImageProvider:
    name = "recording_image"
    model = "recording-image-model"
    supports_reference_images = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_image(self, prompt: str, refs=None, *, size=None, metadata=None):
        metadata = metadata or {}
        refs = list(refs or [])
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
            image_data=[PNG_B64],
            image_urls=[f"https://example.invalid/{asset_id}.png"],
            request_id=f"image-request-{asset_id}",
            raw_response={"asset_id": asset_id},
        )


class Router:
    def __init__(self, image_provider: RecordingImageProvider) -> None:
        self.image_provider = image_provider

    def image(self, _purpose: str) -> RecordingImageProvider:
        return self.image_provider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def call_by_asset_id(provider: RecordingImageProvider, asset_id: str) -> dict[str, Any]:
    for call in provider.calls:
        if call["metadata"].get("asset_id") == asset_id:
            return call
    raise AssertionError(f"Missing image generation call for {asset_id}")


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "role_style_reference_generation"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    style_ref_dir = tmp_root / "style_refs"
    style_ref_dir.mkdir(parents=True, exist_ok=True)
    for name in ("style_a.png", "style_b.png"):
        (style_ref_dir / name).write_bytes(base64.b64decode(PNG_B64))

    settings = Settings()
    settings.output.root_dir = tmp_root
    settings.generation.role_design_style_reference_dir = style_ref_dir
    settings.generation.role_design_style_prompt = "统一国漫CG风格，明显非真人摄影，参考样例图的光影和材质。"

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
        prompt="draw the original role multiview sheet",
        full_body_prompt="draw the front full body reference",
        intro_video_prompt="show the hero",
    )
    state = ProjectState(
        project_id="role-style-reference-generation",
        title="Role Style Reference Generation",
        raw_script="",
        script=ScriptBundle(raw_script=""),
        roles={role.id: role},
    )

    state = await workflow._run_role_full_body_generation(project_dir, state)
    state = await workflow._run_role_multiview_generation(project_dir, state)

    full_body_call = call_by_asset_id(image_provider, "role_hero_appearance_base_full_body")
    multiview_call = call_by_asset_id(image_provider, "role_hero_appearance_base")
    full_body_refs = full_body_call["refs"]
    multiview_refs = multiview_call["refs"]

    require(len(full_body_refs) == 2, f"Full body should receive two style refs: {full_body_refs}")
    require(
        [Path(str(ref.path)).name for ref in full_body_refs] == ["style_a.png", "style_b.png"],
        f"Unexpected full body style refs: {full_body_refs}",
    )
    require(
        "统一国漫CG风格" in full_body_call["prompt"],
        f"Full body prompt missed unified style prompt: {full_body_call['prompt']}",
    )
    require(len(multiview_refs) == 3, f"Multiview should receive two style refs plus full body ref: {multiview_refs}")
    require(
        [Path(str(ref.path)).name for ref in multiview_refs[:2]] == ["style_a.png", "style_b.png"],
        f"Unexpected multiview style refs: {multiview_refs}",
    )
    require(
        Path(str(multiview_refs[2].path)).name == "role_hero_appearance_base_full_body.png",
        f"Unexpected multiview identity ref: {multiview_refs[2]}",
    )
    require(
        "参考图片3是同一角色的全身身份参考" in multiview_call["prompt"],
        f"Multiview prompt missed identity ref instruction: {multiview_call['prompt']}",
    )
    require(
        multiview_call["metadata"]["style_reference_count"] == 2,
        f"Unexpected style ref count metadata: {multiview_call['metadata']}",
    )

    print("role_style_reference_generation_smoke=ok")
    print("full_body_refs=style_a.png,style_b.png")
    print("multiview_refs=style_a.png,style_b.png,full_body")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
