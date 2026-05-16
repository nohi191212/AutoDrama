from __future__ import annotations

from autodrama.core.schemas import (
    ProjectState,
    ScriptDetailOutput,
    ScriptOutlineOutput,
    ScriptPolishOutput,
)
from autodrama.providers.base import TextLLM
from autodrama.utils.prompts import PromptStore


class ScriptService:
    def __init__(self, prompts: PromptStore) -> None:
        self.prompts = prompts

    async def script_outline(self, state: ProjectState, provider: TextLLM) -> ScriptOutlineOutput:
        prompt = self.prompts.render(
            "script_outline",
            title=state.title,
            raw_script=state.raw_script,
        )
        return await provider.generate_json(
            prompt,
            ScriptOutlineOutput,
            temperature=0.7,
            metadata={"node_name": "script_outline", "project_id": state.project_id},
        )

    async def script_detail(self, state: ProjectState, provider: TextLLM) -> ScriptDetailOutput:
        prompt = self.prompts.render(
            "script_detail",
            title=state.title,
            raw_script=state.raw_script,
            outline=state.script.outline or "",
        )
        return await provider.generate_json(
            prompt,
            ScriptDetailOutput,
            temperature=0.7,
            metadata={"node_name": "script_detail", "project_id": state.project_id},
        )

    async def script_polish(self, state: ProjectState, provider: TextLLM) -> ScriptPolishOutput:
        prompt = self.prompts.render(
            "script_polish",
            title=state.title,
            detailed_script=state.script.detailed_script,
        )
        return await provider.generate_json(
            prompt,
            ScriptPolishOutput,
            temperature=0.7,
            metadata={"node_name": "script_polish", "project_id": state.project_id},
        )
