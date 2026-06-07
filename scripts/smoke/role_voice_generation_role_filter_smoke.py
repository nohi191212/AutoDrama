from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any


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


class CountingVoiceProvider:
    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.create_voice_calls: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(self.inner, name)

    async def create_voice(self, **kwargs: Any) -> Any:
        self.create_voice_calls.append(dict(kwargs))
        return await self.inner.create_voice(**kwargs)


def design_item(name: str) -> RoleDesignItem:
    return RoleDesignItem(
        name=name,
        intro=f"{name} 是主角身边的重要角色。",
        role_tier="primary",
        has_dialogue=True,
        visual_reuse_required=True,
        episode_keys=["episode_001"],
        source_chapters=["第1章"],
        appearances=[
            RoleAppearanceDesignItem(
                role_name=name,
                name="base",
                desc=f"{name} stable look",
                full_body_prompt=f"{name} front full body",
                prompt=f"{name} front side back multiview",
                intro_video_prompt=f"{name} intro video prompt",
            )
        ],
        voices=[
            RoleVoiceItem(
                role_name=name,
                emotion="normal",
                desc=f"{name} normal voice",
                sample_text=f"我是{name}，负责推进当前剧情。",
            )
        ],
    )


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "role_voice_generation_role_filter"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    settings = Settings()
    settings.output.root_dir = tmp_root
    settings.project.episode_count = 1
    repo = ProjectRepository(settings)
    router = ProviderRouter(settings, provider_override="fake")
    counting_voice = CountingVoiceProvider(router._fake_voice)
    router._fake_voice = counting_voice
    workflow = PregenWorkflow(repo=repo, router=router)
    project_dir = repo.create_project(
        title="Role Voice Generation Role Filter",
        raw_script="Alpha and 九韶 appear together.",
        project_id="role_voice_generation_role_filter",
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)
    state.script.novel_full = {"episode_001": "Alpha and 九韶 appear together."}
    state.script.novel_extract = {"episode_001": "Alpha and 九韶 appear together."}

    items = [design_item("Alpha"), design_item("九韶")]
    for item in items:
        extract_item = RoleExtractItem(
            name=item.name,
            role_tier="primary",
            has_dialogue=True,
            visual_reuse_required=True,
            episode_keys=["episode_001"],
            source_chapters=["第1章"],
        )
        design_path = workflow.role_designs.item_relative_path_for_name(project_dir, item.name)
        workflow._apply_role_design_item(project_dir, state, item, design_path=design_path)
        role = next(role for role in state.roles.values() if role.name == item.name)
        workflow.role_designs.save_design_item(
            project_dir,
            extract_item=extract_item,
            design_item=item,
            role=role,
            bound_props=[],
        )

    repo.save_node_output(project_dir, "role_design", RoleDesignOutput(roles=items))
    repo.save_state(project_dir, state)

    await workflow.run(project_dir, only="role_voice_generation", force=True)
    require(len(counting_voice.create_voice_calls) == 2, "initial run should generate both role voices")

    counting_voice.create_voice_calls.clear()
    await workflow.run(project_dir, only="role_voice_generation", role_names=["九韶"], force=True)
    require(len(counting_voice.create_voice_calls) == 1, "role-scoped run should generate exactly one role voice")
    require("九韶" in counting_voice.create_voice_calls[0]["preview_text"], "role-scoped run did not target 九韶")

    output = json.loads(
        (project_dir / "assets" / "json" / "nodes" / "role_voice_generation.json").read_text(encoding="utf-8")
    )
    generated_role_names = {item["role_name"] for item in output["generated_voices"]}
    require(generated_role_names == {"Alpha", "九韶"}, f"merged output lost roles: {generated_role_names}")

    print("role_voice_generation_role_filter_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
