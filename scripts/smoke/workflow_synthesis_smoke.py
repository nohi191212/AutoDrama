from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import Settings  # noqa: E402
from autodrama.core.schemas import ProjectState, Role, RoleAudio, ScriptBundle, StoryboardShot  # noqa: E402
from autodrama.providers.base import VoiceSynthesisResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class DummySpeechProvider:
    name = "dummy"
    model = "dummy-seed-tts"

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def resolve_role_voice(self, **kwargs) -> str:
        return "dummy_speaker"

    def resolve_voice_resource_id(self, voice_type: str | None) -> str:
        if voice_type == "role_selected_speaker":
            return "role-selected-resource"
        return "dummy-resource"

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
        self.calls.append({"voice": voice, "text": text, "metadata": metadata or {}})
        return VoiceSynthesisResult(
            provider=self.name,
            model=str((metadata or {}).get("resource_id") or self.model),
            voice=voice,
            audio_data=base64.b64encode(f"{voice}:{text}".encode("utf-8")).decode("ascii"),
            audio_sample_rate=24000,
            audio_format="mp3",
            raw_response={"metadata": metadata or {}},
        )


class DummyRouter:
    def __init__(self) -> None:
        self.provider = DummySpeechProvider()

    def audio(self, purpose: str, *, node_name: str | None = None) -> DummySpeechProvider:
        del node_name
        if purpose != "speech":
            raise ValueError(f"Unexpected audio purpose: {purpose}")
        return self.provider


async def main_async() -> int:
    settings = Settings()
    repo = ProjectRepository(settings)
    workflow = PregenWorkflow(repo=repo, router=DummyRouter())
    provider = DummySpeechProvider()
    tmp_root = ROOT_DIR / ".tmp" / "smoke"
    tmp_root.mkdir(parents=True, exist_ok=True)
    project_dir = tmp_root / "workflow_synthesis_direct_dialogue"
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
                voice_name="Role Selected Voice",
                voice_type="role_selected_speaker",
                voice_resource_id="role-selected-resource",
                voice_model_family="Dummy Seed",
                voice_selection_reason="Smoke selected role voice.",
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
    shot = StoryboardShot(
        shot_id="shot_001_001",
        index=1,
        layout_id="layout_room",
        title="林舟爆发质问",
        duration_seconds=3.0,
        dialogue=["林舟：你为什么骗我？"],
        role_ids=["role_a"],
        video_prompt="林舟压低声音愤怒质问。",
    )

    asset, warning = await workflow._generate_shot_dialogue_audio(
        provider=provider,
        project_dir=project_dir,
        state=state,
        episode_key="episode_001",
        shot=shot,
        line_index=0,
        line=shot.dialogue[0],
    )

    assert warning is None
    assert asset is not None
    assert asset.voice == "role_selected_speaker"
    assert asset.voice_name == "Role Selected Voice"
    assert asset.voice_resource_id == "role-selected-resource"
    assert asset.voice_model_family == "Dummy Seed"
    assert asset.emotion == "angry"
    assert asset.emotion_instruction == "angry instruction"
    assert asset.asset_path and (project_dir / asset.asset_path).exists()
    assert provider.calls[0]["voice"] == "role_selected_speaker"
    assert provider.calls[0]["text"] == "你为什么骗我？"
    assert provider.calls[0]["metadata"]["node_name"] == "shot_dialogue_audio_generation"
    assert provider.calls[0]["metadata"]["resource_id"] == "role-selected-resource"

    removed_output_path = project_dir / "assets" / "json" / "nodes" / "role_voice_generation.json"
    assert not removed_output_path.exists()

    print("workflow_synthesis_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
