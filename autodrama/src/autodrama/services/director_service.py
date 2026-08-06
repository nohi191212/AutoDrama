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
        return cls.format_json(payload) if payload else cls._fallback_context()

    @staticmethod
    def visual_style_prompt(state: ProjectState) -> str:
        value = str(state.metadata.get("visual_style_prompt") or "").strip()
        if not value:
            raise ValueError("project metadata has no global visual style prompt")
        return value

    @staticmethod
    def key_vision_director_brief(state: ProjectState) -> str:
        return str(state.metadata.get("key_vision_director_brief") or "").strip()

    @staticmethod
    def key_vision_continuity_contract(state: ProjectState) -> str:
        return str(state.metadata.get("key_vision_continuity_contract") or "").strip()

    @staticmethod
    def key_vision_render_contract(state: ProjectState, image_canvas: str | None) -> str:
        override = str(state.metadata.get("key_vision_render_contract") or "").strip()
        if override:
            return override
        canvas = str(image_canvas or "").strip()
        if not canvas or canvas.lower() == "auto":
            raise ValueError("key_vision_prompt requires a configured image canvas")
        orientation = "configured orientation"
        parts = canvas.lower().split("x", 1)
        if len(parts) == 2:
            try:
                width, height = (int(part.strip()) for part in parts)
            except ValueError:
                pass
            else:
                orientation = "landscape" if width > height else "portrait" if height > width else "square"
        return (
            f"Configured image canvas: {canvas} ({orientation}). Deliver one continuous in-episode "
            "cinematic frame, not promotional key art: no title space, advertising symmetry, central hero "
            "coronation, montage, or split scene. The configured canvas overrides conflicting orientation "
            "or poster language in softer creative guidance."
        )

    async def key_vision_prompt(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        story_context: str | None = None,
        image_canvas: str | None = None,
    ) -> KeyVisionPromptOutput:
        resolved_story_context = str(story_context or "").strip()
        if not resolved_story_context:
            raise ValueError("key_vision_prompt requires explicit story_context")
        prompt = self.prompts.render(
            "key_vision_prompt",
            story_context=resolved_story_context,
            global_visual_style=self.visual_style_prompt(state),
            director_brief=self.key_vision_director_brief(state),
            render_contract=self.key_vision_render_contract(state, image_canvas),
            continuity_contract=self.key_vision_continuity_contract(state),
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
        output.shot_contract = str(output.shot_contract or "").strip()
        output.scene_style_contract = str(output.scene_style_contract or "").strip()
        output.prompt = str(output.prompt or "").strip()
        for field_name in ("shot_contract", "scene_style_contract", "prompt"):
            if not getattr(output, field_name):
                raise ValueError(f"key_vision_prompt returned an empty {field_name}")
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
        available_seconds: float = 30.0,
        reference_budget: int = 4,
    ) -> ClipToShotsModelOutput:
        """Plan provider-sized shots without leaking project/workflow metadata."""
        prompt = self.prompts.render(
            "clip_to_shots",
            clip_text=clip_text,
            asset_index=asset_index,
            previous_context=previous_context,
            next_context=next_context,
            available_seconds=f"{available_seconds:.1f}",
            reference_budget=str(reference_budget),
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
