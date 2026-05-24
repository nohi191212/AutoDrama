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
from autodrama.core.schemas import (  # noqa: E402
    RoleAppearanceDesignItem,
    RoleDesignItem,
    RoleDesignOutput,
    RoleExtractItem,
    RoleVoiceItem,
)
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def design_item(name: str, episode_key: str) -> RoleDesignItem:
    return RoleDesignItem(
        name=name,
        intro=f"{name} intro",
        role_tier="primary",
        has_dialogue=True,
        visual_reuse_required=True,
        episode_keys=[episode_key],
        source_chapters=["第1章"],
        appearances=[
            RoleAppearanceDesignItem(
                role_name=name,
                name="base",
                desc=f"{name} stable look",
                full_body_prompt=f"{name} front full body natural display",
                prompt=f"{name} front side back multiview with bound prop sheet",
                intro_video_prompt=f"{name} intro video prompt",
            )
        ],
        voices=[
            RoleVoiceItem(
                role_name=name,
                emotion="normal",
                desc=f"{name} normal voice",
                sample_text=f"我是{name}，这是我的前期自我介绍。",
            )
        ],
    )


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "role_generation_episode_scoping"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    settings = Settings()
    settings.output.root_dir = tmp_root
    settings.project.episode_count = 2
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    project_dir = repo.create_project(
        title="Role Generation Episode Scoping",
        raw_script="Alpha appears in episode one. Beta appears in episode two.",
        project_id="role_generation_episode_scoping",
        episode_count=2,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)
    state.script.novel_full = {
        "episode_001": "Alpha episode full text.",
        "episode_002": "Beta episode full text.",
    }
    state.script.novel_extract = {
        "episode_001": "Alpha appears.",
        "episode_002": "Beta appears.",
    }

    for name, episode_key in (("Alpha", "episode_001"), ("Beta", "episode_002")):
        extract_item = RoleExtractItem(
            name=name,
            role_tier="primary",
            has_dialogue=True,
            visual_reuse_required=True,
            episode_keys=[episode_key],
            source_chapters=["第1章"],
        )
        item = design_item(name, episode_key)
        design_path = workflow.role_designs.item_relative_path_for_name(project_dir, name)
        workflow._apply_role_design_item(project_dir, state, item, design_path=design_path)
        role = state.roles[f"role_{name.lower()}"]
        workflow.role_designs.save_design_item(
            project_dir,
            extract_item=extract_item,
            design_item=item,
            role=role,
            bound_props=[],
        )
    repo.save_node_output(
        project_dir,
        "role_design",
        RoleDesignOutput(roles=[design_item("Alpha", "episode_001"), design_item("Beta", "episode_002")]),
    )
    repo.save_state(project_dir, state)

    await workflow.run(project_dir, only="role_voice_generation", episode_keys=["episode_002"], force=True)
    voice_output = json.loads((project_dir / "assets" / "json" / "nodes" / "role_voice_generation.json").read_text(encoding="utf-8"))
    require(
        {item["role_name"] for item in voice_output["generated_voices"]} == {"Beta"},
        f"role_voice_generation did not scope to Beta: {voice_output}",
    )

    await workflow.run(project_dir, only="role_full_body_generation", episode_keys=["episode_002"], force=True)
    full_body_output = json.loads((project_dir / "assets" / "json" / "nodes" / "role_full_body_generation.json").read_text(encoding="utf-8"))
    require(
        {item["owner_id"] for item in full_body_output["generated_assets"]} == {"role_beta"},
        f"role_full_body_generation did not scope to Beta: {full_body_output}",
    )

    await workflow.run(project_dir, only="role_multiview_generation", episode_keys=["episode_002"], force=True)
    multiview_output = json.loads((project_dir / "assets" / "json" / "nodes" / "role_multiview_generation.json").read_text(encoding="utf-8"))
    require(
        {item["owner_id"] for item in multiview_output["generated_assets"]} == {"role_beta"},
        f"role_multiview_generation did not scope to Beta: {multiview_output}",
    )

    await workflow.run(project_dir, only="role_intro_video_prompt", episode_keys=["episode_002"], force=True)
    intro_prompt_output = json.loads((project_dir / "assets" / "json" / "nodes" / "role_intro_video_prompt.json").read_text(encoding="utf-8"))
    require(
        {item["role_id"] for item in intro_prompt_output["prompts"]} == {"role_beta"},
        f"role_intro_video_prompt did not scope to Beta: {intro_prompt_output}",
    )

    await workflow.run(project_dir, only="role_intro_video_generation", episode_keys=["episode_002"], force=True)
    intro_output = json.loads((project_dir / "assets" / "json" / "nodes" / "role_intro_video_generation.json").read_text(encoding="utf-8"))
    require(
        {item["owner_id"] for item in intro_output["generated_assets"]} == {"role_beta"},
        f"role_intro_video_generation did not scope to Beta: {intro_output}",
    )

    print("role_generation_episode_scoping_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
