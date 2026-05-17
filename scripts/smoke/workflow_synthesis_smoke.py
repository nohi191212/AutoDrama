from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, RoleAudio, ScriptBundle  # noqa: E402
from autodrama.providers.base import VoiceSynthesisResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class DummySpeechProvider:
    name = "dummy"
    model = "dummy-seed-tts"
    supports_direct_emotion_synthesis = True

    def resolve_role_voice(self, **kwargs) -> str:
        return "dummy_speaker"

    def resolve_emotion_plan(self, emotion: str) -> dict[str, object]:
        return {
            "emotion": "angry" if emotion == "angry" else "neutral",
            "emotion_scale": 4,
            "speech_rate": 5,
            "loudness_rate": 1,
            "instruction": f"{emotion} instruction",
        }

    def emotion_params_from_plan(self, plan: dict[str, object]) -> dict[str, object]:
        return {key: value for key, value in plan.items() if key != "instruction"}

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, object] | None = None,
    ) -> VoiceSynthesisResult:
        return VoiceSynthesisResult(
            provider=self.name,
            model=self.model,
            voice=voice,
            audio_data=base64.b64encode(f"{voice}:{text}".encode("utf-8")).decode("ascii"),
            audio_sample_rate=24000,
            audio_format="mp3",
            raw_response={"metadata": metadata or {}},
        )


class DummyRouter:
    def __init__(self) -> None:
        self.provider = DummySpeechProvider()

    def audio(self, purpose: str) -> DummySpeechProvider:
        if purpose != "speech":
            raise ValueError(f"Unexpected audio purpose: {purpose}")
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml")
    repo = ProjectRepository(settings)
    workflow = PregenWorkflow(repo=repo, router=DummyRouter())
    tmp_root = ROOT_DIR / ".tmp" / "smoke"
    tmp_root.mkdir(parents=True, exist_ok=True)
    project_dir = tmp_root / "workflow_synthesis"
    repo._create_project_dirs(project_dir)
    state = ProjectState(
        project_id="voice_synthesis_smoke",
        title="Smoke",
        raw_script="Smoke",
        script=ScriptBundle(raw_script="Smoke"),
        roles={
            "role_a": Role(
                id="role_a",
                name="林舟",
                intro="男主角，语气克制。",
                audio={
                    "normal": RoleAudio(
                        id="role_a_audio_normal",
                        role_id="role_a",
                        emotion="normal",
                        desc="平稳自然",
                        sample_text="我是林舟。",
                    ),
                    "angry": RoleAudio(
                        id="role_a_audio_angry",
                        role_id="role_a",
                        emotion="angry",
                        desc="愤怒克制",
                        sample_text="你为什么骗我？",
                    ),
                },
            )
        },
    )

    await workflow._run_role_voice_generation(project_dir, state)
    normal = state.roles["role_a"].audio["normal"]
    angry = state.roles["role_a"].audio["angry"]
    output_path = project_dir / "assets" / "json" / "nodes" / "role_voice_generation.json"

    assert normal.asset_id == "dummy_speaker"
    assert angry.asset_id == "dummy_speaker"
    assert normal.asset_path and (project_dir / normal.asset_path).exists()
    assert angry.asset_path and (project_dir / angry.asset_path).exists()
    assert angry.emotion_instruction == "angry instruction"
    assert output_path.exists()
    assert '"generation_method": "synthesis"' in output_path.read_text(encoding="utf-8")

    print("workflow_synthesis_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
