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
from autodrama.core.schemas import ProjectState, Role, RoleAudio, ScriptBundle  # noqa: E402
from autodrama.core.voice_catalog import RoleVoiceSelectionItem  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def role(role_id: str, name: str, episode_key: str) -> Role:
    return Role(
        id=role_id,
        name=name,
        intro=f"{name}是需要配音的角色。",
        personality="冷静、克制",
        episode_keys=[episode_key],
        audio={
            "normal": RoleAudio(
                id=f"{role_id}_audio_normal",
                role_id=role_id,
                emotion="normal",
                desc=f"{name}的常态基础声音，稳定自然。",
                sample_text=f"我是{name}，我会把眼前的问题处理好。",
            )
        },
    )


def selection_for(node: Any, role_item: Role) -> RoleVoiceSelectionItem:
    return RoleVoiceSelectionItem(
        role_id=role_item.id,
        role_name=role_item.name,
        selected_voice_label="Fake Male",
        selected_voice_type="fake_male_voice",
        selected_voice_resource_id="fake-tts",
        selected_voice_model_family="fake",
        selected_voice_catalog_key="fake:fake-tts:fake_male_voice",
        selected_reason="progressive save smoke selection",
        selection_source="catalog_heuristic",
        top_candidates=[],
        role_design_hash=node.role_design_hash(role_item),
        catalog_version="smoke-catalog-version",
        catalog_hash="smoke-catalog-hash",
        provider="fake",
        model="fake-tts",
        raw_response={
            "selection_prompt_version": node.selection_prompt_version,
            "selection_model": "smoke",
        },
    )


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "voice_select_progressive_save"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    settings = Settings()
    settings.output.root_dir = tmp_root / "outputs"
    repo = ProjectRepository(settings)
    project_dir = tmp_root / "project"
    repo._create_project_dirs(project_dir)

    router = ProviderRouter(settings, provider_override="fake")
    workflow = PregenWorkflow(repo=repo, router=router)
    node = workflow._voice_node_runner("voice_select")
    state = ProjectState(
        project_id="voice_select_progressive_save_smoke",
        title="Voice Select Progressive Save Smoke",
        raw_script="Alpha and Beta both need voices.",
        script=ScriptBundle(raw_script="Smoke"),
        roles={
            "role_alpha": role("role_alpha", "Alpha", "episode_001"),
            "role_beta": role("role_beta", "Beta", "episode_001"),
        },
    )

    async def select_role_voice(**kwargs: Any) -> RoleVoiceSelectionItem:
        role_item = kwargs["role"]
        if role_item.id == "role_beta":
            raise RuntimeError("intentional voice_select failure after first role")
        return selection_for(node, role_item)

    node.select_role_voice = select_role_voice  # type: ignore[method-assign]

    try:
        await node.run(project_dir, state)
    except RuntimeError as exc:
        require("intentional voice_select failure" in str(exc), f"unexpected failure: {exc}")
    else:
        raise AssertionError("voice_select should have failed on the second role")

    output_path = project_dir / "assets" / "json" / "nodes" / "voice_select.json"
    require(output_path.exists(), "voice_select did not save progress after the first role")
    output = json.loads(output_path.read_text(encoding="utf-8"))
    selected_role_ids = [item["role_id"] for item in output["selected_voices"]]
    require(selected_role_ids == ["role_alpha"], f"unexpected progressive output: {output}")
    require(state.roles["role_alpha"].voice_type == "fake_male_voice", "first role was not bound")
    require(state.roles["role_beta"].voice_type is None, "failed role should not be bound")

    print("voice_select_progressive_save_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
