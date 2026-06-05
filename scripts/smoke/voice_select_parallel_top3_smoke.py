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
from autodrama.core.voice_catalog import VoiceSelectShortlistOutput  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


class FilteredSpeechProvider:
    name = "volcengine"
    model = "seed-tts-2.0"
    resource_id = "seed-tts-2.0"

    def available_speakers(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "候选男声 0",
                "voice_type": "zh_male_candidate_0",
                "resource_id": "seed-tts-2.0",
                "model_family": "豆包语音合成模型2.0",
                "gender": "male",
                "language": "中文",
                "scene": "短剧对白",
                "emotion_capable": True,
            },
            {
                "name": "候选男声 1",
                "voice_type": "zh_male_candidate_1",
                "resource_id": "seed-tts-2.0",
                "model_family": "豆包语音合成模型2.0",
                "gender": "male",
                "language": "中文",
                "scene": "短剧对白",
                "emotion_capable": True,
            },
            {
                "name": "候选男声 2",
                "voice_type": "zh_male_candidate_2",
                "resource_id": "seed-tts-2.0",
                "model_family": "豆包语音合成模型2.0",
                "gender": "male",
                "language": "中文",
                "scene": "短剧对白",
                "emotion_capable": True,
            },
            {
                "name": "一代男声",
                "voice_type": "zh_male_legacy",
                "resource_id": "seed-tts-1.0",
                "model_family": "豆包语音合成模型1.0",
                "gender": "male",
                "language": "中文",
            },
            {
                "name": "候选女声",
                "voice_type": "zh_female_candidate",
                "resource_id": "seed-tts-2.0",
                "model_family": "豆包语音合成模型2.0",
                "gender": "female",
                "language": "中文",
            },
            {
                "name": "English Male",
                "voice_type": "en_male_candidate",
                "resource_id": "seed-tts-2.0",
                "model_family": "豆包语音合成模型2.0",
                "gender": "male",
                "language": "English",
            },
        ]


class ParallelTextProvider:
    name = "parallel_text"
    model = "parallel-model"

    def __init__(self) -> None:
        self.active_calls = 0
        self.max_active_calls = 0
        self.call_count = 0
        self.profile_batches: list[list[dict[str, Any]]] = []

    async def generate_json(
        self,
        prompt: str,
        schema,
        *,
        temperature: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ):
        del prompt, temperature
        metadata = metadata or {}
        self.call_count += 1
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        await asyncio.sleep(0.05)
        self.active_calls -= 1

        if schema is not VoiceSelectShortlistOutput:
            raise ValueError(f"unexpected schema: {schema}")
        voice_profiles = metadata.get("voice_profiles")
        if not isinstance(voice_profiles, list) or len(voice_profiles) < 3:
            return VoiceSelectShortlistOutput(candidates=[])
        self.profile_batches.append(voice_profiles)
        return VoiceSelectShortlistOutput(
            candidates=[
                {
                    "candidate_id": voice_profiles[1]["candidate_id"],
                    "voice_label": "mismatched label",
                    "voice_type": voice_profiles[0]["voice_type"],
                    "score": 9.5,
                    "reason": "candidate_id should lock this to the second profile.",
                },
                {
                    "candidate_id": voice_profiles[0]["candidate_id"],
                    "voice_label": voice_profiles[0]["voice_label"],
                    "voice_type": voice_profiles[0]["voice_type"],
                    "score": 9.0,
                    "reason": "second best.",
                },
                {
                    "candidate_id": voice_profiles[2]["candidate_id"],
                    "voice_label": voice_profiles[2]["voice_label"],
                    "voice_type": voice_profiles[2]["voice_type"],
                    "score": 8.5,
                    "reason": "third best.",
                },
            ]
        )


class ParallelRouter:
    def __init__(self) -> None:
        self.speech = FilteredSpeechProvider()
        self.text_provider = ParallelTextProvider()

    def audio(self, purpose: str):
        if purpose != "speech":
            raise ValueError(f"unexpected audio purpose: {purpose}")
        return self.speech

    def text(self, purpose: str):
        if purpose not in {"voice_select", "role"}:
            raise ValueError(f"unexpected text purpose: {purpose}")
        return self.text_provider


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def role(role_id: str, name: str) -> Role:
    return Role(
        id=role_id,
        name=name,
        intro=f"{name}是二十八岁男性角色，冷静克制。",
        personality="谨慎、隐忍",
        episode_keys=["episode_001"],
        audio={
            "normal": RoleAudio(
                id=f"{role_id}_audio_normal",
                role_id=role_id,
                emotion="normal",
                desc="青年男性声音，克制清晰。",
                sample_text=f"我是{name}，我会保持冷静。",
            )
        },
    )


async def main_async() -> int:
    tmp_root = ROOT_DIR / ".tmp" / "smoke" / "voice_select_parallel_top3"
    if tmp_root.exists():
        shutil.rmtree(tmp_root)

    settings = Settings()
    settings.output.root_dir = tmp_root / "outputs"
    repo = ProjectRepository(settings)
    project_dir = tmp_root / "project"
    repo._create_project_dirs(project_dir)

    router = ParallelRouter()
    workflow = PregenWorkflow(repo=repo, router=router)
    node = workflow._voice_node_runner("voice_select")

    state = ProjectState(
        project_id="voice_select_parallel_top3_smoke",
        title="Voice Select Parallel Top3 Smoke",
        raw_script="多个男性角色需要并行选择长期复用音色。",
        script=ScriptBundle(raw_script="Smoke"),
        roles={
            "role_alpha": role("role_alpha", "林舟"),
            "role_beta": role("role_beta", "顾沉"),
            "role_gamma": role("role_gamma", "陆远"),
        },
    )

    await node.run(project_dir, state)

    require(router.text_provider.call_count == 3, f"expected one text call per role, got {router.text_provider.call_count}")
    require(
        router.text_provider.max_active_calls > 1,
        f"role text calls were not concurrent: max_active={router.text_provider.max_active_calls}",
    )
    for batch in router.text_provider.profile_batches:
        require(len(batch) == 3, f"expected exactly 3 filtered candidates, got {len(batch)}")
        voice_types = {profile["voice_type"] for profile in batch}
        require(voice_types == {"zh_male_candidate_0", "zh_male_candidate_1", "zh_male_candidate_2"}, str(voice_types))
        require([profile["candidate_id"] for profile in batch] == ["V001", "V002", "V003"], str(batch))

    output = json.loads((project_dir / "assets" / "json" / "nodes" / "voice_select.json").read_text(encoding="utf-8"))
    require(len(output["selected_voices"]) == 3, f"expected 3 selected roles: {output}")
    for item in output["selected_voices"]:
        require(item["selection_source"] == "text_shortlist", f"expected text shortlist: {item}")
        require(item["selected_voice_type"] == "zh_male_candidate_1", f"candidate_id did not lock selection: {item}")
        require(len(item["top_candidates"]) == 3, f"expected top 3 candidates: {item['top_candidates']}")
        require(item["raw_response"]["selection_prompt_version"].endswith("visual_refs.v4"), "prompt version mismatch")

    print("voice_select_parallel_top3_smoke=ok")
    print(f"project_dir={project_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
