from __future__ import annotations

import json

from autodrama.core.schemas import (
    ProjectState,
    ScriptDetailOutput,
    ScriptOutlineOutput,
    ScriptPolishOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class ScriptService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def episode_count(state: ProjectState) -> int:
        return int(state.metadata.get("episode_count", 1))

    @staticmethod
    def episode_duration_seconds(state: ProjectState) -> int:
        return int(state.metadata.get("episode_duration_seconds", 30))

    @staticmethod
    def visual_style_label(state: ProjectState) -> str:
        return str(state.metadata.get("visual_style_label", "真人电影质感"))

    @staticmethod
    def visual_style_prompt(state: ProjectState) -> str:
        return str(
            state.metadata.get(
                "visual_style_prompt",
                "真人电影质感：真实摄影、自然光或电影布光、真实材质、真实皮肤纹理和电影镜头语言。",
            )
        )

    @staticmethod
    def episode_keys(episode_count: int) -> list[str]:
        return [f"episode_{index:03d}" for index in range(1, episode_count + 1)]

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    async def script_outline(self, state: ProjectState, provider: TextLLM) -> ScriptOutlineOutput:
        episode_count = self.episode_count(state)
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "script_outline",
            title=state.title,
            raw_script=state.raw_script,
            episode_count=episode_count,
            episode_duration_seconds=episode_duration_seconds,
            episode_keys=", ".join(self.episode_keys(episode_count)),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            ScriptOutlineOutput,
            temperature=0.7,
            metadata={
                "node_name": "script_outline",
                "project_id": state.project_id,
                "required_mapping_field": "episode_outlines",
                "expected_keys": self.episode_keys(episode_count),
            },
        )

    async def script_detail(self, state: ProjectState, provider: TextLLM) -> ScriptDetailOutput:
        episode_count = self.episode_count(state)
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "script_detail",
            title=state.title,
            raw_script=state.raw_script,
            outline=state.script.outline or "",
            episode_outlines=self.format_json(state.script.episode_outlines),
            episode_count=episode_count,
            episode_duration_seconds=episode_duration_seconds,
            episode_keys=", ".join(self.episode_keys(episode_count)),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            ScriptDetailOutput,
            temperature=0.7,
            metadata={
                "node_name": "script_detail",
                "project_id": state.project_id,
                "required_mapping_field": "detailed_script",
                "expected_keys": self.episode_keys(episode_count),
            },
        )

    async def script_polish(self, state: ProjectState, provider: TextLLM) -> ScriptPolishOutput:
        episode_count = self.episode_count(state)
        episode_duration_seconds = self.episode_duration_seconds(state)
        prompt = self.prompts.render(
            "script_polish",
            title=state.title,
            detailed_script=self.format_json(state.script.detailed_script),
            episode_count=episode_count,
            episode_duration_seconds=episode_duration_seconds,
            episode_keys=", ".join(self.episode_keys(episode_count)),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            ScriptPolishOutput,
            temperature=0.7,
            metadata={
                "node_name": "script_polish",
                "project_id": state.project_id,
                "required_mapping_field": "final_script",
                "expected_keys": self.episode_keys(episode_count),
            },
        )
