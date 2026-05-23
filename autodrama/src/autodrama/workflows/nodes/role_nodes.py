from __future__ import annotations

from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import ProjectState, RoleDesignItem, RoleDesignOutput
from autodrama.logging import get_logger
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.role_design_repo import RoleDesignRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

ROLE_NODE_NAMES = [
    "role_extract",
    "role_design",
]


class RoleNodeBase:
    def __init__(
        self,
        *,
        workflow: Any,
        repo: ProjectRepository,
        router: Any,
        script_service: ScriptService,
        role_service: RoleService,
        script_contents: ScriptContentRepository,
        role_designs: RoleDesignRepository,
        logger: Any,
    ) -> None:
        self.workflow = workflow
        self.repo = repo
        self.router = router
        self.script_service = script_service
        self.role_service = role_service
        self.script_contents = script_contents
        self.role_designs = role_designs
        self.logger = logger

    def expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.episode_keys(self.script_service.episode_count(state))

    def validate_episode_keys(self, label: str, payload: dict[str, object], state: ProjectState) -> None:
        expected_keys = self.expected_episode_keys(state)
        expected = set(expected_keys)
        actual = set(payload)
        if actual != expected:
            raise ValueError(
                f"{label} must contain exactly {', '.join(expected_keys)}; "
                f"got {', '.join(sorted(actual)) or '-'}"
            )

    def novel_full_contents(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str] | None = None,
        *,
        allow_missing: bool = False,
    ) -> dict[str, str]:
        selected_keys = episode_keys or self.expected_episode_keys(state)
        return self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            selected_keys,
            label="script_novel.novel_full",
            allow_missing=allow_missing,
        )

    @staticmethod
    def role_name_key(name: object) -> str:
        return str(name or "").strip().casefold()

    @staticmethod
    def dedupe_texts(values: list[object]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            result.append(text)
            seen.add(text)
        return result


class RoleExtractNode(RoleNodeBase):
    name = "role_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        self.logger.info(
            "node=role_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full = self.novel_full_contents(project_dir, state, episode_keys)
        output = await self.role_service.role_extract(
            state,
            provider,
            novel_full=novel_full,
        )
        if not output.roles:
            raise ValueError("role_extract must return at least one role")

        expected = set(episode_keys)
        seen_names: set[str] = set()
        for item in output.roles:
            item.name = str(item.name or "").strip()
            if not item.name:
                raise ValueError("role_extract returned a role with empty name")
            name_key = self.role_name_key(item.name)
            if name_key in seen_names:
                raise ValueError(f"role_extract returned duplicated role name: {item.name}")
            seen_names.add(name_key)
            item.aliases = self.dedupe_texts(item.aliases)
            item.source_chapters = self.dedupe_texts(item.source_chapters)
            item.appearance_notes = self.dedupe_texts(item.appearance_notes)
            item.episode_keys = self.dedupe_texts(item.episode_keys)
            invalid_episode_keys = sorted(set(item.episode_keys).difference(expected))
            if invalid_episode_keys:
                raise ValueError(
                    f"role_extract episode_keys for {item.name} must use existing keys; "
                    f"got {', '.join(invalid_episode_keys)}"
                )
            if not item.episode_keys:
                raise ValueError(f"role_extract must include episode_keys for {item.name}")

        state.roles = {}
        role_refs: dict[str, str] = {}
        for item in output.roles:
            role_refs[item.name] = self.role_designs.save_extract_item(project_dir, item)
        state.metadata["role_refs"] = role_refs
        state.budget.used_text_calls += 1
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class RoleDesignNode(RoleNodeBase):
    name = "role_design"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        speech_provider = None
        available_voices: list[dict[str, Any]] = []
        try:
            speech_provider = self.router.audio("speech")
            available_voices = self.workflow._available_speakers_for_prompt(speech_provider)
        except Exception as exc:
            self.logger.warning("node=role_design could not load speech voice catalog: %s", exc)
        self.logger.info(
            "node=role_design provider=%s model=%s available_voices=%d",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            len(available_voices),
        )
        extract_output = self.role_designs.load_extract_output(project_dir)
        if not extract_output.roles:
            raise ValueError("role_design requires at least one role from role_extract")

        active_episode_keys = (
            self.workflow._active_episode_keys_in_order(state)
            if getattr(self.workflow, "_active_episode_keys", None)
            else []
        )
        target_extract_roles = self.workflow._role_design_target_extract_roles(extract_output.roles, state)
        if active_episode_keys:
            self.logger.info(
                "role_design episode-scoped rerun episodes=%s target_roles=%s",
                ",".join(active_episode_keys),
                ",".join(item.name for item in target_extract_roles) or "-",
            )
            if not target_extract_roles:
                self.logger.warning(
                    "role_design found no roles appearing in selected episodes: %s",
                    ",".join(active_episode_keys),
                )

        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        previous_roles = dict(state.roles)
        previous_role_props = {
            prop_id: prop
            for prop_id, prop in state.props.items()
            if prop.owner_role_id
        }
        self.workflow._clear_role_design_state(state)

        designed_by_key: dict[str, RoleDesignItem] = {}
        existing_output = self.role_designs.load_existing_output(project_dir)
        if existing_output is not None:
            should_keep_existing = not force_pregen or bool(active_episode_keys)
            if should_keep_existing:
                extract_by_key = {
                    self.workflow._role_name_key(item.name): item
                    for item in extract_output.roles
                }
                for existing_item in existing_output.roles:
                    if not self.workflow._role_design_item_complete(existing_item):
                        continue
                    existing_key = self.workflow._role_name_key(existing_item.name)
                    extract_item = extract_by_key.get(existing_key)
                    if extract_item is not None:
                        try:
                            self.workflow._validate_role_design_item_identity(
                                existing_item,
                                extract_item,
                                extract_output.roles,
                            )
                        except ValueError as exc:
                            self.logger.warning(
                                "role_design ignored existing design for %s: %s",
                                existing_item.name,
                                exc,
                            )
                            continue
                    else:
                        continue
                    existing_design_path = self.role_designs.item_relative_path_for_name(
                        project_dir,
                        existing_item.name,
                    )
                    designed_by_key[existing_key] = existing_item
                    role_id = normalize_id("role", existing_item.name)
                    previous_role = previous_roles.get(role_id)
                    if previous_role is not None:
                        state.roles[role_id] = previous_role
                    for prop_id, prop in previous_role_props.items():
                        if prop.owner_role_id == role_id:
                            state.props[prop_id] = prop
                    self.workflow._apply_role_design_item(
                        project_dir,
                        state,
                        existing_item,
                        speech_provider=speech_provider,
                        design_path=existing_design_path,
                        preserve_assets=True,
                    )
                    role = state.roles[role_id]
                    self.role_designs.save_design_item(
                        project_dir,
                        extract_item=extract_item,
                        design_item=existing_item,
                        role=role,
                        bound_props=self._role_bound_props(state, role.id),
                    )

        all_role_extracts = [item.model_dump(mode="json") for item in extract_output.roles]
        for extract_item in target_extract_roles:
            role_key = self.workflow._role_name_key(extract_item.name)
            if role_key in designed_by_key and not force_pregen:
                self.logger.info("role_design %s already exists, skipped", extract_item.name)
                continue

            episode_keys = self.workflow._role_episode_keys(extract_item, state)
            role_novel_full = self.novel_full_contents(project_dir, state, episode_keys)
            self.logger.info(
                "role_design generating %s from episodes=%s chapters=%s",
                extract_item.name,
                ",".join(episode_keys),
                ",".join(extract_item.source_chapters) or "-",
            )
            output = await self.role_service.role_design(
                state,
                provider,
                role_item=extract_item,
                role_novel_full=role_novel_full,
                all_role_extracts=all_role_extracts,
                existing_role_designs=[
                    item.model_dump(mode="json")
                    for item in self.workflow._ordered_role_design_items(extract_output.roles, designed_by_key)
                ],
                available_voices=available_voices,
            )
            selected_item = self.workflow._select_role_design_item(output, extract_item)
            self.workflow._validate_role_design_item_identity(selected_item, extract_item, extract_output.roles)
            item = self.workflow._merge_role_extract_into_design(selected_item, extract_item)
            design_path = self.role_designs.item_relative_path_for_name(project_dir, item.name)
            self.workflow._apply_role_design_item(
                project_dir,
                state,
                item,
                speech_provider=speech_provider,
                design_path=design_path,
            )
            role = state.roles[normalize_id("role", item.name)]
            design_path = self.role_designs.save_design_item(
                project_dir,
                extract_item=extract_item,
                design_item=item,
                role=role,
                bound_props=self._role_bound_props(state, role.id),
            )
            designed_by_key[role_key] = item
            state.budget.used_text_calls += 1
            state.metadata["role_design_generation_mode"] = "per_role_recursive"
            state.metadata["role_design_active_episode_keys"] = active_episode_keys
            state.metadata["role_design_target_role_names"] = [
                item.name for item in target_extract_roles
            ]
            state.metadata["role_design_designed_role_names"] = [
                item.name for item in self.workflow._ordered_role_design_items(extract_output.roles, designed_by_key)
            ]
            self.repo.save_node_output(
                project_dir,
                self.name,
                RoleDesignOutput(roles=self.workflow._ordered_role_design_items(extract_output.roles, designed_by_key)),
            )
            self.repo.save_state(project_dir, state)

        final_items = self.workflow._ordered_role_design_items(extract_output.roles, designed_by_key)
        state.metadata["role_design_generation_mode"] = "per_role_recursive"
        state.metadata["role_design_active_episode_keys"] = active_episode_keys
        state.metadata["role_design_target_role_names"] = [item.name for item in target_extract_roles]
        state.metadata["role_design_designed_role_names"] = [item.name for item in final_items]
        self.repo.save_node_output(
            project_dir,
            self.name,
            RoleDesignOutput(roles=final_items),
        )
        return state

    @staticmethod
    def _role_bound_props(state: ProjectState, role_id: str) -> list[Any]:
        return [
            prop
            for prop in state.props.values()
            if prop.owner_role_id == role_id
        ]


def build_role_node_runners(workflow: Any) -> dict[str, RoleNodeBase]:
    role_designs = getattr(workflow, "role_designs", None)
    if role_designs is None:
        role_designs = RoleDesignRepository(workflow.repo, workflow.layout)
    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)
    deps = {
        "workflow": workflow,
        "repo": workflow.repo,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "role_service": workflow.role_service,
        "script_contents": script_contents,
        "role_designs": role_designs,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return {
        RoleExtractNode.name: RoleExtractNode(**deps),
        RoleDesignNode.name: RoleDesignNode(**deps),
    }


def build_role_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_role_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in ROLE_NODE_NAMES
    ]


__all__ = [
    "ROLE_NODE_NAMES",
    "RoleDesignNode",
    "RoleExtractNode",
    "build_role_node_runners",
    "build_role_nodes",
]
