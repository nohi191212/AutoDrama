from __future__ import annotations

import json

from autodrama.core.schemas import (
    BGMDesignOutput,
    LayoutDedupeReviewOutput,
    LayoutDesignOutput,
    LayoutExtractOutput,
    ProjectState,
    PropDesignOutput,
    PropExtractItem,
    PropExtractOutput,
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
    def layout_design_style_prompt(state: ProjectState) -> str:
        return str(state.metadata.get("layout_design_style_prompt") or "").strip()

    async def prop_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
    ) -> PropExtractOutput:
        prompt = self.prompts.render(
            "prop_extract",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(novel_full)),
            episode_keys=", ".join(novel_full),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
        )
        return await provider.generate_json(
            prompt,
            PropExtractOutput,
            temperature=0.4,
            metadata={
                "node_name": "prop_extract",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
            },
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
        prompt = self.prompts.render(
            "prop_design",
            title=state.title,
            raw_script=state.raw_script,
            prop_extract_item=self.format_json(prop_item.model_dump(mode="json")),
            prop_novel_full=self.format_json(prop_novel_full),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(prop_novel_full)),
            all_prop_extracts=self.format_json(all_prop_extracts),
            existing_prop_designs=self.format_json(existing_prop_designs),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
            prop_design_style_prompt=self.prop_design_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            PropDesignOutput,
            temperature=0.6,
            metadata={
                "node_name": "prop_design",
                "project_id": state.project_id,
                "prop_name": prop_item.name,
                "prop_status": prop_item.status,
                "episode_keys": list(prop_novel_full),
            },
        )

    async def layout_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
    ) -> LayoutExtractOutput:
        prompt = self.prompts.render(
            "layout_extract",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(novel_full)),
            episode_keys=", ".join(novel_full),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
            props=self.format_json({prop_id: prop.model_dump(mode="json") for prop_id, prop in state.props.items()}),
        )
        return await provider.generate_json(
            prompt,
            LayoutExtractOutput,
            temperature=0.4,
            metadata={
                "node_name": "layout_extract",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
            },
        )

    async def layout_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        layout_extracts: list[dict[str, object]],
        episode_stories: dict[str, str],
    ) -> LayoutDesignOutput:
        prompt = self.prompts.render(
            "layout_design",
            title=state.title,
            layout_extracts=self.format_json(layout_extracts),
            episode_stories=self.format_json(episode_stories),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(episode_stories)),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
            props=self.format_json({prop_id: prop.model_dump(mode="json") for prop_id, prop in state.props.items()}),
            layout_design_style_prompt=self.layout_design_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            LayoutDesignOutput,
            temperature=0.6,
            metadata={"node_name": "layout_design", "project_id": state.project_id},
        )

    async def layout_dedupe_review(self, state: ProjectState, provider: TextLLM) -> LayoutDedupeReviewOutput:
        prompt = self.prompts.render(
            "layout_dedupe_review",
            title=state.title,
            layouts=self.format_json({layout_id: layout.model_dump(mode="json") for layout_id, layout in state.layouts.items()}),
        )
        return await provider.generate_json(
            prompt,
            LayoutDedupeReviewOutput,
            temperature=0.3,
            metadata={"node_name": "layout_dedupe_review", "project_id": state.project_id},
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
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(episode_stories)),
            bgm_count=bgm_count,
        )
        return await provider.generate_json(
            prompt,
            BGMDesignOutput,
            temperature=0.6,
            metadata={"node_name": "bgm_design", "project_id": state.project_id, "bgm_count": bgm_count},
        )
