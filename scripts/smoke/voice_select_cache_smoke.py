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


def smoke_state() -> ProjectState:
    return ProjectState(
        project_id="voice_select_cache_smoke",
        title="Voice Select Cache Smoke",
        raw_script="林舟发现合同异常。",
        script=ScriptBundle(raw_script="林舟发现合同异常。"),
        roles={
            "role_linz": Role(
                id="role_linz",
                name="林舟",
                intro="二十八岁男性职场青年，冷静克制。",
                personality="谨慎、隐忍、重视证据",
                episode_keys=["episode_001"],
                audio={
                    "normal": RoleAudio(
                        id="role_linz_audio_normal",
                        role_id="role_linz",
                        emotion="normal",
                        desc="青年男性声音，克制清晰。",
                        sample_text="我是林舟，我会保持冷静。",
                    )
                },
            )
        },
    )


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "voice_select_cache"
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
    node.text_shortlist_batch_size = 1
    state = smoke_state()

    state = await node.run(project_dir, state)
    first_output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    first_item = first_output["selected_voices"][0]
    require(first_item["selection_source"] == "text_shortlist", f"expected text shortlist selection, got {first_item}")
    require("text_shortlist" in first_item["raw_response"], "text shortlist raw response missing")
    require(state.roles["role_linz"].voice_type == "fake_male_voice", "selected voice was not bound to role")

    state.roles["role_linz"].voice_type = None
    state.roles["role_linz"].voice_name = None
    state = await node.run(project_dir, state)
    second_output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    second_item = second_output["selected_voices"][0]
    require(second_item["selection_source"] == "cache", f"expected cache selection, got {second_item}")
    require(second_item["selected_voice_type"] == first_item["selected_voice_type"], "cached voice_type changed")
    require(state.roles["role_linz"].voice_type == first_item["selected_voice_type"], "cached selection was not rebound")

    print("voice_select_cache_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
