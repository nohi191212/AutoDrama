from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import (
    ProjectState,
    Role,
    RoleAppearance,
    RoleAppearanceExtractItem,
    RoleExtractItem,
    RoleExtractOutput,
    RoleFinalizeDuplicateGroup,
    RoleFinalizeDropItem,
    RoleFinalizeEpisodeUpdate,
    RoleFinalizeOutput,
    RoleboardPromptItem,
    RoleboardPromptOutput,
)
from autodrama.logging import get_logger
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.roleboard_prompt_repo import RoleboardPromptRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.role_service import RoleService
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

if TYPE_CHECKING:
    from autodrama.workflows.pregen import PregenWorkflow

ROLE_NODE_NAMES = [
    "role_extract_primary",
    "role_extract_functional",
    "role_finalize",
    "roleboard_prompt",
]


class RoleNodeBase:
    def __init__(
        self,
        *,
        workflow: PregenWorkflow,
        repo: ProjectRepository,
        router: Any,
        script_service: ScriptService,
        role_service: RoleService,
        script_contents: ScriptContentRepository,
        roleboard_prompts: RoleboardPromptRepository,
        logger: Any,
    ) -> None:
        self.workflow = workflow
        self.repo = repo
        self.router = router
        self.script_service = script_service
        self.role_service = role_service
        self.script_contents = script_contents
        self.roleboard_prompts = roleboard_prompts
        self.logger = logger

    def expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.state_episode_keys(state)

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

    def novel_extract_contents(
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
            state.script.novel_extract,
            selected_keys,
            label="script_novel_extract.novel_extract",
            allow_missing=allow_missing,
        )

    def novel_full_context(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str] | None = None,
        *,
        allow_missing: bool = False,
    ) -> str:
        novel_full = self.novel_full_contents(
            project_dir,
            state,
            episode_keys,
            allow_missing=allow_missing,
        )
        return self.role_service.format_novel_full_context(novel_full)

    @staticmethod
    def role_name_key(name: object) -> str:
        return str(name or "").strip().casefold()

    @staticmethod
    def _merge_episode_keys(
        existing: list[object],
        additions: list[object],
        expected_order: list[str],
    ) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for value in existing:
            episode_key = str(value or "").strip()
            if not episode_key or episode_key in seen:
                continue
            merged.append(episode_key)
            seen.add(episode_key)
        pending: list[str] = []
        pending_seen: set[str] = set()
        for value in additions:
            episode_key = str(value or "").strip()
            if not episode_key or episode_key in pending_seen:
                continue
            pending.append(episode_key)
            pending_seen.add(episode_key)
        pending_set = set(pending)
        for episode_key in expected_order:
            if episode_key in pending_set and episode_key not in seen:
                merged.append(episode_key)
                seen.add(episode_key)
        for episode_key in pending:
            if episode_key not in seen:
                merged.append(episode_key)
                seen.add(episode_key)
        return merged

    @staticmethod
    def _merge_texts(existing: list[object], additions: list[object]) -> list[str]:
        merged: list[str] = []
        seen: set[str] = set()
        for value in [*existing, *additions]:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            merged.append(text)
            seen.add(text)
        return merged

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
            raise FileNotFoundError(f"{node_name} output is missing; run pregen until role_finalize first")
        return RoleExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def save_role_extract_progress(
        self,
        project_dir: Path,
        node_name: str,
        roles: list[RoleExtractItem],
    ) -> None:
        self.repo.save_node_output(project_dir, node_name, RoleExtractOutput(roles=roles))


class RolePrimaryExtractNode(RoleNodeBase):
    name = "role_extract_primary"
    max_iterations = 10

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role", node_name=self.name)
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
                novel_full_context=self.role_service.format_novel_full_context(novel_full),
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
            self.save_role_extract_progress(project_dir, self.name, roles)
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
        provider = self.router.text("role", node_name=self.name)
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
                novel_full_context=self.role_service.format_novel_full_context(novel_full),
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
            self.save_role_extract_progress(project_dir, self.name, roles)
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



class RoleFinalizeNode(RoleNodeBase):
    name = "role_finalize"

    def _role_keys(self, item: RoleExtractItem) -> set[str]:
        return {
            key
            for key in [self.role_name_key(item.name), *(self.role_name_key(alias) for alias in item.aliases)]
            if key
        }

    def _role_lookup(self, roles: list[RoleExtractItem]) -> dict[str, RoleExtractItem]:
        lookup: dict[str, RoleExtractItem] = {}
        for item in roles:
            for key in self._role_keys(item):
                lookup.setdefault(key, item)
        return lookup

    def _initial_merge(
        self,
        *,
        primary_roles: list[RoleExtractItem],
        functional_roles: list[RoleExtractItem],
        episode_keys: list[str],
    ) -> tuple[list[RoleExtractItem], list[str]]:
        roles: list[RoleExtractItem] = []
        lookup: dict[str, RoleExtractItem] = {}
        warnings: list[str] = []

        def add_role(item: RoleExtractItem, *, source: str) -> None:
            keys = self._role_keys(item)
            existing = next((lookup[key] for key in keys if key in lookup), None)
            if existing is not None:
                if str(existing.role_tier) == "primary" and str(item.role_tier) == "functional":
                    warnings.append(f"role_finalize skipped functional duplicate of primary role: {item.name}")
                    return
                self._merge_extract_item(existing, item, episode_keys)
                warnings.append(f"role_finalize merged duplicated {source} role by name/alias: {item.name}")
                return
            roles.append(item)
            for key in keys:
                lookup[key] = item

        for item in primary_roles:
            add_role(item, source="primary")
        for item in functional_roles:
            add_role(item, source="functional")
        return roles, warnings

    @staticmethod
    def _role_payloads(roles: list[RoleExtractItem]) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in roles]

    def _resolve_role(self, lookup: dict[str, RoleExtractItem], name: object) -> RoleExtractItem | None:
        return lookup.get(self.role_name_key(name))

    def _apply_episode_updates(
        self,
        *,
        roles: list[RoleExtractItem],
        updates: list[RoleFinalizeEpisodeUpdate],
        episode_keys: list[str],
        warnings: list[str],
    ) -> list[RoleFinalizeEpisodeUpdate]:
        expected = set(episode_keys)
        lookup = self._role_lookup(roles)
        applied: list[RoleFinalizeEpisodeUpdate] = []
        for update in updates:
            role = self._resolve_role(lookup, update.role_name)
            if role is None:
                warnings.append(f"role_finalize ignored episode update for unknown role: {update.role_name}")
                continue
            valid_episode_keys = [key for key in self.dedupe_texts(update.add_episode_keys) if key in expected]
            if not valid_episode_keys:
                continue
            before = list(role.episode_keys)
            role.episode_keys = self._merge_episode_keys(
                role.episode_keys,
                valid_episode_keys,
                episode_keys,
            )
            role.source_chapters = self._merge_texts(
                role.source_chapters,
                update.add_source_chapters,
            )
            added = [key for key in role.episode_keys if key not in before]
            if added:
                applied.append(update.model_copy(update={"add_episode_keys": added}))
        return applied

    def _preferred_kept_role(
        self,
        roles: list[RoleExtractItem],
        group: RoleFinalizeDuplicateGroup,
        lookup: dict[str, RoleExtractItem],
    ) -> RoleExtractItem | None:
        kept = self._resolve_role(lookup, group.kept_role_name)
        if kept is not None:
            return kept
        members = [self._resolve_role(lookup, name) for name in group.role_names]
        members = [item for item in members if item is not None]
        if not members:
            return None
        order = {id(item): index for index, item in enumerate(roles)}
        return min(
            members,
            key=lambda item: (
                0 if str(item.role_tier) == "primary" else 1,
                -len(item.episode_keys),
                order.get(id(item), 10**9),
            ),
        )

    def _apply_duplicate_groups(
        self,
        *,
        roles: list[RoleExtractItem],
        groups: list[RoleFinalizeDuplicateGroup],
        episode_keys: list[str],
        warnings: list[str],
    ) -> tuple[list[RoleExtractItem], list[RoleFinalizeDuplicateGroup]]:
        applied: list[RoleFinalizeDuplicateGroup] = []
        removed_ids: set[int] = set()
        for group in groups:
            lookup = self._role_lookup([item for item in roles if id(item) not in removed_ids])
            members: list[RoleExtractItem] = []
            seen_ids: set[int] = set()
            for name in self.dedupe_texts(group.role_names):
                item = self._resolve_role(lookup, name)
                if item is None or id(item) in seen_ids:
                    continue
                members.append(item)
                seen_ids.add(id(item))
            if len(members) < 2:
                continue
            kept = self._preferred_kept_role(roles, group, lookup)
            if kept is None or kept not in members:
                continue
            removed_names: list[str] = []
            for item in members:
                if item is kept:
                    continue
                self._merge_extract_item(kept, item, episode_keys)
                removed_ids.add(id(item))
                removed_names.append(item.name)
            if removed_names:
                applied.append(
                    group.model_copy(
                        update={
                            "role_names": [kept.name, *removed_names],
                            "kept_role_name": kept.name,
                        }
                    )
                )
        return [item for item in roles if id(item) not in removed_ids], applied

    def _apply_drop_roles(
        self,
        *,
        roles: list[RoleExtractItem],
        drops: list[RoleFinalizeDropItem],
        warnings: list[str],
    ) -> tuple[list[RoleExtractItem], list[RoleFinalizeDropItem]]:
        lookup = self._role_lookup(roles)
        dropped: list[RoleFinalizeDropItem] = []
        drop_ids: set[int] = set()
        for drop in drops:
            item = self._resolve_role(lookup, drop.role_name)
            if item is None:
                continue
            if str(item.role_tier) == "primary":
                warnings.append(f"role_finalize refused to drop primary role: {item.name}")
                continue
            drop_ids.add(id(item))
            dropped.append(drop)
        return [item for item in roles if id(item) not in drop_ids], dropped

    def _save_final_roles(
        self,
        project_dir: Path,
        state: ProjectState,
        roles: list[RoleExtractItem],
    ) -> dict[str, str]:
        role_refs: dict[str, str] = {}
        for item in roles:
            role_refs[item.name] = self.roleboard_prompts.save_extract_item(project_dir, item)
        state.roles = {}
        state.metadata["role_refs"] = role_refs
        return role_refs

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role", node_name=self.name)
        self.logger.info(
            "node=role_finalize provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full_context = self.novel_full_context(project_dir, state, episode_keys)
        primary_output = self.load_role_extract_node_output(project_dir, "role_extract_primary")
        functional_output = self.load_role_extract_node_output(project_dir, "role_extract_functional")
        primary_roles = self._clean_extract_items(
            primary_output,
            episode_keys,
            iteration=1,
            node_name="role_extract_primary",
            required_tier="primary",
        )
        functional_roles = self._clean_extract_items(
            functional_output,
            episode_keys,
            iteration=1,
            node_name="role_extract_functional",
            required_tier="functional",
        )
        roles, warnings = self._initial_merge(
            primary_roles=primary_roles,
            functional_roles=functional_roles,
            episode_keys=episode_keys,
        )
        if not roles:
            raise ValueError("role_finalize requires at least one primary or functional role")

        review = await self.role_service.role_finalize_audit(
            state,
            provider,
            novel_full_context=novel_full_context,
            primary_roles=self._role_payloads(primary_roles),
            functional_roles=self._role_payloads(functional_roles),
        )
        state.budget.used_text_calls += 1
        episode_updates = self._apply_episode_updates(
            roles=roles,
            updates=review.episode_updates,
            episode_keys=episode_keys,
            warnings=warnings,
        )
        roles, duplicate_groups = self._apply_duplicate_groups(
            roles=roles,
            groups=review.duplicate_groups,
            episode_keys=episode_keys,
            warnings=warnings,
        )
        roles, dropped_roles = self._apply_drop_roles(
            roles=roles,
            drops=review.drop_roles,
            warnings=warnings,
        )
        for note in self.dedupe_texts(review.notes):
            warnings.append(note)

        role_refs = self._save_final_roles(project_dir, state, roles)
        self.repo.save_node_output(
            project_dir,
            self.name,
            RoleFinalizeOutput(
                final_roles=roles,
                role_refs=role_refs,
                episode_updates=episode_updates,
                duplicate_groups=duplicate_groups,
                dropped_roles=dropped_roles,
                warnings=self.dedupe_texts(warnings),
            ),
        )
        state.metadata["role_finalize"] = {
            "final_roles": len(roles),
            "episode_updates": len(episode_updates),
            "duplicate_groups": len(duplicate_groups),
            "dropped_roles": len(dropped_roles),
        }
        return state


class RoleboardPromptNode(RoleNodeBase):
    name = "roleboard_prompt"

    @staticmethod
    def _appearance_desc(extract_item: RoleExtractItem) -> str:
        parts = [extract_item.brief or "", *extract_item.appearance_notes]
        return "；".join(text for text in (str(part).strip("； ") for part in parts) if text) or extract_item.name

    @staticmethod
    def _role_intro(extract_item: RoleExtractItem) -> str:
        return str(extract_item.brief or "").strip() or f"{extract_item.name}，已从剧本中抽取的角色。"

    @staticmethod
    def _role_appearance_key(role_name: object, appearance_name: object) -> str:
        return f"{str(role_name or '').strip().casefold()}::{str(appearance_name or '').strip().casefold()}"

    @staticmethod
    def _appearance_name(asset: RoleAppearanceExtractItem) -> str:
        return str(asset.name or "base").strip() or "base"

    def _fallback_appearance_asset(self, extract_item: RoleExtractItem, state: ProjectState) -> RoleAppearanceExtractItem:
        return RoleAppearanceExtractItem(
            name="base",
            asset_role="base",
            episode_keys=self.workflow._role_episode_keys(extract_item, state),
            source_chapters=self.dedupe_texts(extract_item.source_chapters),
            brief="稳定可复用的基础身份造型。",
            appearance_desc=self._appearance_desc(extract_item),
            visual_features=self._appearance_desc(extract_item),
        )

    def _role_appearance_assets(
        self,
        extract_item: RoleExtractItem,
        state: ProjectState,
    ) -> list[RoleAppearanceExtractItem]:
        raw_assets = list(extract_item.appearance_assets or [])
        if not raw_assets:
            return [self._fallback_appearance_asset(extract_item, state)]

        result: list[RoleAppearanceExtractItem] = []
        seen: set[str] = set()
        has_base = False
        for index, asset in enumerate(raw_assets, start=1):
            name = self._appearance_name(asset)
            asset_role = str(asset.asset_role or "base").strip().lower()
            if asset_role not in {"base", "variant"}:
                raise ValueError(f"role {extract_item.name}/{name} has invalid asset_role: {asset_role}")
            if not name:
                name = "base" if not result else f"variant_{index}"
            key = name.casefold()
            if key in seen:
                name = f"{name}_{index}"
                key = name.casefold()
            seen.add(key)
            reference_name = str(asset.reference_asset_name or "").strip() or None
            if asset_role == "base":
                has_base = True
                reference_name = None
            elif not reference_name:
                raise ValueError(f"role variant {extract_item.name}/{name} requires reference_asset_name")
            result.append(
                asset.model_copy(
                    update={
                        "name": name,
                        "asset_role": asset_role,
                        "reference_asset_name": reference_name,
                    }
                )
            )

        if not has_base:
            result.insert(0, self._fallback_appearance_asset(extract_item, state))
        base_names = {asset.name for asset in result if asset.asset_role == "base"}
        for asset in result:
            if asset.asset_role == "variant" and asset.reference_asset_name not in base_names:
                raise ValueError(
                    f"role variant {extract_item.name}/{asset.name} references missing base asset "
                    f"{asset.reference_asset_name}"
                )
        return result

    def _appearance_episode_keys(
        self,
        extract_item: RoleExtractItem,
        appearance_asset: RoleAppearanceExtractItem,
        state: ProjectState,
    ) -> list[str]:
        expected = set(self.workflow._expected_episode_keys(state))
        raw_keys = self.dedupe_texts(appearance_asset.episode_keys) or self.workflow._role_episode_keys(extract_item, state)
        keys = [key for key in raw_keys if key in expected]
        return keys or self.workflow._role_episode_keys(extract_item, state)

    def _appearance_source_chapters(
        self,
        extract_item: RoleExtractItem,
        appearance_asset: RoleAppearanceExtractItem,
    ) -> list[str]:
        return self.dedupe_texts(appearance_asset.source_chapters) or self.dedupe_texts(extract_item.source_chapters)

    def _appearance_desc_for_asset(
        self,
        extract_item: RoleExtractItem,
        appearance_asset: RoleAppearanceExtractItem,
    ) -> str:
        parts = [
            appearance_asset.appearance_desc,
            appearance_asset.brief,
            appearance_asset.visual_features,
            appearance_asset.clothing,
            appearance_asset.prompt_hint,
        ]
        text = "；".join(str(part).strip("； ") for part in parts if str(part or "").strip())
        return text or self._appearance_desc(extract_item)

    def _appearance_asset_payload(
        self,
        extract_item: RoleExtractItem,
        appearance_asset: RoleAppearanceExtractItem,
        state: ProjectState,
    ) -> dict[str, object]:
        return {
            "role_name": extract_item.name,
            "appearance_name": self._appearance_name(appearance_asset),
            "asset_role": appearance_asset.asset_role or "base",
            "reference_asset_name": appearance_asset.reference_asset_name,
            "episode_keys": self._appearance_episode_keys(extract_item, appearance_asset, state),
            "source_chapters": self._appearance_source_chapters(extract_item, appearance_asset),
            "brief": appearance_asset.brief or "",
            "clothing": appearance_asset.clothing or "",
            "visual_features": appearance_asset.visual_features or "",
            "appearance_desc": self._appearance_desc_for_asset(extract_item, appearance_asset),
            "prompt_hint": appearance_asset.prompt_hint or "",
        }

    def _key_vision_asset_for_prompt(self, state: ProjectState) -> dict[str, object]:
        asset = state.metadata.get("key_vision_asset")
        if isinstance(asset, dict):
            return {
                "asset_id": asset.get("asset_id") or state.metadata.get("key_vision_asset_id"),
                "asset_path": asset.get("asset_path") or state.metadata.get("key_vision_asset_path"),
                "asset_url": asset.get("asset_url") or state.metadata.get("key_vision_asset_url"),
                "name": asset.get("name") or state.metadata.get("key_vision_name"),
            }
        return {
            "asset_id": state.metadata.get("key_vision_asset_id"),
            "asset_path": state.metadata.get("key_vision_asset_path"),
            "asset_url": state.metadata.get("key_vision_asset_url"),
            "name": state.metadata.get("key_vision_name"),
        }

    def _node_params(self, node_name: str) -> dict[str, Any]:
        node_settings = self.repo.settings.nodes.get(node_name)
        if node_settings is None:
            return {}
        return dict(node_settings.params)

    def _roleboard_image_binding_context(self) -> dict[str, str]:
        try:
            provider = self.router.image("role", node_name="roleboard_image_generation")
        except Exception as exc:
            self.logger.warning("roleboard_prompt could not inspect roleboard_image_generation provider: %s", exc)
            return {"provider_name": "", "model_name": "", "model_id": ""}
        binding = getattr(provider, "model_binding", None)
        model_name = str(getattr(provider, "model", "") or "")
        purpose_model = None
        purpose_model_resolver = getattr(provider, "_purpose_model", None)
        if callable(purpose_model_resolver):
            try:
                purpose_model = purpose_model_resolver({"node_name": "roleboard_image_generation"})
            except Exception:
                purpose_model = None
        if purpose_model:
            model_name = str(purpose_model)
        return {
            "provider_name": str(getattr(provider, "name", "") or ""),
            "model_name": model_name,
            "model_id": str(getattr(binding, "model_id", "") or ""),
        }

    def _roleboard_prompt_template_candidates(self, image_context: dict[str, str]) -> list[str]:
        params = self._node_params("roleboard_image_generation")
        configured = str(
            params.get("roleboard_prompt_template")
            or params.get("prompt_template")
            or ""
        ).strip()
        if configured:
            return [configured.removesuffix(".md")]

        provider_name = slugify(image_context.get("provider_name", ""), fallback="provider").lower()
        model_name = slugify(image_context.get("model_name", ""), fallback="model").lower()
        model_id = str(image_context.get("model_id", "") or "")
        model_id_slug = slugify(model_id.replace(":", "_"), fallback="model").lower()
        candidates = [
            f"roleboard_prompt/{provider_name}_{model_name}",
            f"roleboard_prompt/{model_id_slug}",
            f"roleboard_prompt/{provider_name}",
            "roleboard_prompt/default",
            "roleboard_prompt",
        ]
        result: list[str] = []
        for candidate in candidates:
            if candidate not in result:
                result.append(candidate)
        return result

    def _resolve_roleboard_prompt_template(self, image_context: dict[str, str]) -> str:
        last_error: FileNotFoundError | None = None
        for template_name in self._roleboard_prompt_template_candidates(image_context):
            path = self.workflow.prompts.prompt_dir / f"{template_name}.md"
            if path.exists():
                return template_name
            last_error = FileNotFoundError(f"Prompt template not found: {path}")
        if last_error is not None:
            raise last_error
        raise FileNotFoundError("No roleboard prompt template candidates were available")

    def _prompt_item_from_model_output(
        self,
        extract_item: RoleExtractItem,
        appearance_asset: RoleAppearanceExtractItem,
        output: Any,
        state: ProjectState,
    ) -> RoleboardPromptItem:
        prompt = str(getattr(output, "roleboard_prompt", "") or "").strip()
        appearance_name = self._appearance_name(appearance_asset)
        if not prompt:
            raise ValueError(
                f"roleboard_prompt returned an empty roleboard_prompt for {extract_item.name}/{appearance_name}"
            )
        role_id = normalize_id("role", extract_item.name)
        appearance_id = normalize_id(f"{role_id}_appearance", appearance_name)
        asset_role = str(appearance_asset.asset_role or "base").strip().lower() or "base"
        reference_name = str(appearance_asset.reference_asset_name or "").strip() or None
        if asset_role == "base":
            reference_name = None
        elif not reference_name:
            raise ValueError(f"role variant {extract_item.name}/{appearance_name} requires reference_asset_name")
        return RoleboardPromptItem(
            role_id=role_id,
            role_name=extract_item.name,
            appearance_id=appearance_id,
            appearance_name=appearance_name,
            asset_role=asset_role,
            reference_asset_name=reference_name,
            role_tier=extract_item.role_tier,
            has_dialogue=extract_item.has_dialogue,
            visual_reuse_required=extract_item.visual_reuse_required,
            episode_keys=self._appearance_episode_keys(extract_item, appearance_asset, state),
            source_chapters=self._appearance_source_chapters(extract_item, appearance_asset),
            role_brief=self._role_intro(extract_item),
            appearance_desc=self._appearance_desc_for_asset(extract_item, appearance_asset),
            clothing=str(appearance_asset.clothing or "").strip() or None,
            visual_features=str(appearance_asset.visual_features or "").strip() or None,
            roleboard_prompt=prompt,
            roleboard_negative_prompt=str(getattr(output, "roleboard_negative_prompt", "") or "").strip() or None,
            voice_profile_prompt=str(getattr(output, "voice_profile_prompt", "") or "").strip() or None,
            design_notes=str(getattr(output, "design_notes", "") or "").strip() or None,
        )

    def _apply_roleboard_prompt_item(
        self,
        state: ProjectState,
        item: RoleboardPromptItem,
        *,
        prompt_path: str | None = None,
        preserve_assets: bool = False,
    ) -> None:
        existing_role = state.roles.get(item.role_id)
        existing_appearance = None
        if existing_role is not None:
            existing_appearance = existing_role.appearances.get(item.appearance_name)
        role = existing_role or Role(
            id=item.role_id,
            name=item.role_name,
            intro=item.role_brief or item.role_name,
        )
        role.name = item.role_name
        role.intro = item.role_brief or role.intro
        role.design_path = prompt_path or role.design_path
        role.role_tier = item.role_tier or role.role_tier
        role.has_dialogue = item.has_dialogue
        role.visual_reuse_required = item.visual_reuse_required
        role.episode_keys = self.dedupe_texts([*role.episode_keys, *item.episode_keys])
        role.source_chapters = self.dedupe_texts([*role.source_chapters, *item.source_chapters])
        role.voice_summary = item.voice_profile_prompt or role.voice_summary
        appearance = RoleAppearance(
            id=item.appearance_id,
            role_id=item.role_id,
            name=item.appearance_name,
            asset_role=item.asset_role,
            reference_asset_name=item.reference_asset_name,
            episode_keys=self.dedupe_texts(item.episode_keys),
            source_chapters=self.dedupe_texts(item.source_chapters),
            clothing=item.clothing,
            visual_features=item.visual_features,
            desc=item.appearance_desc,
            prompt=item.roleboard_prompt,
            roleboard_prompt=item.roleboard_prompt,
            roleboard_negative_prompt=item.roleboard_negative_prompt,
            voice_profile_prompt=item.voice_profile_prompt,
        )
        if preserve_assets and existing_appearance is not None:
            appearance.design_image_generation_status = existing_appearance.design_image_generation_status
            appearance.design_image_asset_id = existing_appearance.design_image_asset_id
            appearance.design_image_asset_path = existing_appearance.design_image_asset_path
            appearance.design_image_asset_url = existing_appearance.design_image_asset_url
            appearance.asset_id = existing_appearance.asset_id
            appearance.asset_path = existing_appearance.asset_path
            appearance.asset_url = existing_appearance.asset_url
            appearance.provider = existing_appearance.provider
            appearance.model = existing_appearance.model
            appearance.request_id = existing_appearance.request_id
            appearance.usage = existing_appearance.usage
        role.appearances[item.appearance_name] = appearance
        state.roles[item.role_id] = role
        if self.workflow._role_needs_voice(role):
            self.workflow._ensure_normal_role_audio(role)
            if item.voice_profile_prompt and "normal" in role.audio:
                role.audio["normal"].desc = item.voice_profile_prompt
                role.voice_summary = item.voice_profile_prompt
        else:
            role.audio = {}
            role.voice_summary = None

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role", node_name=self.name)
        self.logger.info(
            "node=roleboard_prompt provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        extract_output = self.roleboard_prompts.load_extract_output(project_dir)
        if not extract_output.roles:
            raise ValueError("roleboard_prompt requires at least one role from role_finalize")

        active_episode_keys = (
            self.workflow._active_episode_keys_in_order(state)
            if getattr(self.workflow, "_active_episode_keys", None)
            else []
        )
        active_episode_set = {str(key) for key in active_episode_keys}
        target_extract_roles = self.workflow._roleboard_prompt_target_extract_roles(extract_output.roles, state)
        if active_episode_keys:
            self.logger.info(
                "roleboard_prompt episode-scoped rerun episodes=%s target_roles=%s",
                ",".join(active_episode_keys),
                ",".join(item.name for item in target_extract_roles) or "-",
            )

        force_pregen = bool(getattr(self.workflow, "_force_pregen", False))
        target_role_keys = {self.workflow._role_name_key(item.name) for item in target_extract_roles}
        previous_roles = dict(state.roles)
        self.workflow._clear_roleboard_prompt_state(state)

        prompt_by_key: dict[str, RoleboardPromptItem] = {}
        existing_output = self.roleboard_prompts.load_existing_output(project_dir)
        if existing_output is not None and (not force_pregen or bool(active_episode_keys)):
            extract_by_key = {self.workflow._role_name_key(item.name): item for item in extract_output.roles}
            for existing_item in existing_output.prompts:
                existing_role_key = self.workflow._role_name_key(existing_item.role_name)
                if force_pregen and existing_role_key in target_role_keys:
                    self.logger.info(
                        "roleboard_prompt %s/%s will be regenerated due to --force",
                        existing_item.role_name,
                        existing_item.appearance_name,
                    )
                    continue
                extract_item = extract_by_key.get(existing_role_key)
                if extract_item is None:
                    continue
                previous_role = previous_roles.get(existing_item.role_id)
                if previous_role is not None:
                    state.roles[existing_item.role_id] = previous_role
                prompt_path = self.roleboard_prompts.item_relative_path_for_name(project_dir, existing_item.role_name)
                self._apply_roleboard_prompt_item(
                    state,
                    existing_item,
                    prompt_path=prompt_path,
                    preserve_assets=True,
                )
                prompt_by_key[self._role_appearance_key(existing_item.role_name, existing_item.appearance_name)] = existing_item

        role_index = self._role_index_items(extract_output.roles)
        role_novel_extract: dict[str, str] | None = None
        key_vision_asset = self._key_vision_asset_for_prompt(state)
        roleboard_image_context = self._roleboard_image_binding_context()
        prompt_template = self._resolve_roleboard_prompt_template(roleboard_image_context)
        self.logger.info(
            "roleboard_prompt template=%s image_provider=%s image_model=%s",
            prompt_template,
            roleboard_image_context.get("provider_name") or "-",
            roleboard_image_context.get("model_name") or "-",
        )

        for extract_item in target_extract_roles:
            appearance_assets = self._role_appearance_assets(extract_item, state)
            if active_episode_set:
                appearance_assets = [
                    appearance_asset
                    for appearance_asset in appearance_assets
                    if set(self._appearance_episode_keys(extract_item, appearance_asset, state)).intersection(active_episode_set)
                ]
            if not appearance_assets:
                continue

            for appearance_asset in appearance_assets:
                appearance_name = self._appearance_name(appearance_asset)
                prompt_key = self._role_appearance_key(extract_item.name, appearance_name)
                if prompt_key in prompt_by_key and not force_pregen:
                    self.logger.info("roleboard_prompt %s/%s already exists, skipped", extract_item.name, appearance_name)
                    continue

                episode_keys = self._appearance_episode_keys(extract_item, appearance_asset, state)
                if role_novel_extract is None:
                    role_novel_extract = self.novel_extract_contents(project_dir, state)
                role_novel_full = self.novel_full_contents(project_dir, state, episode_keys)
                self.logger.info(
                    "roleboard_prompt generating %s/%s from episodes=%s chapters=%s",
                    extract_item.name,
                    appearance_name,
                    ",".join(episode_keys),
                    ",".join(self._appearance_source_chapters(extract_item, appearance_asset)) or "-",
                )
                output = await self.role_service.roleboard_prompt(
                    state,
                    provider,
                    role_item=extract_item,
                    role_novel_extract=role_novel_extract,
                    role_novel_full=role_novel_full,
                    role_index=role_index,
                    key_vision_asset=key_vision_asset,
                    appearance_asset=self._appearance_asset_payload(extract_item, appearance_asset, state),
                    prompt_template=prompt_template,
                    roleboard_image_provider=roleboard_image_context.get("provider_name"),
                    roleboard_image_model=roleboard_image_context.get("model_name"),
                )
                item = self._prompt_item_from_model_output(extract_item, appearance_asset, output, state)
                prompt_path = self.roleboard_prompts.item_relative_path_for_name(project_dir, item.role_name)
                self._apply_roleboard_prompt_item(state, item, prompt_path=prompt_path)
                prompt_by_key[prompt_key] = item
                state.budget.used_text_calls += 1

                final_items = self.workflow._ordered_roleboard_prompt_items(extract_output.roles, prompt_by_key)
                state.metadata["roleboard_prompt_generation_mode"] = "per_role_appearance_recursive"
                state.metadata["roleboard_prompt_active_episode_keys"] = active_episode_keys
                state.metadata["roleboard_prompt_target_role_names"] = [item.name for item in target_extract_roles]
                state.metadata["roleboard_prompt_designed_role_names"] = [
                    f"{prompt_item.role_name}/{prompt_item.appearance_name}" for prompt_item in final_items
                ]
                role = state.roles[item.role_id]
                role_items = [prompt_item for prompt_item in final_items if prompt_item.role_id == item.role_id]
                self.roleboard_prompts.save_prompt_items(
                    project_dir,
                    extract_item=extract_item,
                    prompt_items=role_items,
                    role=role,
                )
                self.repo.save_node_output(project_dir, self.name, RoleboardPromptOutput(prompts=final_items))
                self.repo.save_state(project_dir, state)

        final_items = self.workflow._ordered_roleboard_prompt_items(extract_output.roles, prompt_by_key)
        for extract_item in extract_output.roles:
            role_id = normalize_id("role", extract_item.name)
            role = state.roles.get(role_id)
            if role is None:
                continue
            role_items = [prompt_item for prompt_item in final_items if prompt_item.role_id == role_id]
            if role_items:
                self.roleboard_prompts.save_prompt_items(
                    project_dir,
                    extract_item=extract_item,
                    prompt_items=role_items,
                    role=role,
                )
        state.metadata["roleboard_prompt_generation_mode"] = "per_role_appearance_recursive"
        state.metadata["roleboard_prompt_active_episode_keys"] = active_episode_keys
        state.metadata["roleboard_prompt_target_role_names"] = [item.name for item in target_extract_roles]
        state.metadata["roleboard_prompt_designed_role_names"] = [
            f"{prompt_item.role_name}/{prompt_item.appearance_name}" for prompt_item in final_items
        ]
        self.repo.save_node_output(project_dir, self.name, RoleboardPromptOutput(prompts=final_items))
        return state

    @staticmethod
    def _role_index_items(extract_roles: list[RoleExtractItem]) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "aliases": item.aliases,
                "role_tier": item.role_tier,
                "episode_keys": item.episode_keys,
                "source_chapters": item.source_chapters,
                "brief": item.brief,
                "appearance_assets": [asset.model_dump(mode="json") for asset in item.appearance_assets],
            }
            for item in extract_roles
        ]



def build_role_node_runners(workflow: Any) -> dict[str, RoleNodeBase]:
    roleboard_prompts = getattr(workflow, "roleboard_prompts", None)
    if roleboard_prompts is None:
        roleboard_prompts = RoleboardPromptRepository(workflow.repo, workflow.layout)
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
        "roleboard_prompts": roleboard_prompts,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return {
        RolePrimaryExtractNode.name: RolePrimaryExtractNode(**deps),
        RoleFunctionalExtractNode.name: RoleFunctionalExtractNode(**deps),
        RoleFinalizeNode.name: RoleFinalizeNode(**deps),
        RoleboardPromptNode.name: RoleboardPromptNode(**deps),
    }


def build_role_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = build_role_node_runners(workflow)
    return [
        WorkflowNode(name=node_name, run=runners[node_name].run)
        for node_name in ROLE_NODE_NAMES
    ]


__all__ = [
    "ROLE_NODE_NAMES",
    "RoleboardPromptNode",
    "RoleFinalizeNode",
    "RoleFunctionalExtractNode",
    "RolePrimaryExtractNode",
    "build_role_node_runners",
    "build_role_nodes",
]
