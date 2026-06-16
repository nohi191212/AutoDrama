from __future__ import annotations

import json

from autodrama.core.schemas import (
    AmbientEntityOutput,
    ProjectState,
    RoleboardPromptModelOutput,
    RoleDuplicateAuditReviewOutput,
    RoleEpisodeKeyAuditReviewOutput,
    RoleExtractItem,
    RoleExtractOutput,
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
    def roleboard_style_prompt(state: ProjectState) -> str:
        return str(state.metadata.get("roleboard_style_prompt") or "").strip()

    @classmethod
    def minute_segments_context(cls, state: ProjectState, episode_keys: list[str] | None = None) -> str:
        payload = state.metadata.get("minute_segments")
        if not isinstance(payload, dict) or not payload:
            return "（暂无分钟片段；请以完整小说正文为准。）"
        if episode_keys:
            selected = {str(key) for key in episode_keys}
            episodes = payload.get("episodes")
            if isinstance(episodes, list):
                payload = {
                    **payload,
                    "episodes": [
                        item
                        for item in episodes
                        if isinstance(item, dict) and str(item.get("episode_key")) in selected
                    ],
                }
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
            minute_segments=self.minute_segments_context(state, list(novel_full)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(novel_full)),
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
            minute_segments=self.minute_segments_context(state, list(novel_full)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(novel_full)),
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
            minute_segments=self.minute_segments_context(state, list(novel_full)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(novel_full)),
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
            minute_segments=self.minute_segments_context(state, list(novel_full)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(novel_full)),
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
            minute_segments=self.minute_segments_context(state, list(novel_full)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(novel_full)),
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
    ) -> RoleboardPromptModelOutput:
        prompt = self.prompts.render(
            "roleboard_prompt",
            role_extract_item=self.format_json(role_item.model_dump(mode="json")),
            role_novel_extract=self.format_json(role_novel_extract),
            role_novel_full=self.format_json(role_novel_full),
            minute_segments=self.minute_segments_context(state, list(role_novel_full)),
            director_prep=DirectorService.director_prep_context(state, episode_keys=list(role_novel_full)),
            role_index=self.format_json(role_index),
            key_vision_asset=self.format_json(key_vision_asset or {}),
            roleboard_style_prompt=self.roleboard_style_prompt(state),
            roleboard_view_requirement=self.roleboard_view_requirement(),
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
            },
        )
