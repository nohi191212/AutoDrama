from __future__ import annotations

from autodrama.core.schemas import ProjectState, RoleDesignOutput, RoleVoiceDesignOutput
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class RoleService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    async def role_design(self, state: ProjectState, provider: TextLLM) -> RoleDesignOutput:
        prompt = self.prompts.render(
            "role_design",
            title=state.title,
            final_script=state.script.final_script,
        )
        return await provider.generate_json(
            prompt,
            RoleDesignOutput,
            temperature=0.7,
            metadata={"node_name": "role_design", "project_id": state.project_id},
        )

    async def role_voice_design(self, state: ProjectState, provider: TextLLM) -> RoleVoiceDesignOutput:
        prompt = self.prompts.render(
            "role_voice_design",
            title=state.title,
            final_script=state.script.final_script,
            roles={role_id: role.model_dump(mode="json") for role_id, role in state.roles.items()},
        )
        return await provider.generate_json(
            prompt,
            RoleVoiceDesignOutput,
            temperature=0.6,
            metadata={"node_name": "role_voice_design", "project_id": state.project_id},
        )
