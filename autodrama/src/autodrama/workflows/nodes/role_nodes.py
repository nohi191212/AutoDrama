from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    AmbientEntityOutput,
    ProjectState,
    RoleDesignItem,
    RoleDesignOutput,
    RoleDuplicateAuditOutput,
    RoleDuplicateAuditReviewOutput,
    RoleDuplicateMergeItem,
    RoleEpisodeKeyAuditItem,
    RoleEpisodeKeyAuditOutput,
    RoleEpisodeKeyAuditReviewOutput,
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

if TYPE_CHECKING:
    from autodrama.workflows.pregen import PregenWorkflow

ROLE_NODE_NAMES = [
    "role_extract_primary",
    "role_extract_functional",
    "role_extract",
    "role_episode_key_audit",
    "role_duplicate_audit",
    "ambient_entity_extract",
    "role_design",
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


class RoleEpisodeKeyAuditNode(RoleNodeBase):
    name = "role_episode_key_audit"
    max_concurrency = 30

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
        pending_additions: list[str] = []
        pending_seen: set[str] = set()
        for value in additions:
            episode_key = str(value or "").strip()
            if not episode_key or episode_key in pending_seen:
                continue
            pending_additions.append(episode_key)
            pending_seen.add(episode_key)
        pending_addition_set = set(pending_additions)
        for episode_key in expected_order:
            if episode_key in pending_addition_set and episode_key not in seen:
                merged.append(episode_key)
                seen.add(episode_key)
        for episode_key in pending_additions:
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
    def _role_item_key(item: RoleExtractItem) -> str:
        return RoleNodeBase.role_name_key(item.name)

    def _load_role_json(self, project_dir: Path, item: RoleExtractItem) -> tuple[Path, dict[str, Any]]:
        role_id = normalize_id("role", item.name)
        path = self.role_designs.item_path(project_dir, role_id)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                return path, payload
        relative_path = self.role_designs.save_extract_item(project_dir, item)
        path = project_dir / relative_path
        payload = json.loads(path.read_text(encoding="utf-8"))
        return path, payload

    def _role_json_for_prompt(self, payload: dict[str, Any], item: RoleExtractItem) -> dict[str, Any]:
        role_json = dict(payload)
        role_json.setdefault("role_name", item.name)
        role_json.setdefault("extract", item.model_dump(mode="json"))
        return role_json

    async def _audit_role(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        item: RoleExtractItem,
        novel_full: dict[str, str],
        expected_episode_keys: list[str],
        semaphore: asyncio.Semaphore,
    ) -> RoleEpisodeKeyAuditItem:
        role_id = normalize_id("role", item.name)
        role_path, role_payload = self._load_role_json(project_dir, item)
        role_json = self._role_json_for_prompt(role_payload, item)
        async with semaphore:
            review = await self.role_service.role_episode_key_audit(
                state,
                provider,
                role_json=role_json,
                novel_full=novel_full,
                episode_keys=expected_episode_keys,
            )
        return self._audit_item_from_review(
            project_dir=project_dir,
            role_id=role_id,
            role_name=item.name,
            role_path=role_path,
            item=item,
            review=review,
            expected_episode_keys=expected_episode_keys,
        )

    def _audit_item_from_review(
        self,
        *,
        project_dir: Path,
        role_id: str,
        role_name: str,
        role_path: Path,
        item: RoleExtractItem,
        review: RoleEpisodeKeyAuditReviewOutput,
        expected_episode_keys: list[str],
    ) -> RoleEpisodeKeyAuditItem:
        expected = set(expected_episode_keys)
        existing_episode_keys = [str(key) for key in item.episode_keys]
        existing_episode_set = set(existing_episode_keys)
        missing_episode_keys = self.dedupe_texts(review.missing_episode_keys)
        missing_episode_set = set(missing_episode_keys)
        ignored_episode_keys = [
            episode_key
            for episode_key in missing_episode_keys
            if episode_key not in expected or episode_key in existing_episode_set
        ]
        added_episode_keys = [
            episode_key
            for episode_key in expected_episode_keys
            if episode_key in missing_episode_set
            and episode_key not in existing_episode_set
        ]
        existing_source_chapters = [str(value) for value in item.source_chapters]
        existing_source_chapter_set = set(existing_source_chapters)
        added_source_chapters = []
        if added_episode_keys:
            added_source_chapters = [
                text
                for text in self.dedupe_texts(review.missing_source_chapters)
                if text not in existing_source_chapter_set
            ]
        return RoleEpisodeKeyAuditItem(
            role_id=role_id,
            role_name=role_name,
            role_json_path=self.repo.layout.project_relative(project_dir, role_path),
            original_episode_keys=existing_episode_keys,
            missing_episode_keys=missing_episode_keys,
            added_episode_keys=added_episode_keys,
            final_episode_keys=self._merge_episode_keys(existing_episode_keys, added_episode_keys, expected_episode_keys),
            original_source_chapters=existing_source_chapters,
            added_source_chapters=added_source_chapters,
            final_source_chapters=self._merge_texts(existing_source_chapters, added_source_chapters),
            ignored_episode_keys=ignored_episode_keys,
            evidence=review.evidence,
            confidence=review.confidence,
        )

    def _apply_audit_item_to_role_json(
        self,
        project_dir: Path,
        audit_item: RoleEpisodeKeyAuditItem,
        expected_episode_keys: list[str],
    ) -> None:
        if not audit_item.added_episode_keys:
            return
        if not audit_item.role_json_path:
            return
        path = project_dir / audit_item.role_json_path
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return
        for key in ("extract", "design", "state_role"):
            section = payload.get(key)
            if not isinstance(section, dict):
                continue
            section["episode_keys"] = self._merge_episode_keys(
                list(section.get("episode_keys") or []),
                audit_item.added_episode_keys,
                expected_episode_keys,
            )
            section["source_chapters"] = self._merge_texts(
                list(section.get("source_chapters") or []),
                audit_item.added_source_chapters,
            )
        self.repo.write_json(path, payload)

    def _apply_audit_items_to_extract_output(
        self,
        output: RoleExtractOutput,
        audit_by_name: dict[str, RoleEpisodeKeyAuditItem],
        expected_episode_keys: list[str],
    ) -> RoleExtractOutput:
        updated_roles: list[RoleExtractItem] = []
        for item in output.roles:
            audit_item = audit_by_name.get(self.role_name_key(item.name))
            if audit_item is None:
                updated_roles.append(item)
                continue
            updated_roles.append(
                item.model_copy(
                    update={
                        "episode_keys": self._merge_episode_keys(
                            item.episode_keys,
                            audit_item.added_episode_keys,
                            expected_episode_keys,
                        ),
                        "source_chapters": self._merge_texts(
                            item.source_chapters,
                            audit_item.added_source_chapters,
                        ),
                    }
                )
            )
        return RoleExtractOutput(roles=updated_roles)

    def _update_extract_node_output_if_exists(
        self,
        project_dir: Path,
        node_name: str,
        audit_by_name: dict[str, RoleEpisodeKeyAuditItem],
        expected_episode_keys: list[str],
    ) -> None:
        path = self.repo.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            return
        output = RoleExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))
        self.repo.save_node_output(
            project_dir,
            node_name,
            self._apply_audit_items_to_extract_output(output, audit_by_name, expected_episode_keys),
        )

    def _update_role_design_output_if_exists(
        self,
        project_dir: Path,
        audit_by_name: dict[str, RoleEpisodeKeyAuditItem],
        expected_episode_keys: list[str],
    ) -> None:
        path = self.role_designs.design_output_path(project_dir)
        if not path.exists():
            return
        output = RoleDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))
        updated_items: list[RoleDesignItem] = []
        for item in output.roles:
            audit_item = audit_by_name.get(self.role_name_key(item.name))
            if audit_item is None:
                updated_items.append(item)
                continue
            updated_items.append(
                item.model_copy(
                    update={
                        "episode_keys": self._merge_episode_keys(
                            item.episode_keys,
                            audit_item.added_episode_keys,
                            expected_episode_keys,
                        ),
                        "source_chapters": self._merge_texts(
                            item.source_chapters,
                            audit_item.added_source_chapters,
                        ),
                    }
                )
            )
        self.repo.save_node_output(project_dir, "role_design", RoleDesignOutput(roles=updated_items))

    def _apply_audit_items(
        self,
        project_dir: Path,
        state: ProjectState,
        audit_items: list[RoleEpisodeKeyAuditItem],
        expected_episode_keys: list[str],
    ) -> None:
        audit_by_name = {
            self.role_name_key(item.role_name): item
            for item in audit_items
            if item.added_episode_keys
        }
        if not audit_by_name:
            return

        self._update_extract_node_output_if_exists(project_dir, "role_extract_primary", audit_by_name, expected_episode_keys)
        self._update_extract_node_output_if_exists(project_dir, "role_extract_functional", audit_by_name, expected_episode_keys)
        self._update_extract_node_output_if_exists(project_dir, "role_extract", audit_by_name, expected_episode_keys)
        self._update_role_design_output_if_exists(project_dir, audit_by_name, expected_episode_keys)

        for audit_item in audit_items:
            if not audit_item.added_episode_keys:
                continue
            self._apply_audit_item_to_role_json(project_dir, audit_item, expected_episode_keys)
            role = state.roles.get(audit_item.role_id)
            if role is not None:
                role.episode_keys = self._merge_episode_keys(role.episode_keys, audit_item.added_episode_keys, expected_episode_keys)
                role.source_chapters = self._merge_texts(role.source_chapters, audit_item.added_source_chapters)

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role", node_name=self.name)
        self.logger.info(
            "node=role_episode_key_audit provider=%s model=%s concurrency=%d",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            self.max_concurrency,
        )
        expected_episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full = self.novel_full_contents(project_dir, state, expected_episode_keys)
        extract_output = self.load_role_extract_node_output(project_dir, "role_extract")
        if not extract_output.roles:
            raise ValueError("role_episode_key_audit requires at least one role from role_extract")

        semaphore = asyncio.Semaphore(self.max_concurrency)
        audit_items = await asyncio.gather(
            *[
                self._audit_role(
                    provider=provider,
                    project_dir=project_dir,
                    state=state,
                    item=item,
                    novel_full=novel_full,
                    expected_episode_keys=expected_episode_keys,
                    semaphore=semaphore,
                )
                for item in extract_output.roles
            ]
        )
        state.budget.used_text_calls += len(audit_items)
        self._apply_audit_items(project_dir, state, list(audit_items), expected_episode_keys)
        output = RoleEpisodeKeyAuditOutput(
            concurrency=self.max_concurrency,
            audited_roles=list(audit_items),
        )
        self.repo.save_node_output(project_dir, self.name, output)
        state.metadata["role_episode_key_audit"] = {
            "checked_roles": len(audit_items),
            "updated_roles": sum(1 for item in audit_items if item.added_episode_keys),
        }
        self.repo.save_state(project_dir, state)
        return state


class RoleDuplicateAuditNode(RoleNodeBase):
    name = "role_duplicate_audit"

    @staticmethod
    def _role_index_items(roles: list[RoleExtractItem]) -> list[dict[str, Any]]:
        return [
            {
                "name": item.name,
                "aliases": item.aliases,
                "role_tier": item.role_tier,
                "episode_keys": item.episode_keys,
                "source_chapters": item.source_chapters,
                "brief": item.brief,
                "appearance_notes": item.appearance_notes,
                "has_dialogue": item.has_dialogue,
                "visual_reuse_required": item.visual_reuse_required,
            }
            for item in roles
        ]

    @staticmethod
    def _merge_episode_keys(existing: list[object], additions: list[object], expected_order: list[str]) -> list[str]:
        return RoleEpisodeKeyAuditNode._merge_episode_keys(existing, additions, expected_order)

    def _review_components(
        self,
        review: RoleDuplicateAuditReviewOutput,
        roles: list[RoleExtractItem],
    ) -> list[tuple[list[str], list[str], list[float]]]:
        roles_by_key = {self.role_name_key(item.name): item for item in roles}
        parent: dict[str, str] = {}
        records: list[tuple[list[str], str | None, float | None]] = []

        def find(key: str) -> str:
            parent.setdefault(key, key)
            if parent[key] != key:
                parent[key] = find(parent[key])
            return parent[key]

        def union(left: str, right: str) -> None:
            left_root = find(left)
            right_root = find(right)
            if left_root != right_root:
                parent[right_root] = left_root

        for group in review.duplicate_groups:
            group_keys: list[str] = []
            seen: set[str] = set()
            for role_name in self.dedupe_texts(group.role_names):
                role_key = self.role_name_key(role_name)
                if role_key not in roles_by_key or role_key in seen:
                    continue
                group_keys.append(role_key)
                seen.add(role_key)
            if len(group_keys) < 2:
                continue
            records.append((group_keys, group.evidence, group.confidence))
            for role_key in group_keys[1:]:
                union(group_keys[0], role_key)

        components: dict[str, set[str]] = {}
        evidence_by_root: dict[str, list[str]] = {}
        confidence_by_root: dict[str, list[float]] = {}
        for group_keys, evidence, confidence in records:
            root = find(group_keys[0])
            bucket = components.setdefault(root, set())
            bucket.update(group_keys)
            if evidence:
                evidence_by_root.setdefault(root, []).append(str(evidence).strip())
            if confidence is not None:
                confidence_by_root.setdefault(root, []).append(float(confidence))

        result: list[tuple[list[str], list[str], list[float]]] = []
        for root, keys in components.items():
            if len(keys) < 2:
                continue
            result.append((list(keys), evidence_by_root.get(root, []), confidence_by_root.get(root, [])))
        return result

    def _merge_plan_from_review(
        self,
        review: RoleDuplicateAuditReviewOutput,
        roles: list[RoleExtractItem],
        expected_episode_keys: list[str],
    ) -> tuple[dict[str, RoleExtractItem], set[str], list[RoleDuplicateMergeItem]]:
        roles_by_key = {self.role_name_key(item.name): item for item in roles}
        role_order = {self.role_name_key(item.name): index for index, item in enumerate(roles)}
        merge_by_key: dict[str, RoleExtractItem] = {}
        removed_keys: set[str] = set()
        merge_items: list[RoleDuplicateMergeItem] = []

        for group_keys, evidences, confidences in self._review_components(review, roles):
            ordered_group_keys = sorted(group_keys, key=lambda key: role_order.get(key, 10**9))
            base_key = min(
                ordered_group_keys,
                key=lambda key: (
                    -len(self.dedupe_texts(roles_by_key[key].episode_keys)),
                    role_order.get(key, 10**9),
                ),
            )
            group_episode_keys: list[str] = []
            original_episode_keys_by_role: dict[str, list[str]] = {}
            for role_key in ordered_group_keys:
                item = roles_by_key[role_key]
                original_episode_keys_by_role[item.name] = list(item.episode_keys)
                group_episode_keys.extend(item.episode_keys)
            final_episode_keys = self._merge_episode_keys([], group_episode_keys, expected_episode_keys)
            base_item = roles_by_key[base_key]
            merge_by_key[base_key] = base_item.model_copy(update={"episode_keys": final_episode_keys})
            for role_key in ordered_group_keys:
                if role_key != base_key:
                    removed_keys.add(role_key)
            merge_items.append(
                RoleDuplicateMergeItem(
                    kept_role_name=base_item.name,
                    removed_role_names=[roles_by_key[key].name for key in ordered_group_keys if key != base_key],
                    original_episode_keys_by_role=original_episode_keys_by_role,
                    final_episode_keys=final_episode_keys,
                    evidence="；".join(self.dedupe_texts(evidences)) or None,
                    confidence=min(confidences) if confidences else None,
                )
            )
        return merge_by_key, removed_keys, merge_items

    def _apply_merge_to_extract_output(
        self,
        output: RoleExtractOutput,
        merge_by_key: dict[str, RoleExtractItem],
        removed_keys: set[str],
    ) -> RoleExtractOutput:
        updated_roles: list[RoleExtractItem] = []
        for item in output.roles:
            role_key = self.role_name_key(item.name)
            if role_key in removed_keys:
                continue
            updated_roles.append(merge_by_key.get(role_key, item))
        return RoleExtractOutput(roles=updated_roles)

    def _update_extract_node_output_if_exists(
        self,
        project_dir: Path,
        node_name: str,
        merge_by_key: dict[str, RoleExtractItem],
        removed_keys: set[str],
    ) -> RoleExtractOutput | None:
        path = self.repo.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            return None
        output = RoleExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))
        updated_output = self._apply_merge_to_extract_output(output, merge_by_key, removed_keys)
        self.repo.save_node_output(project_dir, node_name, updated_output)
        return updated_output

    def _update_role_design_output_if_exists(
        self,
        project_dir: Path,
        merge_by_key: dict[str, RoleExtractItem],
        removed_keys: set[str],
    ) -> None:
        path = self.role_designs.design_output_path(project_dir)
        if not path.exists():
            return
        output = RoleDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))
        updated_items: list[RoleDesignItem] = []
        for item in output.roles:
            role_key = self.role_name_key(item.name)
            if role_key in removed_keys:
                continue
            merged_extract = merge_by_key.get(role_key)
            if merged_extract is None:
                updated_items.append(item)
                continue
            updated_items.append(item.model_copy(update={"episode_keys": merged_extract.episode_keys}))
        self.repo.save_node_output(project_dir, "role_design", RoleDesignOutput(roles=updated_items))

    def _role_json_path_for_item(
        self,
        project_dir: Path,
        state: ProjectState,
        item: RoleExtractItem,
    ) -> Path:
        existing_refs = state.metadata.get("role_refs")
        if isinstance(existing_refs, dict):
            path_ref = existing_refs.get(item.name)
            if isinstance(path_ref, str) and path_ref.strip():
                path = Path(path_ref)
                if not path.is_absolute():
                    path = project_dir / path
                if path.exists():
                    return path
        role_id = normalize_id("role", item.name)
        path = self.role_designs.item_path(project_dir, role_id)
        if path.exists():
            return path
        relative_path = self.role_designs.save_extract_item(project_dir, item)
        return project_dir / relative_path

    def _write_merged_role_json(
        self,
        project_dir: Path,
        state: ProjectState,
        item: RoleExtractItem,
    ) -> str:
        role_id = normalize_id("role", item.name)
        path = self._role_json_path_for_item(project_dir, state, item)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid role JSON: {path}")
        payload["role_id"] = role_id
        payload["role_name"] = item.name
        payload["extract"] = item.model_dump(mode="json")
        for section_name in ("design", "state_role"):
            section = payload.get(section_name)
            if isinstance(section, dict):
                section["episode_keys"] = list(item.episode_keys)
        self.repo.write_json(path, payload)
        return self.repo.layout.project_relative(project_dir, path)

    def _sync_state_after_merge(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        final_roles: list[RoleExtractItem],
        merge_by_key: dict[str, RoleExtractItem],
        removed_keys: set[str],
        removed_role_names: list[str],
    ) -> None:
        removed_roles = [
            role
            for role in state.roles.values()
            if self.role_name_key(role.name) in removed_keys
        ]
        removed_role_ids = {role.id for role in removed_roles}
        removed_role_ids.update(normalize_id("role", name) for name in removed_role_names)
        removed_names = {role.name for role in removed_roles}
        removed_names.update(removed_role_names)
        for merge_item in merge_by_key.values():
            role = state.roles.get(normalize_id("role", merge_item.name))
            if role is not None:
                role.episode_keys = list(merge_item.episode_keys)

        for removed_name in removed_names:
            state.roles.pop(normalize_id("role", removed_name), None)
        if removed_role_ids:
            state.props = {
                prop_id: prop
                for prop_id, prop in state.props.items()
                if prop.owner_role_id not in removed_role_ids
            }

        role_refs: dict[str, str] = {}
        for item in final_roles:
            role_key = self.role_name_key(item.name)
            if role_key in merge_by_key:
                role_refs[item.name] = self._write_merged_role_json(project_dir, state, merge_by_key[role_key])
            else:
                role_refs[item.name] = self.repo.layout.project_relative(
                    project_dir,
                    self._role_json_path_for_item(project_dir, state, item),
                )
        state.metadata["role_refs"] = role_refs

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role", node_name=self.name)
        self.logger.info(
            "node=role_duplicate_audit provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        expected_episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_full = self.novel_full_contents(project_dir, state, expected_episode_keys)
        extract_output = self.load_role_extract_node_output(project_dir, "role_extract")
        if not extract_output.roles:
            raise ValueError("role_duplicate_audit requires at least one role from role_extract")

        review = await self.role_service.role_duplicate_audit(
            state,
            provider,
            novel_full=novel_full,
            role_index=self._role_index_items(extract_output.roles),
        )
        state.budget.used_text_calls += 1
        merge_by_key, removed_keys, merge_items = self._merge_plan_from_review(
            review,
            extract_output.roles,
            expected_episode_keys,
        )

        if merge_items:
            self._update_extract_node_output_if_exists(project_dir, "role_extract_primary", merge_by_key, removed_keys)
            self._update_extract_node_output_if_exists(project_dir, "role_extract_functional", merge_by_key, removed_keys)
            final_extract = self._update_extract_node_output_if_exists(project_dir, "role_extract", merge_by_key, removed_keys)
            if final_extract is None:
                final_extract = self._apply_merge_to_extract_output(extract_output, merge_by_key, removed_keys)
                self.repo.save_node_output(project_dir, "role_extract", final_extract)
            self._update_role_design_output_if_exists(project_dir, merge_by_key, removed_keys)
            self._sync_state_after_merge(
                project_dir,
                state,
                final_roles=final_extract.roles,
                merge_by_key=merge_by_key,
                removed_keys=removed_keys,
                removed_role_names=[
                    role_name
                    for item in merge_items
                    for role_name in item.removed_role_names
                ],
            )
        else:
            final_extract = extract_output

        output = RoleDuplicateAuditOutput(
            checked_roles=len(extract_output.roles),
            merged_groups=merge_items,
            remaining_role_names=[item.name for item in final_extract.roles],
        )
        self.repo.save_node_output(project_dir, self.name, output)
        state.metadata["role_duplicate_audit"] = {
            "checked_roles": len(extract_output.roles),
            "merged_groups": len(merge_items),
            "removed_roles": sum(len(item.removed_role_names) for item in merge_items),
        }
        self.repo.save_state(project_dir, state)
        return state


class AmbientEntityExtractNode(RoleNodeBase):
    name = "ambient_entity_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("role", node_name=self.name)
        self.logger.info(
            "node=ambient_entity_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        expected = set(episode_keys)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        primary_output = self.load_role_extract_node_output(project_dir, "role_extract_primary")
        functional_output = self.load_role_extract_node_output(project_dir, "role_extract_functional")
        output = await self.role_service.ambient_entity_extract(
            state,
            provider,
            novel_full=self.novel_full_contents(project_dir, state, episode_keys),
            primary_roles=self._role_tuples(primary_output.roles),
            functional_roles=self._role_tuples(functional_output.roles),
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
        provider = self.router.text("role", node_name=self.name)
        available_voices: list[dict[str, Any]] = []
        try:
            speech_provider = self.router.audio("speech", node_name="role_design_speech")
            available_voices = self.workflow._available_speakers_for_prompt(speech_provider)
        except Exception as exc:
            self.logger.warning("node=role_design could not load speech voice catalog: %s", exc)
        self.logger.info(
            "node=role_design provider=%s model=%s available_voices=%d voice_selection=deferred",
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
        target_role_keys = {
            self.workflow._role_name_key(item.name)
            for item in target_extract_roles
        }
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
                    if force_pregen and existing_key in target_role_keys:
                        self.logger.info(
                            "role_design %s will be regenerated due to --force",
                            existing_item.name,
                        )
                        continue
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
                        design_path=existing_design_path,
                        preserve_assets=True,
                        validate_voice_sample_text=False,
                    )
                    role = state.roles[role_id]
                    self.role_designs.save_design_item(
                        project_dir,
                        extract_item=extract_item,
                        design_item=existing_item,
                        role=role,
                        bound_props=self._role_bound_props(state, role.id),
                    )
                    designed_by_key[existing_key] = existing_item

        role_index = self._role_index_items(extract_output.roles)
        role_novel_extract: dict[str, str] | None = None
        for extract_item in target_extract_roles:
            role_key = self.workflow._role_name_key(extract_item.name)
            if role_key in designed_by_key and not force_pregen:
                self.logger.info("role_design %s already exists, skipped", extract_item.name)
                continue

            episode_keys = self.workflow._role_episode_keys(extract_item, state)
            if role_novel_extract is None:
                role_novel_extract = self.novel_extract_contents(project_dir, state)
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
                role_novel_extract=role_novel_extract,
                role_novel_full=role_novel_full,
                role_index=role_index,
                designed_role_voices=self._designed_role_voice_refs(
                    extract_output.roles,
                    designed_by_key,
                    exclude_role_name=extract_item.name,
                ),
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
            }
            for item in extract_roles
        ]

    def _designed_role_voice_refs(
        self,
        extract_roles: list[RoleExtractItem],
        designed_by_key: dict[str, RoleDesignItem],
        *,
        exclude_role_name: str,
    ) -> list[dict[str, Any]]:
        excluded_key = self.workflow._role_name_key(exclude_role_name)
        refs: list[dict[str, Any]] = []
        for item in self.workflow._ordered_role_design_items(extract_roles, designed_by_key):
            if self.workflow._role_name_key(item.name) == excluded_key:
                continue
            voices = [
                {
                    "emotion": voice.emotion,
                    "voice_name": voice.voice_name,
                    "voice_type": voice.voice_type,
                    "voice_resource_id": voice.voice_resource_id,
                    "desc": voice.desc,
                    "voice_selection_reason": voice.voice_selection_reason,
                }
                for voice in item.voices
            ]
            if not voices:
                continue
            refs.append(
                {
                    "role_name": item.name,
                    "role_tier": item.role_tier,
                    "has_dialogue": item.has_dialogue,
                    "voices": voices,
                }
            )
        return refs


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
        RoleEpisodeKeyAuditNode.name: RoleEpisodeKeyAuditNode(**deps),
        RoleDuplicateAuditNode.name: RoleDuplicateAuditNode(**deps),
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
    "RoleDuplicateAuditNode",
    "RoleEpisodeKeyAuditNode",
    "RoleExtractNode",
    "RoleFunctionalExtractNode",
    "RolePrimaryExtractNode",
    "build_role_node_runners",
    "build_role_nodes",
]
