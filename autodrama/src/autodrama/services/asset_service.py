from __future__ import annotations

import json

from autodrama.core.schemas import (
    BGMDesignOutput,
    LayoutDedupeReviewOutput,
    LayoutDesignOutput,
    ProjectState,
    PropDesignOutput,
    PropExtractItem,
    PropExtractOutput,
    RoleAppearanceDesignOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class AssetService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

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
    def role_appearance_view_requirement(state: ProjectState) -> str:
        visual_style = str(state.metadata.get("visual_style", "live_action"))
        if visual_style == "live_action":
            return "半身或全身真人电影感角色设定图；干净背景；无其他人物；不要做三视图拼版。"
        return (
            "三视图角色设定图 / character turnaround sheet：同一角色正面、侧面、背面三视图并排，"
            "统一身高比例和服装细节，干净背景，无其他人物；不要做单张半身照或只有一个角度的角色图。"
        )

    async def role_appearance_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_stories: dict[str, str],
    ) -> RoleAppearanceDesignOutput:
        prompt = self.prompts.render(
            "role_appearance_design",
            title=state.title,
            episode_stories=self.format_json(episode_stories),
            roles=self.format_json(
                [
                    {
                        "name": role.name,
                        "intro": role.intro,
                        "personality": role.personality,
                        "aliases": role.aliases,
                        "voice_summary": role.voice_summary,
                    }
                    for role in state.roles.values()
                ]
            ),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
            role_appearance_view_requirement=self.role_appearance_view_requirement(state),
        )
        return await provider.generate_json(
            prompt,
            RoleAppearanceDesignOutput,
            temperature=0.6,
            metadata={"node_name": "role_appearance_design", "project_id": state.project_id},
        )

    async def prop_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
    ) -> PropExtractOutput:
        role_bound_props = [
            prop.model_dump(mode="json")
            for prop in state.props.values()
            if prop.source in {"role_design", "role_appearance_design"} or prop.owner_role_id
        ]
        prompt = self.prompts.render(
            "prop_extract",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            episode_keys=", ".join(novel_full),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
            role_bound_props=self.format_json(role_bound_props),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
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
        role_bound_props = [
            prop.model_dump(mode="json")
            for prop in state.props.values()
            if prop.source in {"role_design", "role_appearance_design"} or prop.owner_role_id
        ]
        prompt = self.prompts.render(
            "prop_design",
            title=state.title,
            raw_script=state.raw_script,
            prop_extract_item=self.format_json(prop_item.model_dump(mode="json")),
            prop_novel_full=self.format_json(prop_novel_full),
            all_prop_extracts=self.format_json(all_prop_extracts),
            existing_prop_designs=self.format_json(existing_prop_designs),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
            role_bound_props=self.format_json(role_bound_props),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
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

    async def layout_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_stories: dict[str, str],
    ) -> LayoutDesignOutput:
        prompt = self.prompts.render(
            "layout_design",
            title=state.title,
            episode_stories=self.format_json(episode_stories),
            roles=self.format_json({role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()}),
            props=self.format_json({prop_id: prop.model_dump(mode="json") for prop_id, prop in state.props.items()}),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
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
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
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
            visual_style_label=self.visual_style_label(state),
            bgm_count=bgm_count,
        )
        return await provider.generate_json(
            prompt,
            BGMDesignOutput,
            temperature=0.6,
            metadata={"node_name": "bgm_design", "project_id": state.project_id, "bgm_count": bgm_count},
        )
