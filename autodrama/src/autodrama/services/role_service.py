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
        return DirectorService.visual_style_prompt(state)

    @classmethod
    def role_character_intro(cls, role_item: RoleExtractItem) -> str:
        payload = {
            "name": role_item.name,
            "role_tier": role_item.role_tier,
            "brief": role_item.brief or "",
            "has_dialogue": role_item.has_dialogue,
        }
        return cls.format_json(payload)

    @staticmethod
    def roleboard_character_description(
        role_item: RoleExtractItem,
        appearance_asset: dict[str, object] | None,
    ) -> str:
        appearance = appearance_asset or {}

        def text_list(value: object) -> list[str]:
            if not isinstance(value, list):
                return []
            return [str(item).strip() for item in value if str(item).strip()]

        lines = [f"角色：{role_item.name}"]
        identity = text_list(appearance.get("identity_invariants"))
        wardrobe = text_list(appearance.get("wardrobe"))
        age = str(appearance.get("age_band") or "").strip()
        period = str(appearance.get("time_period") or "").strip()
        appearance_name = str(appearance.get("appearance_name") or appearance.get("name") or "").strip()
        reference_name = str(appearance.get("reference_asset_name") or "").strip()
        brief = str(role_item.brief or "").strip()

        if appearance_name and appearance_name.casefold() != "base":
            lines.append(f"当前造型：{appearance_name}")
        if brief:
            lines.append(f"角色内容：{brief}")
        if age:
            lines.append(f"年龄阶段：{age}")
        if period:
            lines.append(f"时间阶段：{period}")
        if identity:
            lines.append(f"稳定外形：{'；'.join(identity)}")
        if wardrobe:
            lines.append(f"当前服饰：{'；'.join(wardrobe)}")
        if reference_name:
            lines.append(
                "这是同一角色的变化造型；沿用基础造型的稳定身份，只采用本段明确给出的变化。"
            )
        if len(lines) == 1:
            lines.append("现有资料没有给出更多稳定外形，可做克制、可复用且不增加剧情事实的设计补全。")
        return "\n".join(lines)

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
            "一张横向 16:9 单角色（或单生物）身份板，干净中性背景、留白充足。"
            "构图固定为恰好三个等尺度完整全身视图：正面、侧面、背面。"
            "三个视图中人物保持同一张脸、发型、服装和身体比例，生物保持同一体态、纹理和关键结构；"
            "视图彼此分离、不重叠、不裁切。不要主视觉大图、近景、材质细节格、动作分解、复杂姿势、"
            "额外视角、环境剧情、标签或任何可读文字。"
        )


    async def role_extract_primary(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        novel_full_context: str,
        existing_primary_roles: list[dict[str, object]] | None = None,
    ) -> RoleExtractOutput:
        existing_primary_roles = existing_primary_roles or []
        prompt = self.prompts.render(
            "role_extract_primary",
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
        existing_functional_roles: list[dict[str, object]] | None = None,
    ) -> RoleExtractOutput:
        existing_functional_roles = existing_functional_roles or []
        prompt = self.prompts.render(
            "role_extract_functional",
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
        role_novel_full: dict[str, str],
        role_index: list[dict[str, object]],
        key_vision_asset: dict[str, object] | None = None,
        appearance_asset: dict[str, object] | None = None,
        prompt_variant: str = "default",
        roleboard_image_provider: str | None = None,
        roleboard_image_model: str | None = None,
    ) -> RoleboardPromptModelOutput:
        prompt = self.prompts.render_context(
            "roleboard_prompt",
            {
                "role_extract_item": self.format_json(role_item.model_dump(mode="json")),
                "character_intro": self.role_character_intro(role_item),
                "role_novel_full": self.format_json(role_novel_full),
                "clip_segments": self.clip_segments_context(state, list(role_novel_full)),
                "project_context": DirectorService.project_context(state, episode_keys=list(role_novel_full)),
                "visual_tone": self.visual_tone(state),
                "role_index": self.format_json(role_index),
                "key_vision_asset": self.format_json(key_vision_asset or {}),
                "appearance_asset": self.format_json(appearance_asset or {}),
                "roleboard_style_prompt": self.roleboard_style_prompt(state),
                "roleboard_view_requirement": self.roleboard_view_requirement(),
                "roleboard_character_description": self.roleboard_character_description(
                    role_item,
                    appearance_asset,
                ),
                "roleboard_image_provider": roleboard_image_provider or "",
                "roleboard_image_model": roleboard_image_model or "",
            },
            variant=prompt_variant,
        )
        return await provider.generate_json(
            prompt,
            RoleboardPromptModelOutput,
            temperature=0.45,
            metadata={
                "node_name": "roleboard_prompt",
                "project_id": state.project_id,
                "role_name": role_item.name,
                "prompt_asset_name": "_".join(
                    value
                    for value in (
                        str(role_item.name or "").strip(),
                        str((appearance_asset or {}).get("appearance_name") or (appearance_asset or {}).get("name") or "base").strip(),
                    )
                    if value
                ),
                "episode_keys": list(role_novel_full),
                "key_vision_asset": key_vision_asset or {},
                "appearance_asset": appearance_asset or {},
                "roleboard_prompt_variant": prompt_variant,
                "roleboard_image_provider": roleboard_image_provider,
                "roleboard_image_model": roleboard_image_model,
            },
        )
