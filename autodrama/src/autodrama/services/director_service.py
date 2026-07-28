from __future__ import annotations

import json
from typing import Any

from autodrama.core.schemas import ClipToShotsModelOutput, KeyVisionPromptOutput, ProjectState
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class DirectorService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def _episode_count(state: ProjectState) -> int:
        return int(state.metadata.get("episode_count", 1))

    @staticmethod
    def _episode_duration_seconds(state: ProjectState) -> int:
        return int(state.metadata.get("episode_duration_seconds", 30))

    @staticmethod
    def _fallback_context() -> str:
        return "（暂无单独项目约束；请以当前剧本文本、已有资产和节点要求为准。）"

    @classmethod
    def project_context(
        cls,
        state: ProjectState,
        *,
        episode_keys: list[str] | None = None,
    ) -> str:
        del episode_keys
        payload: dict[str, str] = {}
        visual_style_prompt = cls.visual_style_prompt(state)
        if visual_style_prompt:
            payload["visual_style_prompt"] = visual_style_prompt
        visual_tone = str(state.metadata.get("visual_tone") or "").strip()
        if visual_tone:
            payload["visual_tone"] = visual_tone
        return cls.format_json(payload) if payload else cls._fallback_context()

    @staticmethod
    def visual_style_prompt(state: ProjectState) -> str:
        return str(state.metadata.get("visual_style_prompt") or "").strip()

    async def key_vision_prompt(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        story_context: str | None = None,
    ) -> KeyVisionPromptOutput:
        prompt = self.prompts.render(
            "key_vision_prompt",
            story_context=str(story_context or state.script.outline or "").strip(),
            visual_style_prompt=self.visual_style_prompt(state) or "（未单独配置。请以原始故事为准。）",
        )
        output = await provider.generate_json(
            prompt,
            KeyVisionPromptOutput,
            temperature=0.45,
            metadata={
                "node_name": "key_vision_prompt",
                "project_id": state.project_id,
            },
        )
        output.prompt = str(output.prompt or "").strip()
        if not output.prompt:
            raise ValueError("key_vision_prompt returned an empty prompt")
        return output

    async def clip_to_shots(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        audit_asset_name: str,
        clip_text: str,
        asset_index: str,
        previous_context: str,
        next_context: str,
    ) -> ClipToShotsModelOutput:
        """Plan provider-sized shots without leaking project/workflow metadata."""
        prompt = self.prompts.render(
            "clip_to_shots",
            clip_text=clip_text,
            asset_index=asset_index,
            previous_context=previous_context,
            next_context=next_context,
        )
        return await provider.generate_json(
            prompt,
            ClipToShotsModelOutput,
            temperature=0.35,
            metadata={
                "node_name": "clip_to_shots",
                "project_id": state.project_id,
                "prompt_asset_name": audit_asset_name,
            },
        )


__all__ = ["DirectorService"]
