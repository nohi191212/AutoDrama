from __future__ import annotations

from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    AmbientEntityOutput,
    ProjectState,
    RoleDesignItem,
    RoleDesignOutput,
    RoleExtractItem,
    RoleExtractOutput,
)
from autodrama.logging import get_logger
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.role_design_repo import RoleDesignRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

ROLE_NODE_NAMES = [
    "role_extract_primary",
    "role_extract_functional",
    "role_extract",
    "ambient_entity_extract",
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

    def _clean_extract_items(
        self,
        output: RoleExtractOutput,
        episode_keys: list[str],
        *,
        iteration: int,
        node_name: str,
        required_tier: str | None = None,
    ) -> list[RoleExtractItem]:
        expected = set(episode_keys)
        seen_names: set[str] = set()
        for item in output.roles:
            item.name = str(item.name or "").strip()
            if not item.name:
                raise ValueError(f"{node_name} iteration {iteration} returned a role with empty name")
            name_key = self.role_name_key(item.name)
            if name_key in seen_names:
                raise ValueError(f"{node_name} iteration {iteration} returned duplicated role name: {item.name}")
            seen_names.add(name_key)
            if required_tier is not None:
                returned_tier = str(item.role_tier or "").strip().lower()
                if "role_tier" in item.model_fields_set and returned_tier != required_tier:
                    raise ValueError(
                        f"{node_name} iteration {iteration} returned role_tier={item.role_tier!r} "
                        f"for {item.name}; expected {required_tier!r}"
                    )
                item.role_tier = required_tier
            item.aliases = self.dedupe_texts(item.aliases)
            item.source_chapters = self.dedupe_texts(item.source_chapters)
            item.appearance_notes = self.dedupe_texts(item.appearance_notes)
            item.episode_keys = self.dedupe_texts(item.episode_keys)
            if item.role_tier == "primary":
                item.visual_reuse_required = True
            invalid_episode_keys = sorted(set(item.episode_keys).difference(expected))
            if invalid_episode_keys:
                raise ValueError(
                    f"{node_name} iteration {iteration} episode_keys for {item.name} must use existing keys; "
                    f"got {', '.join(invalid_episode_keys)}"
                )
            if not item.episode_keys:
                raise ValueError(f"{node_name} iteration {iteration} must include episode_keys for {item.name}")
        return output.roles

    def _merge_extract_item(
        self,
        target: RoleExtractItem,
        incoming: RoleExtractItem,
        episode_keys: list[str],
    ) -> None:
        target.aliases = self.dedupe_texts([*target.aliases, *incoming.aliases])
        target.source_chapters = self.dedupe_texts([*target.source_chapters, *incoming.source_chapters])
        target.appearance_notes = self.dedupe_texts([*target.appearance_notes, *incoming.appearance_notes])
        merged_episode_keys = set([*target.episode_keys, *incoming.episode_keys])
        target.episode_keys = [episode_key for episode_key in episode_keys if episode_key in merged_episode_keys]
        if not target.brief and incoming.brief:
            target.brief = incoming.brief
        target.has_dialogue = target.has_dialogue or incoming.has_dialogue
        target.visual_reuse_required = target.visual_reuse_required or incoming.visual_reuse_required

    @staticmethod
    def _role_tuples(roles: list[RoleExtractItem]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for item in roles:
            name = str(item.name or "").strip()
            if not name:
                continue
            brief = str(item.brief or "").strip()
            if not brief:
                role_tier = str(item.role_tier or "").strip()
                episode_keys = ",".join(item.episode_keys)
                parts = [part for part in (role_tier, episode_keys) if part]
                brief = "；".join(parts) or "已抽取角色"
            result.append((name, brief))
        return result

    def _append_new_roles(
        self,
        *,
        roles: list[RoleExtractItem],
        roles_by_key: dict[str, RoleExtractItem],
        output_roles: list[RoleExtractItem],
        episode_keys: list[str],
    ) -> int:
        new_count = 0
        for item in output_roles:
            name_key = self.role_name_key(item.name)
            existing_item = roles_by_key.get(name_key)
            if existing_item is not None:
                self._merge_extract_item(existing_item, item, episode_keys)
                continue
            roles_by_key[name_key] = item
            roles.append(item)
            new_count += 1
        return new_count

    def load_role_extract_node_output(self, project_dir: Path, node_name: str) -> RoleExtractOutput:
        path = self.repo.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            raise FileNotFoundError(f"{node_name} output is missing; run pregen until role_extract first")
        return RoleExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))


class RolePrimaryExtractNode(RoleNodeBase):
    name = "role_extract_primary"
    max_iterations = 10

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        self.logger.info(
            "node=role_extract_primary provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full = self.novel_full_contents(project_dir, state, episode_keys)
        roles: list[RoleExtractItem] = []
        roles_by_key: dict[str, RoleExtractItem] = {}
        text_call_count = 0

        for iteration in range(1, self.max_iterations + 1):
            output = await self.role_service.role_extract_primary(
                state,
                provider,
                novel_full=novel_full,
                existing_primary_roles=self._role_tuples(roles),
            )
            text_call_count += 1
            output_roles = self._clean_extract_items(
                output,
                episode_keys,
                iteration=iteration,
                node_name=self.name,
                required_tier="primary",
            )
            new_count = self._append_new_roles(
                roles=roles,
                roles_by_key=roles_by_key,
                output_roles=output_roles,
                episode_keys=episode_keys,
            )
            self.logger.info(
                "role_extract_primary iteration %d/%d returned=%d new=%d total=%d",
                iteration,
                self.max_iterations,
                len(output_roles),
                new_count,
                len(roles),
            )
            if new_count == 0:
                break
        else:
            self.logger.warning(
                "role_extract_primary reached max iterations=%d total_roles=%d",
                self.max_iterations,
                len(roles),
            )

        if not roles:
            raise ValueError("role_extract_primary must return at least one primary role")

        state.budget.used_text_calls += text_call_count
        self.repo.save_node_output(project_dir, self.name, RoleExtractOutput(roles=roles))
        return state


class RoleFunctionalExtractNode(RoleNodeBase):
    name = "role_extract_functional"
    max_iterations = 10

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        self.logger.info(
            "node=role_extract_functional provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full = self.novel_full_contents(project_dir, state, episode_keys)
        primary_output = self.load_role_extract_node_output(project_dir, "role_extract_primary")
        primary_roles = self._role_tuples(primary_output.roles)
        roles: list[RoleExtractItem] = []
        roles_by_key: dict[str, RoleExtractItem] = {}
        text_call_count = 0

        for iteration in range(1, self.max_iterations + 1):
            output = await self.role_service.role_extract_functional(
                state,
                provider,
                novel_full=novel_full,
                primary_roles=primary_roles,
                existing_functional_roles=self._role_tuples(roles),
            )
            text_call_count += 1
            output_roles = self._clean_extract_items(
                output,
                episode_keys,
                iteration=iteration,
                node_name=self.name,
                required_tier="functional",
            )
            primary_keys = {
                self.role_name_key(name)
                for primary_item in primary_output.roles
                for name in [primary_item.name, *primary_item.aliases]
            }
            duplicated_primary = [
                item.name
                for item in output_roles
                if self.role_name_key(item.name) in primary_keys
            ]
            if duplicated_primary:
                raise ValueError(
                    "role_extract_functional returned roles that already exist in primary_roles: "
                    f"{', '.join(duplicated_primary)}"
                )
            new_count = self._append_new_roles(
                roles=roles,
                roles_by_key=roles_by_key,
                output_roles=output_roles,
                episode_keys=episode_keys,
            )
            self.logger.info(
                "role_extract_functional iteration %d/%d returned=%d new=%d total=%d",
                iteration,
                self.max_iterations,
                len(output_roles),
                new_count,
                len(roles),
            )
            if new_count == 0:
                break
        else:
            self.logger.warning(
                "role_extract_functional reached max iterations=%d total_roles=%d",
                self.max_iterations,
                len(roles),
            )

        state.budget.used_text_calls += text_call_count
        self.repo.save_node_output(project_dir, self.name, RoleExtractOutput(roles=roles))
        return state


class RoleExtractNode(RoleNodeBase):
    name = "role_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        self.logger.info("node=role_extract merging primary and functional role outputs")
        episode_keys = self.expected_episode_keys(state)
        primary_output = self.load_role_extract_node_output(project_dir, "role_extract_primary")
        functional_output = self.load_role_extract_node_output(project_dir, "role_extract_functional")

        roles: list[RoleExtractItem] = []
        roles_by_key: dict[str, RoleExtractItem] = {}
        for item in self._clean_extract_items(
            primary_output,
            episode_keys,
            iteration=1,
            node_name="role_extract_primary",
            required_tier="primary",
        ):
            name_key = self.role_name_key(item.name)
            roles_by_key[name_key] = item
            roles.append(item)

        for item in self._clean_extract_items(
            functional_output,
            episode_keys,
            iteration=1,
            node_name="role_extract_functional",
            required_tier="functional",
        ):
            name_key = self.role_name_key(item.name)
            if name_key in roles_by_key:
                self.logger.warning(
                    "role_extract merge skipped functional duplicate because primary has priority: %s",
                    item.name,
                )
                continue
            roles_by_key[name_key] = item
            roles.append(item)

        if not roles:
            raise ValueError("role_extract must contain at least one primary or functional role")

        final_output = RoleExtractOutput(roles=roles)
        state.roles = {}
        role_refs: dict[str, str] = {}
        for item in final_output.roles:
            role_refs[item.name] = self.role_designs.save_extract_item(project_dir, item)
        state.metadata["role_refs"] = role_refs
        self.repo.save_node_output(project_dir, self.name, final_output)
        return state


class AmbientEntityExtractNode(RoleNodeBase):
    name = "ambient_entity_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role")
        self.logger.info(
            "node=ambient_entity_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        expected = set(episode_keys)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        output = await self.role_service.ambient_entity_extract(
            state,
            provider,
            novel_full=self.novel_full_contents(project_dir, state, episode_keys),
        )

        seen_names: set[str] = set()
        cleaned_entities = []
        for entity in output.entities:
            entity.name = str(entity.name or "").strip()
            if not entity.name:
                raise ValueError("ambient_entity_extract returned an entity with empty name")
            name_key = self.role_name_key(entity.name)
            if name_key in seen_names:
                raise ValueError(f"ambient_entity_extract returned duplicated entity name: {entity.name}")
            seen_names.add(name_key)
            entity.episode_keys = self.dedupe_texts(entity.episode_keys)
            entity.visual_notes = self.dedupe_texts(entity.visual_notes)
            invalid_episode_keys = sorted(set(entity.episode_keys).difference(expected))
            if invalid_episode_keys:
                raise ValueError(
                    f"ambient_entity_extract episode_keys for {entity.name} must use existing keys; "
                    f"got {', '.join(invalid_episode_keys)}"
                )
            if not entity.episode_keys:
                raise ValueError(f"ambient_entity_extract must include episode_keys for {entity.name}")
            cleaned_entities.append(entity)

        final_output = AmbientEntityOutput(entities=cleaned_entities)
        self.repo.save_node_output(project_dir, self.name, final_output)
        self.repo.write_json(self.repo.layout.ambient_entities_path(project_dir), final_output)
        state.budget.used_text_calls += 1
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
        RolePrimaryExtractNode.name: RolePrimaryExtractNode(**deps),
        RoleFunctionalExtractNode.name: RoleFunctionalExtractNode(**deps),
        RoleExtractNode.name: RoleExtractNode(**deps),
        AmbientEntityExtractNode.name: AmbientEntityExtractNode(**deps),
        RoleDesignNode.name: RoleDesignNode(**deps),
    }


def build_role_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_role_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in ROLE_NODE_NAMES
    ]


__all__ = [
    "AmbientEntityExtractNode",
    "ROLE_NODE_NAMES",
    "RoleDesignNode",
    "RoleExtractNode",
    "RoleFunctionalExtractNode",
    "RolePrimaryExtractNode",
    "build_role_node_runners",
    "build_role_nodes",
]
