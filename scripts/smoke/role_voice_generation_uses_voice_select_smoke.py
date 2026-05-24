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

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, RoleAudio, ScriptBundle  # noqa: E402
from autodrama.providers.base import VoiceSynthesisResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class DummySpeechProvider:
    name = "dummy"
    model = "dummy-model"
    resource_id = "dummy-tts"
    supports_direct_emotion_synthesis = True
    supports_local_voice_clone = False

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def available_speakers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "Dummy Male",
                "voice_type": "dummy_male_voice",
                "resource_id": "dummy-tts",
                "model_family": "dummy",
                "gender": "male",
                "scene": "通用场景",
                "language": "中文",
                "emotion_capable": True,
            },
            {
                "name": "Dummy Female",
                "voice_type": "dummy_female_voice",
                "resource_id": "dummy-tts",
                "model_family": "dummy",
                "gender": "female",
                "scene": "通用场景",
                "language": "中文",
                "emotion_capable": True,
            },
        ]

    def resolve_voice_resource_id(self, voice_type: str | None) -> str:
        return "dummy-tts" if voice_type else "dummy-fallback-resource"

    def resolve_emotion_plan(self, emotion: str) -> dict[str, Any]:
        return {
            "emotion": "neutral",
            "emotion_scale": 2,
            "instruction": f"{emotion} instruction",
        }

    def emotion_params_from_plan(self, plan: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in plan.items() if key != "instruction"}

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        metadata = metadata or {}
        self.calls.append({"voice": voice, "text": text, "metadata": metadata})
        return VoiceSynthesisResult(
            provider=self.name,
            model=str(metadata.get("resource_id") or self.resource_id),
            voice=voice,
            audio_data=base64.b64encode(f"{voice}:{text}".encode("utf-8")).decode("ascii"),
            audio_sample_rate=24000,
            audio_format="mp3",
            raw_response={"metadata": metadata},
        )


class DummyRouter:
    def __init__(self) -> None:
        self.provider = DummySpeechProvider()

    def audio(self, purpose: str) -> DummySpeechProvider:
        if purpose != "speech":
            raise ValueError(f"Unexpected audio purpose: {purpose}")
        return self.provider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "role_voice_generation_uses_voice_select"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)
    settings = Settings()
    settings.output.root_dir = tmp_root / "outputs"
    repo = ProjectRepository(settings)
    project_dir = tmp_root / "project"
    repo._create_project_dirs(project_dir)

    router = DummyRouter()
    workflow = PregenWorkflow(repo=repo, router=router)
    state = ProjectState(
        project_id="role_voice_generation_uses_voice_select_smoke",
        title="Role Voice Generation Uses Voice Select Smoke",
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

    state = await workflow._voice_node_runner("voice_select").run(project_dir, state)
    selection_output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    selected_voice = selection_output["selected_voices"][0]["selected_voice_type"]
    require(selected_voice == "dummy_male_voice", f"unexpected selected voice: {selection_output}")

    state = await workflow._voice_node_runner("role_voice_generation").run(project_dir, state)
    require(router.provider.calls, "synthesize_speech was not called")
    first_call = router.provider.calls[0]
    require(first_call["voice"] == "dummy_male_voice", f"synthesis did not use voice_select voice: {first_call}")
    require(first_call["metadata"]["resource_id"] == "dummy-tts", "synthesis did not use selected resource_id")

    output = json.loads((project_dir / "assets" / "json" / "nodes" / "role_voice_generation.json").read_text(encoding="utf-8"))
    generated = output["generated_voices"][0]
    require(generated["voice"] == "dummy_male_voice", "role_voice_generation output voice mismatch")
    require(generated["voice_name"] == "Dummy Male", "role_voice_generation output voice_label mismatch")
    require(state.roles["role_linz"].audio["normal"].voice_type == "dummy_male_voice", "audio voice_type mismatch")

    print("role_voice_generation_uses_voice_select_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
