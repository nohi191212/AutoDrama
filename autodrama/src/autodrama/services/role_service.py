from __future__ import annotations

import json

from autodrama.core.schemas import ProjectState, RoleDesignOutput, RoleVoiceDesignOutput
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class RoleService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    @staticmethod
    def format_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, indent=2)

    async def role_design(self, state: ProjectState, provider: TextLLM) -> RoleDesignOutput:
        prompt = self.prompts.render(
            "role_design",
            title=state.title,
            raw_script=state.raw_script,
            final_script=self.format_json(state.script.final_script),
            visual_style_label=state.metadata.get("visual_style_label", "真人电影质感"),
            visual_style_prompt=state.metadata.get(
                "visual_style_prompt",
                "真人电影质感：真实摄影、自然光或电影布光、真实材质、真实皮肤纹理和电影镜头语言。",
            ),
        )
        return await provider.generate_json(
            prompt,
            RoleDesignOutput,
            temperature=0.7,
            metadata={"node_name": "role_design", "project_id": state.project_id},
        )

    async def role_voice_design(
        self,
        state: ProjectState,
        provider: TextLLM,
        *,
        available_voices: list[dict[str, object]] | None = None,
    ) -> RoleVoiceDesignOutput:
        available_voices = available_voices or []
        prompt = self.prompts.render(
            "role_voice_design",
            title=state.title,
            final_script=self.format_json(state.script.final_script),
            roles=self.format_json(
                [
                    {
                        "name": role.name,
                        "intro": role.intro,
                        "personality": role.personality,
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
