from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import ProjectState, Role, RoleAudio
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.utils.prompts import PromptStore

NodeRunner = Callable[[ProjectState], Awaitable[ProjectState]]


PREGEN_NODES = [
    "script_outline",
    "script_detail",
    "script_polish",
    "role_design",
    "role_voice_design",
]


class PregenWorkflow:
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        self.repo = repo
        self.router = router
        self.prompts = prompts or PromptStore()
        self.script_service = ScriptService(self.prompts)
        self.role_service = RoleService(self.prompts)

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "role_voice_design",
        force: bool = False,
    ) -> ProjectState:
        if until not in PREGEN_NODES:
            raise ValueError(f"Unsupported pregen stop node: {until}")

        state = self.repo.load_state(project_dir)
        stop_index = PREGEN_NODES.index(until)
        for node_name in PREGEN_NODES[: stop_index + 1]:
            if not force and node_name in state.completed_nodes:
                continue
            state = await getattr(self, f"_run_{node_name}")(project_dir, state)
            state.mark_completed(node_name)
            self.repo.save_state(project_dir, state)
        return state

    async def _run_script_outline(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        output = await self.script_service.script_outline(state, provider)
        state.script.outline = output.outline
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_outline", output)
        return state

    async def _run_script_detail(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        output = await self.script_service.script_detail(state, provider)
        state.script.detailed_script = output.detailed_script
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_detail", output)
        return state

    async def _run_script_polish(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script")
        output = await self.script_service.script_polish(state, provider)
        state.script.final_script = output.final_script
        state.script.revision_notes = output.revision_notes
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "script_polish", output)
        return state

    async def _run_role_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        output = await self.role_service.role_design(state, provider)
        roles: dict[str, Role] = {}
        for item in output.roles:
            role_id = normalize_id("role", item.name)
            roles[role_id] = Role(
                id=role_id,
                name=item.name,
                intro=item.intro,
                personality=item.personality,
                aliases=item.aliases,
            )
        state.roles = roles
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_design", output)
        return state

    async def _run_role_voice_design(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        output = await self.role_service.role_voice_design(state, provider)
        roles_by_name = {role.name: role for role in state.roles.values()}

        for item in output.role_voices:
            role = roles_by_name.get(item.role_name)
            if role is None:
                continue
            audio_id = normalize_id(f"{role.id}_audio", str(item.emotion))
            role.audio[str(item.emotion)] = RoleAudio(
                id=audio_id,
                role_id=role.id,
                emotion=str(item.emotion),
                desc=item.desc,
                sample_text=item.sample_text,
            )
            normal_voice = role.audio.get("normal")
            if normal_voice:
                role.voice_summary = normal_voice.desc

        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, "role_voice_design", output)
        return state
