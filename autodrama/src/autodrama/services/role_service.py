from __future__ import annotations

import json

from autodrama.core.schemas import (
    ProjectState,
    RoleboardPromptModelOutput,
    RoleExtractItem,
    RoleExtractOutput,
    RoleFinalizeAuditReviewOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.services.director_service import DirectorService
from autodrama.utils.prompts import PromptStore


class RoleService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    @staticmethod
    def format_novel_full_context(novel_full: dict[str, str]) -> str:
        sections: list[str] = []
        for episode_key, text in novel_full.items():
            content = str(text or "").strip()
            sections.append(f"【episode_key: {episode_key}】\n{content}")
        return "\n\n".join(sections)

    @staticmethod
    def roleboard_style_prompt(state: ProjectState) -> str:
        return str(state.metadata.get("roleboard_style_prompt") or "").strip()

    @staticmethod
    def visual_tone(state: ProjectState) -> str:
        return str(state.metadata.get("visual_tone") or "").strip()

    @classmethod
    def role_character_intro(cls, role_item: RoleExtractItem) -> str:
        payload = {
            "name": role_item.name,
            "aliases": role_item.aliases,
            "role_tier": role_item.role_tier,
            "brief": role_item.brief or "",
            "appearance_notes": role_item.appearance_notes,
            "has_dialogue": role_item.has_dialogue,
            "visual_reuse_required": role_item.visual_reuse_required,
        }
        return cls.format_json(payload)

    @classmethod
    def clip_segments_context(cls, state: ProjectState, episode_keys: list[str] | None = None) -> str:
        payload = state.metadata.get("clip_segments")
        if not isinstance(payload, dict) or not payload:
            return "（暂无 clip 片段；请以完整小说正文为准。）"
        if episode_keys:
            selected = {str(key) for key in episode_keys}
            payload = {key: value for key, value in payload.items() if str(key) in selected}
        return cls.format_json(payload)

    @staticmethod
    def roleboard_view_requirement() -> str:
        return (
            "角色身份板一次生成：创建艺术性的 16:9 高端动画工作室角色身份板，不是标准网格参考表。"
            "画面使用白色或柔和米白色背景，布局不对称、留白充足、所有角色视角彼此分离且不重叠。"
            "必须包含偏离中心的大型英雄全身视角，并以干净间距加入中性全身、背面、侧面、坐姿、"
            "倾斜姿势、蹲姿、俯视身体角度、仰视身体角度、表情研究、黑色轮廓研究和面部/头发/服装细节研究。"
            "所有视图必须统一同一脸、同一发型、同一服装、同一身体比例、同一姿势语言和同一视觉个性。"
        )


    async def role_extract_primary(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full_context: str,
        existing_primary_roles: list[tuple[str, str]] | None = None,
    ) -> RoleExtractOutput:
        existing_primary_roles = existing_primary_roles or []
        prompt = self.prompts.render(
            "role_extract_primary",
            title=state.title,
            raw_script=state.raw_script,
            novel_full_context=novel_full_context,
            existing_primary_roles=self.format_json(existing_primary_roles),
        )
        return await provider.generate_json(
            prompt,
            RoleExtractOutput,
            temperature=0.35,
            metadata={
                "node_name": "role_extract_primary",
                "project_id": state.project_id,
                "existing_primary_roles": existing_primary_roles,
            },
        )

    async def role_extract_functional(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full_context: str,
        primary_roles: list[tuple[str, str]],
        existing_functional_roles: list[tuple[str, str]] | None = None,
    ) -> RoleExtractOutput:
        existing_functional_roles = existing_functional_roles or []
        prompt = self.prompts.render(
            "role_extract_functional",
            title=state.title,
            raw_script=state.raw_script,
            novel_full_context=novel_full_context,
            primary_roles=self.format_json(primary_roles),
            functional_roles=self.format_json(existing_functional_roles),
        )
        return await provider.generate_json(
            prompt,
            RoleExtractOutput,
            temperature=0.35,
            metadata={
                "node_name": "role_extract_functional",
                "project_id": state.project_id,
                "primary_roles": primary_roles,
                "functional_roles": existing_functional_roles,
            },
        )


    async def role_finalize_audit(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full_context: str,
        primary_roles: list[dict[str, object]],
        functional_roles: list[dict[str, object]],
    ) -> RoleFinalizeAuditReviewOutput:
        prompt = self.prompts.render(
            "role_finalize_audit",
            title=state.title,
            raw_script=state.raw_script,
            novel_full_context=novel_full_context,
            primary_roles=self.format_json(primary_roles),
            functional_roles=self.format_json(functional_roles),
        )
        return await provider.generate_json(
            prompt,
            RoleFinalizeAuditReviewOutput,
            temperature=0.2,
            metadata={
                "node_name": "role_finalize",
                "project_id": state.project_id,
                "primary_role_count": len(primary_roles),
                "functional_role_count": len(functional_roles),
            },
        )


    async def roleboard_prompt(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        role_item: RoleExtractItem,
        role_novel_extract: dict[str, str],
        role_novel_full: dict[str, str],
        role_index: list[dict[str, object]],
        key_vision_asset: dict[str, object] | None = None,
        appearance_asset: dict[str, object] | None = None,
        prompt_template: str = "roleboard_prompt",
        roleboard_image_provider: str | None = None,
        roleboard_image_model: str | None = None,
    ) -> RoleboardPromptModelOutput:
        prompt = self.prompts.render(
            prompt_template,
            role_extract_item=self.format_json(role_item.model_dump(mode="json")),
            character_intro=self.role_character_intro(role_item),
            role_novel_extract=self.format_json(role_novel_extract),
            role_novel_full=self.format_json(role_novel_full),
            clip_segments=self.clip_segments_context(state, list(role_novel_full)),
            project_context=DirectorService.project_context(state, episode_keys=list(role_novel_full)),
            visual_tone=(
                self.visual_tone(state)
                or "（暂无 visual_tone，请只依据人物介绍生成中性、可复用的角色身份板提示词。）"
            ),
            role_index=self.format_json(role_index),
            key_vision_asset=self.format_json(key_vision_asset or {}),
            appearance_asset=self.format_json(appearance_asset or {}),
            roleboard_style_prompt=self.roleboard_style_prompt(state),
            roleboard_view_requirement=self.roleboard_view_requirement(),
            roleboard_image_provider=roleboard_image_provider or "",
            roleboard_image_model=roleboard_image_model or "",
        )
        return await provider.generate_json(
            prompt,
            RoleboardPromptModelOutput,
            temperature=0.45,
            metadata={
                "node_name": "roleboard_prompt",
                "project_id": state.project_id,
                "role_name": role_item.name,
                "episode_keys": list(role_novel_full),
                "key_vision_asset": key_vision_asset or {},
                "appearance_asset": appearance_asset or {},
                "roleboard_prompt_template": prompt_template,
                "roleboard_image_provider": roleboard_image_provider,
                "roleboard_image_model": roleboard_image_model,
            },
        )
