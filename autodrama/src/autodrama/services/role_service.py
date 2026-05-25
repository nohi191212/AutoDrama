from __future__ import annotations

import json

from autodrama.core.schemas import (
    AmbientEntityOutput,
    ProjectState,
    RoleDesignOutput,
    RoleDuplicateAuditReviewOutput,
    RoleEpisodeKeyAuditReviewOutput,
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
    def role_design_style_prompt(state: ProjectState) -> str:
        return str(state.metadata.get("role_design_style_prompt") or "").strip()

    @staticmethod
    def role_appearance_view_requirement() -> str:
        return (
            "角色形象分两步生成：先生成单人正面全身图，再以该全身图为身份参考生成同一角色正面、侧面、背面三视图"
            "和绑定物品设计图。三视图必须统一身高比例、脸型、发型、服装和道具细节。"
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

    async def role_episode_key_audit(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        role_json: dict[str, object],
        novel_full: dict[str, str],
        episode_keys: list[str],
    ) -> RoleEpisodeKeyAuditReviewOutput:
        prompt = self.prompts.render(
            "role_episode_key_audit",
            title=state.title,
            role_json=self.format_json(role_json),
            novel_full=self.format_json(novel_full),
            episode_keys=", ".join(episode_keys),
        )
        return await provider.generate_json(
            prompt,
            RoleEpisodeKeyAuditReviewOutput,
            temperature=0.2,
            metadata={
                "node_name": "role_episode_key_audit",
                "project_id": state.project_id,
                "expected_keys": episode_keys,
                "role_name": role_json.get("role_name") or role_json.get("name"),
            },
        )

    async def role_duplicate_audit(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
        role_index: list[dict[str, object]],
    ) -> RoleDuplicateAuditReviewOutput:
        prompt = self.prompts.render(
            "role_duplicate_audit",
            title=state.title,
            novel_full=self.format_json(novel_full),
            role_index=self.format_json(role_index),
        )
        return await provider.generate_json(
            prompt,
            RoleDuplicateAuditReviewOutput,
            temperature=0.2,
            metadata={
                "node_name": "role_duplicate_audit",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
                "role_count": len(role_index),
            },
        )

    async def ambient_entity_extract(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full: dict[str, str],
        primary_roles: list[tuple[str, str]],
        functional_roles: list[tuple[str, str]],
    ) -> AmbientEntityOutput:
        prompt = self.prompts.render(
            "ambient_entity_extract",
            title=state.title,
            raw_script=state.raw_script,
            novel_full=self.format_json(novel_full),
            primary_roles=self.format_json(primary_roles),
            functional_roles=self.format_json(functional_roles),
            episode_keys=", ".join(novel_full),
        )
        return await provider.generate_json(
            prompt,
            AmbientEntityOutput,
            temperature=0.35,
            metadata={
                "node_name": "ambient_entity_extract",
                "project_id": state.project_id,
                "expected_keys": list(novel_full),
                "primary_roles": primary_roles,
                "functional_roles": functional_roles,
            },
        )

    async def role_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        role_item: RoleExtractItem,
        role_novel_extract: dict[str, str],
        role_novel_full: dict[str, str],
        role_index: list[dict[str, object]],
        designed_role_voices: list[dict[str, object]],
        available_voices: list[dict[str, object]] | None = None,
    ) -> RoleDesignOutput:
        available_voices = available_voices or []
        prompt = self.prompts.render(
            "role_design",
            role_extract_item=self.format_json(role_item.model_dump(mode="json")),
            role_novel_extract=self.format_json(role_novel_extract),
            role_novel_full=self.format_json(role_novel_full),
            role_index=self.format_json(role_index),
            designed_role_voices=self.format_json(designed_role_voices),
            available_voices=self.format_json(available_voices),
            role_design_style_prompt=self.role_design_style_prompt(state),
            role_appearance_view_requirement=self.role_appearance_view_requirement(),
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
