from __future__ import annotations

import json

from autodrama.core.schemas import (
    BGMDesignOutput,
    LayoutDedupeReviewOutput,
    LayoutExtractOutput,
    LayoutPromptOutput,
    LayoutPropBoundaryReviewOutput,
    ProjectState,
    PropDedupeOutput,
    PropDesignOutput,
    PropExtractItem,
    PropExtractOutput,
    PropPromptOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.services.director_service import DirectorService
from autodrama.utils.prompts import PromptStore


class AssetService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def prop_design_style_prompt(state: ProjectState) -> str:
        return str(state.metadata.get("prop_design_style_prompt") or "").strip()

    @staticmethod
    def visual_tone(state: ProjectState) -> str:
        return str(state.metadata.get("visual_tone") or "").strip()

    @classmethod
    def clip_segments_context(cls, state: ProjectState, episode_keys: list[str] | None = None) -> str:
        payload = state.metadata.get("clip_segments")
        if not isinstance(payload, dict) or not payload:
            return "（暂无 clip 片段；请以完整小说正文和分集摘要为准。）"
        if episode_keys:
            selected = {str(key) for key in episode_keys}
            payload = {key: value for key, value in payload.items() if str(key) in selected}
        return cls.format_json(payload)

    async def prop_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full_all_episodes: dict[str, str],
        existing_props: list[dict[str, object]] | None = None,
    ) -> PropExtractOutput:
        prompt = self.prompts.render(
            "prop_extract",
            novel_full_all_episodes=self.format_json(novel_full_all_episodes),
            existing_props=self.format_json(existing_props or []),
        )
        return await provider.generate_json(
            prompt,
            PropExtractOutput,
            temperature=0.4,
            metadata={
                "node_name": "prop_extract",
                "project_id": state.project_id,
                "expected_keys": list(novel_full_all_episodes),
            },
        )

    async def prop_finalize(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        props: list[dict[str, object]],
    ) -> PropDedupeOutput:
        prompt = self.prompts.render(
            "prop_finalize",
            props=self.format_json(props),
        )
        return await provider.generate_json(
            prompt,
            PropDedupeOutput,
            temperature=0.3,
            metadata={"node_name": "prop_finalize", "project_id": state.project_id},
        )

    async def prop_prompt(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        props: list[dict[str, object]],
        prompt_template: str = "prop_prompt",
    ) -> PropPromptOutput:
        prop_style_prompt = (
            self.prop_design_style_prompt(state)
            or self.visual_tone(state)
            or "（暂无道具风格约束，请只依据道具资产描述输出中性、可复用的道具提示词。）"
        )
        prompt = self.prompts.render(
            prompt_template,
            props=self.format_json(props),
            prop_style_prompt=prop_style_prompt,
        )
        return await provider.generate_json(
            prompt,
            PropPromptOutput,
            temperature=0.6,
            metadata={"node_name": "prop_prompt", "project_id": state.project_id},
        )

    async def prop_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        prop_item: PropExtractItem,
        prop_novel_full: dict[str, str],
        all_prop_extracts: list[dict[str, object]],
        existing_prop_designs: list[dict[str, object]],
    ) -> PropDesignOutput:
        del prop_novel_full, all_prop_extracts, existing_prop_designs
        prompt_output = await self.prop_prompt(
            state,
            provider,
            props=[prop_item.model_dump(mode="json")],
            prompt_template="prop_prompt",
        )
        prompt_by_asset = {
            (item.prop_name, item.asset_name): item.prompt
            for item in prompt_output.prop_asset_prompts
        }
        return PropDesignOutput(
            props=[
                {
                    "name": prop_item.name if asset.name == "base" else f"{prop_item.name}_{asset.name}",
                    "desc": asset.desc,
                    "prompt": prompt_by_asset.get((prop_item.name, asset.name), ""),
                    "status": asset.status,
                    "episode_keys": asset.episode_keys or prop_item.episode_keys,
                }
                for asset in prop_item.assets
                if prompt_by_asset.get((prop_item.name, asset.name), "")
            ]
        )
    async def layout_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full_all_episodes: dict[str, str],
        existing_layouts: list[dict[str, object]] | None = None,
    ) -> LayoutExtractOutput:
        prompt = self.prompts.render(
            "layout_extract",
            novel_full_all_episodes=self.format_json(novel_full_all_episodes),
            existing_layouts=self.format_json(existing_layouts or []),
        )
        return await provider.generate_json(
            prompt,
            LayoutExtractOutput,
            temperature=0.4,
            metadata={
                "node_name": "layout_extract",
                "project_id": state.project_id,
                "expected_keys": list(novel_full_all_episodes),
            },
        )

    async def layout_prompt(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        layouts: list[dict[str, object]],
        prompt_template: str = "layout_prompt",
    ) -> LayoutPromptOutput:
        prompt = self.prompts.render(
            prompt_template,
            layouts=self.format_json(layouts),
            visual_tone=self.visual_tone(state) or "（暂无导演 visual_tone，请只依据场景结构化资产输出中性、可复用的场景提示词。）",
        )
        return await provider.generate_json(
            prompt,
            LayoutPromptOutput,
            temperature=0.6,
            metadata={"node_name": "layout_prompt", "project_id": state.project_id},
        )

    async def layout_finalize(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        layouts: list[dict[str, object]],
    ) -> LayoutDedupeReviewOutput:
        prompt = self.prompts.render(
            "layout_finalize",
            layouts=self.format_json(layouts),
        )
        return await provider.generate_json(
            prompt,
            LayoutDedupeReviewOutput,
            temperature=0.3,
            metadata={"node_name": "layout_finalize", "project_id": state.project_id},
        )

    async def layout_prop_boundary_review(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        props: list[dict[str, object]],
        layouts: list[dict[str, object]],
    ) -> LayoutPropBoundaryReviewOutput:
        prompt = self.prompts.render(
            "layout_prop_boundary_review",
            props=self.format_json(props),
            layouts=self.format_json(layouts),
        )
        return await provider.generate_json(
            prompt,
            LayoutPropBoundaryReviewOutput,
            temperature=0.2,
            metadata={"node_name": "layout_prop_boundary_review", "project_id": state.project_id},
        )

    async def bgm_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_stories: dict[str, str],
    ) -> BGMDesignOutput:
        bgm_count = int(state.metadata.get("bgm_count", 3))
        prompt = self.prompts.render(
            "bgm_design",
            title=state.title,
            episode_stories=self.format_json(episode_stories),
            project_context=DirectorService.project_context(state, episode_keys=list(episode_stories)),
            bgm_count=bgm_count,
        )
        return await provider.generate_json(
            prompt,
            BGMDesignOutput,
            temperature=0.6,
            metadata={"node_name": "bgm_design", "project_id": state.project_id, "bgm_count": bgm_count},
        )
