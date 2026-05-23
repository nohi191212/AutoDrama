from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import RoleAppearanceDesignItem, RoleDesignItem, RoleExtractItem  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.repositories.role_design_repo import RoleDesignRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    settings = Settings()
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = "functional_role_asset_policy_smoke"
    project_dir = settings.output.root_dir / project_id
    if project_dir.exists():
        shutil.rmtree(project_dir)

    state = repo.create_project(
        title="Functional Role Asset Policy Smoke",
        raw_script="山门守卫持令牌阻拦沈烬入山，没有单独对白。",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=5,
    )
    state = repo.load_state(project_dir)

    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    role_designs = RoleDesignRepository(repo, repo.layout)

    extract_item = RoleExtractItem(
        name="山门守卫",
        role_tier="functional",
        aliases=["守山弟子"],
        episode_keys=["episode_001"],
        source_chapters=["第1章"],
        brief="守在青霄宗山门前的无名弟子，持令牌阻拦沈烬入山。",
        appearance_notes=["灰袍", "佩剑", "腰悬山门令牌"],
        has_dialogue=False,
        visual_reuse_required=False,
    )
    repo.save_node_output(project_dir, "role_extract", {"roles": [extract_item.model_dump(mode="json")]})
    state.metadata["role_refs"] = {
        extract_item.name: role_designs.save_extract_item(project_dir, extract_item)
    }

    design_item = RoleDesignItem(
        name="山门守卫",
        intro="山门守卫是青霄宗山门前短暂出现的守山弟子，负责持令牌阻拦入山。",
        personality="守规、谨慎",
        aliases=["守山弟子"],
        role_tier="functional",
        has_dialogue=False,
        visual_reuse_required=False,
        episode_keys=["episode_001"],
        source_chapters=["第1章"],
        appearances=[
            RoleAppearanceDesignItem(
                role_name="山门守卫",
                name="base",
                desc="灰袍青年弟子，腰悬山门令牌，佩剑，神情谨慎。",
                full_body_prompt=(
                    "真人电影质感，单人正面全身照，灰袍青年守山弟子从头到脚完整入画，"
                    "腰悬山门令牌，佩剑，身体重心自然，干净背景，无字幕水印。"
                ),
                prompt=(
                    "真人电影质感，左侧为同一灰袍青年守山弟子正面、侧面、背面三视图，"
                    "统一身高比例和服装细节，腰悬山门令牌，佩剑；右侧为山门令牌设计图，"
                    "与左侧人物保持同一比例尺，干净背景，无字幕水印。"
                ),
                role_bound_props=[],
                intro_video_prompt=None,
            )
        ],
        voices=[],
    )

    design_path = role_designs.item_relative_path_for_name(project_dir, design_item.name)
    workflow._apply_role_design_item(project_dir, state, design_item, design_path=design_path)
    role = state.roles["role_山门守卫"]
    require(role.role_tier == "functional", "role_tier was not copied to state role")
    require(not role.has_dialogue, "has_dialogue should be false")
    require(role.audio == {}, "functional role without dialogue should not have audio designs")

    role_designs.save_design_item(
        project_dir,
        extract_item=extract_item,
        design_item=design_item,
        role=role,
        bound_props=[],
    )
    repo.save_state(project_dir, state)

    state = await workflow._static_asset_node_runner("role_full_body_generation").run(project_dir, state)
    role = state.roles["role_山门守卫"]
    appearance = role.appearances["base"]
    require(appearance.full_body_image_asset_path, "functional role full-body image should be generated")
    full_body_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "role_full_body_generation.json").read_text(encoding="utf-8")
    )
    full_body_asset_types = [item["asset_type"] for item in full_body_output["generated_assets"]]
    require(full_body_asset_types == ["role_full_body"], "functional role should emit one full-body asset")

    state = await workflow._static_asset_node_runner("role_multiview_generation").run(project_dir, state)
    role = state.roles["role_山门守卫"]
    appearance = role.appearances["base"]
    require(appearance.design_image_asset_path, "functional role multiview image should be generated")
    multiview_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "role_multiview_generation.json").read_text(encoding="utf-8")
    )
    multiview_asset_types = [item["asset_type"] for item in multiview_output["generated_assets"]]
    require(multiview_asset_types == ["role_multiview"], "functional role should emit one multiview asset")

    state = await workflow._static_asset_node_runner("role_intro_video_generation").run(project_dir, state)
    role = state.roles["role_山门守卫"]
    appearance = role.appearances["base"]
    require(appearance.intro_video_generation_status == "skipped", "functional intro video should be skipped")
    require(appearance.intro_video_asset_path is None, "functional intro video path should remain empty")
    intro_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "role_intro_video_generation.json").read_text(encoding="utf-8")
    )
    require(intro_output["generated_assets"] == [], "functional role should not emit intro video asset")

    state = await workflow._voice_node_runner("role_voice_generation").run(project_dir, state)
    voice_output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "role_voice_generation.json").read_text(encoding="utf-8")
    )
    require(voice_output["generated_voices"] == [], "functional role without dialogue should not generate voices")

    print("functional_role_asset_policy_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
