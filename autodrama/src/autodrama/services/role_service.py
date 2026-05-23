from __future__ import annotations

import json

from autodrama.core.schemas import (
    AmbientEntityOutput,
    ProjectState,
    RoleDesignOutput,
    RoleExtractItem,
    RoleExtractOutput,
    RoleVoiceDesignOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class RoleService:
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

    async def role_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
        existing_roles: list[tuple[str, str]] | None = None,
    ) -> RoleExtractOutput:
        existing_roles = existing_roles or []
        prompt = self.prompts.render(
            "role_extract",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            existing_roles=self.format_json(existing_roles),
            episode_keys=", ".join(novel_full),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            RoleExtractOutput,
            temperature=0.4,
            metadata={
                "node_name": "role_extract",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
                "existing_roles": existing_roles,
            },
        )

    async def role_extract_primary(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
        existing_primary_roles: list[tuple[str, str]] | None = None,
    ) -> RoleExtractOutput:
        existing_primary_roles = existing_primary_roles or []
        prompt = self.prompts.render(
            "role_extract_primary",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            existing_primary_roles=self.format_json(existing_primary_roles),
            episode_keys=", ".join(novel_full),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            RoleExtractOutput,
            temperature=0.35,
            metadata={
                "node_name": "role_extract_primary",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
                "existing_primary_roles": existing_primary_roles,
            },
        )

    async def role_extract_functional(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
        primary_roles: list[tuple[str, str]],
        existing_functional_roles: list[tuple[str, str]] | None = None,
    ) -> RoleExtractOutput:
        existing_functional_roles = existing_functional_roles or []
        prompt = self.prompts.render(
            "role_extract_functional",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            primary_roles=self.format_json(primary_roles),
            functional_roles=self.format_json(existing_functional_roles),
            episode_keys=", ".join(novel_full),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            RoleExtractOutput,
            temperature=0.35,
            metadata={
                "node_name": "role_extract_functional",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
                "primary_roles": primary_roles,
                "functional_roles": existing_functional_roles,
            },
        )

    async def ambient_entity_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
    ) -> AmbientEntityOutput:
        prompt = self.prompts.render(
            "ambient_entity_extract",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            episode_keys=", ".join(novel_full),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
        )
        return await provider.generate_json(
            prompt,
            AmbientEntityOutput,
            temperature=0.35,
            metadata={
                "node_name": "ambient_entity_extract",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
            },
        )

    async def role_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        role_item: RoleExtractItem,
        role_novel_full: dict[str, str],
        all_role_extracts: list[dict[str, object]],
        existing_role_designs: list[dict[str, object]],
        available_voices: list[dict[str, object]] | None = None,
    ) -> RoleDesignOutput:
        available_voices = available_voices or []
        prompt = self.prompts.render(
            "role_design",
            title=state.title,
            raw_script=state.raw_script,
            role_extract_item=self.format_json(role_item.model_dump(mode="json")),
            role_novel_full=self.format_json(role_novel_full),
            all_role_extracts=self.format_json(all_role_extracts),
            existing_role_designs=self.format_json(existing_role_designs),
            available_voice_count=len(available_voices),
            available_voices=self.format_json(available_voices),
            visual_style_label=self.visual_style_label(state),
            visual_style_prompt=self.visual_style_prompt(state),
            role_appearance_view_requirement=self.role_appearance_view_requirement(state),
        )
        return await provider.generate_json(
            prompt,
            RoleDesignOutput,
            temperature=0.6,
            metadata={
                "node_name": "role_design",
                "project_id": state.project_id,
                "role_name": role_item.name,
                "episode_keys": list(role_novel_full),
            },
        )

    async def role_voice_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        episode_stories: dict[str, str],
        available_voices: list[dict[str, object]] | None = None,
    ) -> RoleVoiceDesignOutput:
        available_voices = available_voices or []
        prompt = self.prompts.render(
            "role_voice_design",
            title=state.title,
            episode_stories=self.format_json(episode_stories),
            roles=self.format_json(
                [
                    {
                        "name": role.name,
                        "intro": role.intro,
                        "personality": role.personality,
                        "role_tier": role.role_tier,
                        "has_dialogue": role.has_dialogue,
                        "visual_reuse_required": role.visual_reuse_required,
                        "aliases": role.aliases,
                    }
                    for role in state.roles.values()
                ]
            ),
            available_voice_count=len(available_voices),
            available_voices=self.format_json(available_voices),
        )
        return await provider.generate_json(
            prompt,
            RoleVoiceDesignOutput,
            temperature=0.6,
            metadata={"node_name": "role_voice_design", "project_id": state.project_id},
        )
