from __future__ import annotations

import asyncio
import base64
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import ProviderSettings, RuntimeSettings, Settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    Layout,
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    RoleIntroVideoPromptOutput,
    ScriptBundle,
    StaticAssetGenerationOutput,
    StoryboardShot,
)
from autodrama.providers.base import AssetRef, ImageGenerationResult  # noqa: E402
from autodrama.providers.volcengine.image.seedream import VolcengineSeedreamImageProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAFgwJ/luz7XwAAAABJRU5ErkJggg=="


class RecordingImageProvider:
    name = "recording_seedream"
    model = "doubao-seedream-5-0-260128"
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
        asset_id = str(metadata.get("asset_id") or f"asset_{len(self.calls)}")
        asset_url = f"https://seedream.example.invalid/{asset_id}.png"
        return ImageGenerationResult(
            provider=self.name,
            model=self.model,
            image_urls=[asset_url],
            image_data=[PNG_B64],
            request_id=f"request-{asset_id}",
            raw_response={"url": asset_url},
        )


class Router:
    def __init__(self, image_provider: RecordingImageProvider) -> None:
        self.image_provider = image_provider
        self.image_purposes: list[str] = []

    def image(self, purpose: str) -> RecordingImageProvider:
        self.image_purposes.append(purpose)
        return self.image_provider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def call_by_asset_id(provider: RecordingImageProvider, asset_id: str) -> dict[str, Any]:
    for call in provider.calls:
        if call["metadata"].get("asset_id") == asset_id:
            return call
    raise AssertionError(f"Missing image generation call for {asset_id}")


def first_ref_url(call: dict[str, Any]) -> str | None:
    refs = call["refs"]
    return refs[0].url if refs else None


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "static_image_url_refs"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    settings = Settings()
    settings.output.root_dir = tmp_root
    repo = ProjectRepository(settings)
    image_provider = RecordingImageProvider()
    workflow = PregenWorkflow(repo=repo, router=Router(image_provider))  # type: ignore[arg-type]
    project_dir = tmp_root / "project"

    role = Role(
        id="role_hero",
        name="Hero",
        intro="A steady protagonist.",
        episode_keys=["episode_001"],
    )
    role.appearances["base"] = RoleAppearance(
        id="role_hero_appearance_base",
        role_id=role.id,
        name="base",
        desc="stable hero look",
        prompt="draw the final role multiview sheet",
        full_body_prompt="draw the front full body reference",
        role_bound_prop_ids=["prop_role_sword"],
        intro_video_prompt="show the hero",
    )
    state = ProjectState(
        project_id="static-image-url-refs",
        title="Static Image URL Refs",
        raw_script="",
        script=ScriptBundle(raw_script="", novel_full={"episode_001": False}),
        roles={role.id: role},
        props={
            "prop_badge_normal": Prop(
                id="prop_badge_normal",
                name="Badge_normal",
                desc="normal badge",
                prompt="draw a clean badge",
                status="normal",
                episode_keys=["episode_001"],
            ),
            "prop_badge_damaged": Prop(
                id="prop_badge_damaged",
                name="Badge_damaged",
                desc="damaged badge",
                prompt="draw the damaged badge based on the normal badge",
                status="damaged",
                episode_keys=["episode_001"],
            ),
            "prop_role_sword": Prop(
                id="prop_role_sword",
                name="Hero sword",
                desc="hero-bound sword",
                prompt="draw the hero sword based on the role sheet",
                status="normal",
                episode_keys=["episode_001"],
                owner_role_id=role.id,
                owner_role_name=role.name,
                source="role_appearance_design",
            ),
        },
        layouts={
            "layout_room": Layout(
                id="layout_room",
                name="Interview Room",
                desc="quiet room",
                prompt="draw the interview room layout",
                episode_keys=["episode_001"],
            )
        },
    )

    state = await workflow._run_role_full_body_generation(project_dir, state)
    state = await workflow._run_role_multiview_generation(project_dir, state)
    state = await workflow._run_role_intro_video_prompt(project_dir, state)
    state = await workflow._run_prop_generation(project_dir, state)
    state = await workflow._run_layout_image_generation(project_dir, state)

    appearance = state.roles[role.id].appearances["base"]
    normal_prop = state.props["prop_badge_normal"]
    damaged_prop = state.props["prop_badge_damaged"]
    role_prop = state.props["prop_role_sword"]
    layout = state.layouts["layout_room"]

    require(appearance.full_body_image_asset_url is not None, "Role full body URL was not saved")
    require(appearance.design_image_asset_url is not None, "Role multiview URL was not saved")
    require(normal_prop.asset_url is not None, "Normal prop URL was not saved")
    require(damaged_prop.asset_url is not None, "Variant prop URL was not saved")
    require(role_prop.asset_url is not None, "Role-bound prop URL was not saved")
    require(layout.asset_url is not None, "Layout URL was not saved")

    multiview_call = call_by_asset_id(image_provider, "role_hero_appearance_base")
    require(
        first_ref_url(multiview_call) == appearance.full_body_image_asset_url,
        f"Role multiview did not reference the saved full-body URL: {multiview_call['refs']}",
    )
    damaged_call = call_by_asset_id(image_provider, "prop_badge_damaged")
    require(
        first_ref_url(damaged_call) == normal_prop.asset_url,
        f"Prop variant did not reference the saved normal prop URL: {damaged_call['refs']}",
    )
    role_prop_call = call_by_asset_id(image_provider, "prop_role_sword")
    require(
        first_ref_url(role_prop_call) == appearance.design_image_asset_url,
        f"Role-bound prop did not reference the saved role design URL: {role_prop_call['refs']}",
    )

    prompt_path = project_dir / "assets" / "json" / "nodes" / "role_intro_video_prompt.json"
    prompt_output = RoleIntroVideoPromptOutput.model_validate_json(prompt_path.read_text(encoding="utf-8"))
    require(
        prompt_output.prompts[0].reference_asset_url == appearance.design_image_asset_url,
        "Role intro video prompt did not persist the reference image URL",
    )

    shot = StoryboardShot(
        shot_id="episode_001_shot_001",
        index=1,
        layout_id=layout.id,
        title="Hero enters",
        duration_seconds=6,
        role_ids=[role.id],
        role_appearance_ids=[appearance.id],
        prop_ids=[damaged_prop.id, role_prop.id],
        ref_frame_prompt="Hero enters the room.",
        video_prompt="Hero enters the room.",
    )
    shot_refs = workflow._shot_ref_asset_refs(project_dir, state, shot)
    shot_ref_urls = {ref.metadata.get("asset_type"): ref.url for ref in shot_refs if ref.type == "image"}
    require(shot_ref_urls.get("layout") == layout.asset_url, f"Layout shot ref did not use URL: {shot_refs}")
    require(
        shot_ref_urls.get("role_appearance") == appearance.design_image_asset_url,
        f"Role shot ref did not use URL: {shot_refs}",
    )
    prop_urls = [ref.url for ref in shot_refs if ref.metadata.get("asset_type") == "prop"]
    require(damaged_prop.asset_url in prop_urls and role_prop.asset_url in prop_urls, f"Prop shot refs missed URLs: {shot_refs}")

    seedream = VolcengineSeedreamImageProvider(
        ProviderSettings(
            models={"seedream_5": "doubao-seedream-5-0-260128"},
            options={
                "seedream_image_size": "2K",
                "seedream_prop_size": "2048x2048",
            },
        ),
        RuntimeSettings(),
    )
    payload = seedream.build_payload(
        "draw a damaged badge",
        refs=[
            AssetRef(
                id=normal_prop.asset_id or normal_prop.id,
                type="image",
                path=str(project_dir / str(normal_prop.asset_path)),
                url=normal_prop.asset_url,
            )
        ],
        metadata={"node_name": "prop_generation"},
    )
    require(payload["image"] == normal_prop.asset_url, f"Seedream did not prefer ref.url: {payload}")
    require(payload["size"] == "2048x2048", f"Seedream did not apply prop size: {payload['size']}")

    full_body_output_path = project_dir / "assets" / "json" / "nodes" / "role_full_body_generation.json"
    role_output_path = project_dir / "assets" / "json" / "nodes" / "role_multiview_generation.json"
    prop_output_path = project_dir / "assets" / "json" / "nodes" / "prop_generation.json"
    layout_output_path = project_dir / "assets" / "json" / "nodes" / "layout_image_generation.json"
    for output_path in (full_body_output_path, role_output_path, prop_output_path, layout_output_path):
        output = StaticAssetGenerationOutput.model_validate_json(output_path.read_text(encoding="utf-8"))
        require(
            all(item.asset_url for item in output.generated_assets),
            f"Node output missed asset_url: {output_path}",
        )

    summary_path = tmp_root / "summary.json"
    summary_path.write_text(
        json.dumps(
            {
                "role_full_body_url": appearance.full_body_image_asset_url,
                "role_multiview_url": appearance.design_image_asset_url,
                "normal_prop_url": normal_prop.asset_url,
                "damaged_prop_url": damaged_prop.asset_url,
                "role_prop_url": role_prop.asset_url,
                "layout_url": layout.asset_url,
                "seedream_reference": payload["image"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("static_image_url_refs_smoke=ok")
    print(f"summary_path={summary_path}")
    print(f"image_calls={len(image_provider.calls)}")
    print(f"seedream_reference={payload['image']}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
