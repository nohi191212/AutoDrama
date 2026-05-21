from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, ScriptBundle  # noqa: E402
from autodrama.providers.volcengine.audio.seed_tts import VolcengineSeedTTSProvider  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.role_service import RoleService  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class CapturingTextProvider:
    name = "capture"
    model = "capture-json"

    def __init__(self) -> None:
        self.prompt = ""

    async def generate_json(
        self,
        prompt: str,
        schema,
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ):
        del temperature, metadata
        self.prompt = prompt
        return schema.model_validate(
            {
                "role_voices": [
                    {
                        "role_name": "林舟",
                        "emotion": "normal",
                        "voice_name": "云舟 2.0",
                        "voice_type": "zh_male_m191_uranus_bigtts",
                        "voice_resource_id": "seed-tts-2.0",
                        "voice_selection_reason": "青年男性音色，克制、清晰，适合冷静男主。",
                        "desc": "青年男声，沉稳克制，咬字清楚。",
                        "sample_text": "我是林舟，一个习惯把细节记在心里的人。我会保持冷静，找到问题的源头。",
                    },
                    {
                        "role_name": "林舟",
                        "emotion": "angry",
                        "voice_name": "云舟 2.0",
                        "voice_type": "zh_male_m191_uranus_bigtts",
                        "voice_resource_id": "seed-tts-2.0",
                        "voice_selection_reason": "青年男性音色，克制、清晰，适合冷静男主。",
                        "desc": "同一青年男声，语气更锋利，但不吼叫。",
                        "sample_text": "我是林舟，就算被逼到角落，也不能失去判断。我会把真相一点点找回来。",
                    },
                ]
            }
        )


class NoopRouter:
    pass


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml")
    speech_provider = VolcengineSeedTTSProvider(settings.providers["volcengine"], settings.runtime)
    speakers = speech_provider.available_speakers()
    prompt_speakers = speech_provider.available_speakers_for_prompt()
    selected = speech_provider.speaker_by_voice_type("zh_male_m191_uranus_bigtts")

    assert len(speakers) == 448
    assert len(prompt_speakers) == 448
    assert selected is not None
    assert selected["resource_id"] == "seed-tts-2.0"

    state = ProjectState(
        project_id="voice_catalog_smoke",
        title="Smoke",
        raw_script="林舟发现合同异常。",
        script=ScriptBundle(raw_script="林舟发现合同异常。", novel_extract={"episode_1": "林舟发现合同异常。"}),
        roles={
            "role_linz": Role(
                id="role_linz",
                name="林舟",
                intro="二十八岁职场青年，冷静克制。",
                personality="谨慎、隐忍、重视证据。",
            )
        },
    )
    text_provider = CapturingTextProvider()
    service = RoleService(PromptStore())
    output = await service.role_voice_design(
        state,
        text_provider,
        episode_stories={"episode_1": "林舟发现合同异常。"},
        available_voices=prompt_speakers,
    )

    assert '"voice_type": "zh_male_m191_uranus_bigtts"' in text_provider.prompt
    assert "可用音色列表" in text_provider.prompt

    workflow = PregenWorkflow(repo=ProjectRepository(settings), router=NoopRouter())
    workflow._apply_role_voice_design_output(state, output, speech_provider=speech_provider)
    role = state.roles["role_linz"]
    normal = role.audio["normal"]
    angry = role.audio["angry"]

    assert role.voice_name == "云舟 2.0"
    assert role.voice_type == "zh_male_m191_uranus_bigtts"
    assert role.voice_resource_id == "seed-tts-2.0"
    assert normal.voice_type == role.voice_type
    assert angry.voice_resource_id == role.voice_resource_id
    assert speech_provider.resolve_voice_resource_id(role.voice_type) == role.voice_resource_id

    payload = speech_provider.build_synthesis_payload(
        voice=role.voice_type,
        text="我是林舟。",
        metadata={"emotion": "normal", "resource_id": role.voice_resource_id},
    )
    assert payload["req_params"]["speaker"] == role.voice_type

    print("volcengine_voice_catalog_smoke=ok")
    print(f"speaker_count={len(speakers)}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
