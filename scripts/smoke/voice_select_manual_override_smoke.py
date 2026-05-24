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

from autodrama.config import ProviderSettings, Settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, RoleAudio, ScriptBundle  # noqa: E402
from autodrama.providers.local.mock.fake import FakeVoiceDesignProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class DummyRouter:
    def __init__(self, provider) -> None:
        self.provider = provider

    def audio(self, purpose: str):
        if purpose != "speech":
            raise ValueError(f"Unexpected audio purpose: {purpose}")
        return self.provider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "voice_select_manual_override"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    settings = Settings()
    settings.output.root_dir = tmp_root / "outputs"
    repo = ProjectRepository(settings)
    project_dir = tmp_root / "project"
    repo._create_project_dirs(project_dir)

    provider = FakeVoiceDesignProvider()
    provider.settings = ProviderSettings(
        options={
            "role_speakers": {
                "role_linz": {
                    "voice_label": "Fake Female",
                    "voice_type": "fake_female_voice",
                    "voice_resource_id": "fake-tts",
                    "voice_model_family": "fake",
                }
            }
        }
    )
    workflow = PregenWorkflow(repo=repo, router=DummyRouter(provider))
    node = workflow._voice_node_runner("voice_select")
    state = ProjectState(
        project_id="voice_select_manual_override_smoke",
        title="Voice Select Manual Override Smoke",
        raw_script="林舟发现合同异常。",
        script=ScriptBundle(raw_script="Smoke"),
        roles={
            "role_linz": Role(
                id="role_linz",
                name="林舟",
                intro="二十八岁男性职场青年，冷静克制。",
                personality="谨慎、隐忍",
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

    state = await node.run(project_dir, state)
    output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    item = output["selected_voices"][0]
    require(item["selection_source"] == "manual_override", f"manual override not recorded: {item}")
    require(item["selected_voice_type"] == "fake_female_voice", "manual override voice_type was not selected")
    require(state.roles["role_linz"].voice_type == "fake_female_voice", "manual override was not bound to role")
    require(state.roles["role_linz"].audio["normal"].voice_type == "fake_female_voice", "manual override was not bound to audio")

    print("voice_select_manual_override_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
