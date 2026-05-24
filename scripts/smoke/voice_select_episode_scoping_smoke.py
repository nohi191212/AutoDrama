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
from autodrama.core.schemas import ProjectState, Role, RoleAudio, ScriptBundle  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def role(role_id: str, name: str, intro: str, episode_key: str) -> Role:
    return Role(
        id=role_id,
        name=name,
        intro=intro,
        personality="冷静、克制",
        episode_keys=[episode_key],
        audio={
            "normal": RoleAudio(
                id=f"{role_id}_audio_normal",
                role_id=role_id,
                emotion="normal",
                desc=f"{name}的基础声音需求。",
                sample_text=f"我是{name}，我会把眼前的事处理好。",
            )
        },
    )


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "voice_select_episode_scoping"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    settings = Settings()
    settings.output.root_dir = tmp_root / "outputs"
    settings.project.episode_count = 2
    repo = ProjectRepository(settings)
    project_dir = tmp_root / "project"
    repo._create_project_dirs(project_dir)

    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    node = workflow._voice_node_runner("voice_select")
    state = ProjectState(
        project_id="voice_select_episode_scoping_smoke",
        title="Voice Select Episode Scope Smoke",
        raw_script="Alpha appears in episode one. Beta appears in episode two.",
        script=ScriptBundle(raw_script="Smoke"),
        roles={
            "role_alpha": role("role_alpha", "Alpha", "二十八岁男性职场青年。", "episode_001"),
            "role_beta": role("role_beta", "Beta", "二十六岁女性数据分析师。", "episode_002"),
        },
        metadata={"episode_count": 2},
    )

    state = await node.run(project_dir, state)
    first_output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    first_by_role = {item["role_id"]: item for item in first_output["selected_voices"]}
    require(set(first_by_role) == {"role_alpha", "role_beta"}, "initial voice_select did not select both roles")
    beta_before = first_by_role["role_beta"]

    workflow._active_episode_keys = {"episode_001"}
    workflow._force_pregen = True
    state = await node.run(project_dir, state)
    delattr(workflow, "_active_episode_keys")
    delattr(workflow, "_force_pregen")

    scoped_output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    scoped_by_role = {item["role_id"]: item for item in scoped_output["selected_voices"]}
    require(set(scoped_by_role) == {"role_alpha", "role_beta"}, "episode-scoped output did not preserve non-target roles")
    require(scoped_by_role["role_beta"] == beta_before, "episode_002 role selection changed during episode_001 rerun")
    require(state.roles["role_alpha"].voice_type == scoped_by_role["role_alpha"]["selected_voice_type"], "target role not rebound")
    require(state.roles["role_beta"].voice_type == beta_before["selected_voice_type"], "non-target role binding changed")

    print("voice_select_episode_scoping_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
