from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Callable

from autodrama.core.schemas import (
    ClipSegmentNodeOutput,
    ClipSegmentOutput,
    ProjectState,
    ScriptNovelExtractOutput,
    ScriptNovelOutput,
    ScriptOutlineOutput,
)
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

SCRIPT_NODE_NAMES = [
    "script_import",
    "script_detail_expand",
    "script_novel_extract",
]
MANUAL_SCRIPT_NODE_NAMES = [
    "script_outline",
    "script_novel",
]
SCRIPT_COMPLETION_ALIASES = {
    "script_import": ("script_outline",),
    "script_detail_expand": ("script_novel",),
}



class ScriptNodeBase:
    def __init__(
        self,
        *,
        workflow: Any | None = None,
        repo: ProjectRepository,
        layout: ProjectLayout,
        router: Any,
        script_service: ScriptService,
        script_contents: ScriptContentRepository,
        logger: Any,
        force_getter: Callable[[], bool] | None = None,
    ) -> None:
        self.workflow = workflow
        self.repo = repo
        self.layout = layout
        self.router = router
        self.script_service = script_service
        self.script_contents = script_contents
        self.logger = logger
        self.force_getter = force_getter or (lambda: False)

    def expected_episode_keys(self, state: ProjectState) -> list[str]:
        return self.script_service.state_episode_keys(state)

    def mark_completion_aliases(self, state: ProjectState) -> None:
        if self.name == "script_outline":
            state.mark_completed("script_import")
        elif self.name == "script_novel":
            state.mark_completed("script_detail_expand")

    def target_episode_keys(self, state: ProjectState) -> list[str]:
        getter = getattr(self.workflow, "_active_episode_keys_in_order", None)
        if callable(getter):
            active_episode_keys = list(getter(state))
            if active_episode_keys:
                return active_episode_keys
        return self.expected_episode_keys(state)

    def validate_episode_keys(self, label: str, payload: dict[str, object], state: ProjectState) -> None:
        expected_keys = self.expected_episode_keys(state)
        expected = set(expected_keys)
        actual = set(payload)
        if actual != expected:
            raise ValueError(
                f"{label} must contain exactly {', '.join(expected_keys)}; "
                f"got {', '.join(sorted(actual)) or '-'}"
            )

    @staticmethod
    def validate_script_outline(output: ScriptOutlineOutput) -> dict[str, str]:
        if not str(output.outline or "").strip():
            raise ValueError("script_outline outline is empty")
        episode_outlines = {
            str(key).strip(): str(value or "").strip()
            for key, value in dict(output.episode_outlines or {}).items()
            if str(key).strip()
        }
        if not episode_outlines:
            raise ValueError("script_outline episode_outlines is empty")
        invalid_keys = [
            key
            for key in episode_outlines
            if re.fullmatch(r"episode_\d{3}", key) is None
        ]
        if invalid_keys:
            raise ValueError(
                "script_outline episode_outlines keys must use episode_001 format; "
                f"got {', '.join(invalid_keys)}"
            )
        empty_keys = [key for key, value in episode_outlines.items() if not value]
        if empty_keys:
            raise ValueError(f"script_outline episode_outlines contains empty text: {', '.join(empty_keys)}")
        return episode_outlines

    @staticmethod
    def format_previous_novel_chapters(novel_full: dict[str, str], episode_keys: list[str]) -> str:
        chunks: list[str] = []
        for episode_key in episode_keys:
            text = str(novel_full.get(episode_key) or "").strip()
            if text:
                chunks.append(f"## {episode_key}\n{text}")
        return "\n\n".join(chunks) if chunks else "（暂无，当前是第一章。）"


class ScriptImportNode(ScriptNodeBase):
    name = "script_import"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        episode_keys = self.expected_episode_keys(state)
        if len(episode_keys) != 1:
            raise ValueError(
                "script_import currently imports one mature screenplay as one episode; "
                f"got episode keys {', '.join(episode_keys)}. "
                "Use pregen --only script_outline and pregen --only script_novel for generated multi-episode scripts."
            )
        episode_key = episode_keys[0]
        source_script = str(state.raw_script or state.script.raw_script or "").strip()
        if not source_script:
            raise ValueError("script_import raw_script is empty")

        provider = self.router.text("script", node_name=self.name)
        self.logger.info(
            "node=script_import provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_import(
            state,
            provider,
            raw_script=source_script,
        )
        outline = str(output.outline or "").strip()
        if not outline:
            raise ValueError("script_import outline is empty")
        episode_outlines = [
            str(value or "").strip()
            for value in list(output.episode_outlines or [])
            if str(value or "").strip()
        ]
        if not episode_outlines:
            episode_outlines = [outline]
        episode_outline_content = "\n\n".join(
            f"第{index}集：{content}"
            for index, content in enumerate(episode_outlines, start=1)
        )

        state.raw_script = source_script
        state.script.raw_script = source_script
        state.script.outline = outline
        state.script.episode_outlines = {
            episode_key: self.script_contents.write_content(
                project_dir,
                "outlines",
                episode_key,
                node_name=self.name,
                content=episode_outline_content,
            )
        }
        source_script_file = state.metadata.get("source_script_file")
        state.script.novel_full = {
            episode_key: self.script_contents.write_content(
                project_dir,
                "novel_full",
                episode_key,
                node_name=self.name,
                content=source_script,
                dependency_field="source_script_file",
                dependency_path=str(source_script_file) if source_script_file else None,
            )
        }
        state.script.novel_extract = {episode_key: state.script.novel_extract.get(episode_key) or False}
        roles = [role.model_dump(mode="json") for role in output.roles]
        props = [prop.model_dump(mode="json") for prop in output.props]
        layouts = [layout.model_dump(mode="json") for layout in output.layouts]
        state.metadata.update(
            {
                "script_mode": "mature_script",
                "mature_script_imported_episode_key": episode_key,
                "script_import_source_file": str(source_script_file) if source_script_file else None,
                "script_import_episode_keys": [episode_key],
                "script_import_episode_outlines": episode_outlines,
                "script_import_roles": roles,
                "script_import_props": props,
                "script_import_layouts": layouts,
                "script_import_notes": output.notes,
                "script_novel_full_episode_paths": dict(state.script.novel_full),
            }
        )
        self.repo.save_node_output(
            project_dir,
            self.name,
            {
                "outline": outline,
                "episode_outlines": episode_outlines,
                "episode_outline_paths": state.script.episode_outlines,
                "novel_full": state.script.novel_full,
                "roles": roles,
                "props": props,
                "layouts": layouts,
                "notes": output.notes,
                "imported_mature_script": True,
            },
        )
        state.budget.used_text_calls += 1
        return state


class ScriptDetailExpandNode(ScriptNodeBase):
    name = "script_detail_expand"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script", node_name=self.name)
        self.logger.info(
            "node=script_detail_expand provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_import.novel_full", state.script.novel_full, state)
        imported_contents = self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            episode_keys,
            label="script_import.novel_full",
        )
        outline_contents = self.script_contents.load_contents(
            project_dir,
            state.script.episode_outlines,
            episode_keys,
            label="script_import.episode_outlines",
            allow_missing=True,
        )

        expanded_paths: dict[str, str] = {}
        expanded_outputs: dict[str, dict[str, object]] = {}
        source_paths = dict(state.script.novel_full)
        source_script_file = state.metadata.get("source_script_file")
        for episode_key in episode_keys:
            raw_script = str(imported_contents.get(episode_key) or "").strip()
            if not raw_script:
                raise ValueError(f"script_detail_expand raw script is empty for {episode_key}")
            detail_expand_output = await self.script_service.script_detail_expand(
                state,
                provider,
                episode_key=episode_key,
                raw_script=raw_script,
                episode_outline=outline_contents.get(episode_key, ""),
            )
            expanded_script = detail_expand_output.expanded_script.strip()
            if not expanded_script:
                raise ValueError(f"script_detail_expand returned empty expanded_script for {episode_key}")

            expanded_paths[episode_key] = self.script_contents.write_content(
                project_dir,
                "novel_full",
                episode_key,
                node_name=self.name,
                content=expanded_script,
                dependency_field="source_script_file" if source_script_file else "source_script_path",
                dependency_path=str(source_script_file) if source_script_file else source_paths.get(episode_key),
            )
            expanded_outputs[episode_key] = detail_expand_output.model_dump(mode="json")
            state.budget.used_text_calls += 1

        state.script.novel_full = expanded_paths
        state.metadata.update(
            {
                "mature_script_detail_expanded": True,
                "script_novel_full_episode_paths": dict(state.script.novel_full),
            }
        )
        self.repo.save_node_output(
            project_dir,
            self.name,
            {
                "detail_expanded": True,
                "novel_full": state.script.novel_full,
                "episodes": expanded_outputs,
            },
        )
        return state


class ScriptOutlineNode(ScriptNodeBase):
    name = "script_outline"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script", node_name=self.name)
        self.logger.info(
            "node=script_outline provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        output = await self.script_service.script_outline(state, provider)
        episode_outlines = self.validate_script_outline(output)
        outline = str(output.outline or "").strip()
        state.script.outline = outline
        state.script.episode_outlines = {}
        ordered_episode_keys = self.script_service.sort_episode_keys(list(episode_outlines))
        for episode_key in ordered_episode_keys:
            content = episode_outlines[episode_key]
            outline_path = self.script_contents.content_path(project_dir, "outlines", episode_key)
            self.repo.write_json(
                outline_path,
                self.script_contents.content_payload(
                    node_name="script_outline",
                    episode_key=episode_key,
                    content=str(content).strip(),
                ),
            )
            state.script.episode_outlines[episode_key] = self.script_contents.project_relative(project_dir, outline_path)
        state.metadata["episode_count"] = len(ordered_episode_keys)
        state.metadata["script_outline_episode_keys"] = ordered_episode_keys
        state.budget.used_text_calls += 1
        self.repo.save_node_output(
            project_dir,
            self.name,
            {
                "outline": outline,
                "episode_outlines": state.script.episode_outlines,
            },
        )
        self.mark_completion_aliases(state)
        return state


class ScriptNovelNode(ScriptNodeBase):
    name = "script_novel"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script", node_name=self.name)
        self.logger.info(
            "node=script_novel provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        outline_contents = self.script_contents.load_contents(
            project_dir,
            state.script.episode_outlines,
            episode_keys,
            label="script_outline.episode_outlines",
        )
        target_char_count = self.script_service.episode_target_char_count(state)
        force_pregen = self.force_getter()
        if force_pregen:
            state.script.novel_full = {episode_key: False for episode_key in episode_keys}
        else:
            state.script.novel_full = {
                episode_key: state.script.novel_full.get(episode_key) or False
                for episode_key in episode_keys
            }
        state.metadata["script_novel_target_char_count"] = target_char_count
        expected_episode_keys = set(episode_keys)
        state.metadata["script_novel_full_episode_paths"] = (
            {
                key: value
                for key, value in dict(state.metadata.get("script_novel_full_episode_paths") or {}).items()
                if key in expected_episode_keys
            }
            if not force_pregen
            else {}
        )

        novel_contents: dict[str, str] = {}
        for episode_index, episode_key in enumerate(episode_keys, start=1):
            episode_path = self.script_contents.content_path(project_dir, "novel_full", episode_key)
            legacy_full_path = self.script_contents.novel_legacy_full_path(project_dir, episode_key)
            legacy_episode_path = self.script_contents.novel_legacy_episode_path(project_dir, episode_key)
            existing_text = None
            if not force_pregen:
                existing_text = self.script_contents.load_content_ref(project_dir, state.script.novel_full.get(episode_key))
                if not existing_text:
                    existing_text = self.script_contents.load_episode_text(episode_path)
                if not existing_text:
                    existing_text = self.script_contents.load_episode_text(legacy_full_path)
                if not existing_text:
                    existing_text = self.script_contents.load_episode_text(legacy_episode_path)

            if not force_pregen and existing_text:
                if not episode_path.exists():
                    self.repo.write_json(
                        episode_path,
                        self.script_contents.content_payload(
                            node_name=self.name,
                            episode_key=episode_key,
                            content=existing_text,
                            dependency_field="source_outline_path",
                            dependency_path=state.script.episode_outlines.get(episode_key),
                        ),
                    )
                novel_contents[episode_key] = existing_text
                state.script.novel_full[episode_key] = self.script_contents.project_relative(project_dir, episode_path)
                state.metadata["script_novel_full_episode_paths"][episode_key] = self.script_contents.project_relative(
                    project_dir,
                    episode_path,
                )
                self.repo.save_state(project_dir, state)
                self.logger.info(
                    "script_novel %s already exists, skipped; saved in %s",
                    episode_key,
                    self.script_contents.project_relative(project_dir, episode_path),
                )
                continue

            previous_chapters = self.format_previous_novel_chapters(
                novel_contents,
                episode_keys[: episode_index - 1],
            )
            output = await self.script_service.script_novel_episode(
                state,
                provider,
                episode_key=episode_key,
                episode_outlines=outline_contents,
                current_episode_outline=outline_contents.get(episode_key, ""),
                previous_chapters=previous_chapters,
            )
            novel_full = output.novel_full.strip()
            if not novel_full:
                raise ValueError(f"script_novel novel_full is empty for {episode_key}")

            novel_contents[episode_key] = novel_full
            state.budget.used_text_calls += 1
            self.repo.write_json(
                episode_path,
                self.script_contents.content_payload(
                    node_name=self.name,
                    episode_key=episode_key,
                    content=novel_full,
                    dependency_field="source_outline_path",
                    dependency_path=state.script.episode_outlines.get(episode_key),
                ),
            )
            state.script.novel_full[episode_key] = self.script_contents.project_relative(project_dir, episode_path)
            state.metadata["script_novel_full_episode_paths"][episode_key] = self.script_contents.project_relative(
                project_dir,
                episode_path,
            )
            self.repo.save_state(project_dir, state)
            self.logger.info(
                "script_novel %s generated target_chars=%d saved in %s",
                episode_key,
                target_char_count,
                self.script_contents.project_relative(project_dir, episode_path),
            )

        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        output = ScriptNovelOutput(novel_full=state.script.novel_full)
        self.repo.save_node_output(project_dir, self.name, output)
        self.mark_completion_aliases(state)
        return state


class ScriptNovelExtractNode(ScriptNodeBase):
    name = "script_novel_extract"
    DEFAULT_CONCURRENCY = 5

    @staticmethod
    def _concurrency(provider: Any) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        for source in (params, options):
            for key in ("script_novel_extract_concurrency", "concurrency", "text_concurrency"):
                try:
                    value = int(source.get(key) or 0)
                except (AttributeError, TypeError, ValueError):
                    continue
                if value > 0:
                    return max(1, value)
        return ScriptNovelExtractNode.DEFAULT_CONCURRENCY

    async def _extract_one_episode(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        script_novel_full: str,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, str]:
        async with semaphore:
            output = await self.script_service.script_novel_extract(
                state,
                provider,
                episode_key=episode_key,
                script_novel_full=script_novel_full,
            )
        extracted_text = str(output.script_novel_extract or "").strip()
        if not extracted_text:
            raise ValueError(f"script_novel_extract output is empty for {episode_key}")
        episode_path = self.script_contents.content_path(project_dir, "novel_extract", episode_key)
        self.repo.write_json(
            episode_path,
            self.script_contents.content_payload(
                node_name=self.name,
                episode_key=episode_key,
                content=extracted_text,
                dependency_field="source_novel_full_path",
                dependency_path=state.script.novel_full.get(episode_key),
            ),
        )
        return episode_key, self.script_contents.project_relative(project_dir, episode_path)

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script", node_name=self.name)
        concurrency = self._concurrency(provider)
        self.logger.info(
            "node=script_novel_extract provider=%s model=%s concurrency=%d",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            concurrency,
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_contents = self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            episode_keys,
            label="script_novel.novel_full",
        )
        force_pregen = self.force_getter()
        if force_pregen:
            state.script.novel_extract = {episode_key: False for episode_key in episode_keys}
        else:
            state.script.novel_extract = {
                episode_key: state.script.novel_extract.get(episode_key) or False
                for episode_key in episode_keys
            }
        state.metadata["script_novel_extract_concurrency"] = concurrency

        extract_contents = (
            {}
            if force_pregen
            else self.script_contents.load_contents(
                project_dir,
                state.script.novel_extract,
                episode_keys,
                label="script_novel_extract.novel_extract",
                allow_missing=True,
            )
        )
        pending_keys = [episode_key for episode_key in episode_keys if not extract_contents.get(episode_key)]
        if pending_keys:
            self.logger.info("script_novel_extract pending episodes=%s", ",".join(pending_keys))
            semaphore = asyncio.Semaphore(concurrency)
            results = await asyncio.gather(
                *[
                    self._extract_one_episode(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        episode_key=episode_key,
                        script_novel_full=novel_contents[episode_key],
                        semaphore=semaphore,
                    )
                    for episode_key in pending_keys
                ]
            )
            for episode_key, episode_path in results:
                state.script.novel_extract[episode_key] = episode_path
            state.budget.used_text_calls += len(results)
            self.repo.save_state(project_dir, state)
            self.logger.info("script_novel_extract generated episodes=%d", len(results))
        else:
            self.logger.info("script_novel_extract all episodes already exist, skipped")

        self.validate_episode_keys("script_novel_extract.novel_extract", state.script.novel_extract, state)
        output = ScriptNovelExtractOutput(novel_extract=state.script.novel_extract)
        self.repo.save_node_output(project_dir, self.name, output)
        return state

class ClipSegmentNode(ScriptNodeBase):
    name = "clip_segment"
    MIN_CLIP_SECONDS = 8
    MAX_CLIP_SECONDS = 15
    SHORT_TEXT_WARNING_CHARS = 20
    LONG_TEXT_WARNING_CHARS = 900

    @staticmethod
    def _one_line(value: object, *, max_chars: int = 120) -> str:
        text = " ".join(str(value or "").split()).strip()
        if len(text) <= max_chars:
            return text
        return text[: max_chars - 1].rstrip() + "..."

    @classmethod
    def _episode_matches(cls, episode_keys: object, episode_key: str) -> bool:
        if not episode_keys:
            return True
        if not isinstance(episode_keys, list):
            return True
        return episode_key in {str(key) for key in episode_keys if str(key).strip()}

    @classmethod
    def _format_asset_index(cls, items: dict[str, str]) -> str:
        if not items:
            return "（无）"
        return "\n".join(f"{name}: {intro}" for name, intro in items.items())

    @staticmethod
    def _item_intro(item: dict[str, object]) -> str:
        for key in ("brief", "intro", "desc", "description", "prompt"):
            value = item.get(key)
            if str(value or "").strip():
                return str(value)
        notes = item.get("appearance_notes") or item.get("visual_notes")
        if isinstance(notes, list) and notes:
            return "；".join(str(note) for note in notes[:3] if str(note).strip())
        return ""

    def _load_node_payload(self, project_dir: Path, node_name: str) -> dict[str, object]:
        path = self.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            raise FileNotFoundError(f"{node_name} output is missing; run pregen through {node_name} before clip_segment")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid {node_name} output JSON: {path}")
        return payload

    def _load_first_node_payload(self, project_dir: Path, node_names: tuple[str, ...]) -> dict[str, object]:
        for node_name in node_names:
            path = self.layout.node_output_path(project_dir, node_name)
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(payload, dict):
                    raise ValueError(f"Invalid {node_name} output JSON: {path}")
                return payload
        expected = ", ".join(node_names)
        raise FileNotFoundError(f"{expected} output is missing; run pregen through {node_names[-1]} before clip_segment")
    @classmethod
    def _list_items_index(cls, items: object, episode_key: str) -> dict[str, str]:
        result: dict[str, str] = {}
        if not isinstance(items, list):
            return result
        for raw_item in items:
            if not isinstance(raw_item, dict):
                continue
            if not cls._episode_matches(raw_item.get("episode_keys"), episode_key):
                continue
            name = str(raw_item.get("name") or "").strip()
            intro = cls._one_line(cls._item_intro(raw_item))
            if name and intro:
                result[name] = intro
        return result

    @classmethod
    def _mapping_index(cls, mapping: object) -> dict[str, str]:
        if not isinstance(mapping, dict):
            return {}
        return {
            str(name).strip(): cls._one_line(intro)
            for name, intro in mapping.items()
            if str(name).strip() and str(intro or "").strip()
        }

    @classmethod
    def _state_assets_index(cls, assets: object, episode_key: str, *, intro_attr: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for asset in dict(assets or {}).values():
            episode_keys = getattr(asset, "episode_keys", [])
            if not cls._episode_matches(episode_keys, episode_key):
                continue
            name = str(getattr(asset, "name", "") or "").strip()
            intro = cls._one_line(getattr(asset, intro_attr, "") or getattr(asset, "intro", ""))
            if name and intro:
                result[name] = intro
        return result

    def role_index_context(self, project_dir: Path, state: ProjectState, episode_key: str) -> str:
        payload = self._load_node_payload(project_dir, "role_extract")
        items = self._list_items_index(payload.get("roles"), episode_key)
        if not items:
            items = self._state_assets_index(state.roles, episode_key, intro_attr="intro")
        return self._format_asset_index(items)

    def prop_index_context(self, project_dir: Path, state: ProjectState, episode_key: str) -> str:
        items = self._state_assets_index(state.props, episode_key, intro_attr="desc")
        if items:
            return self._format_asset_index(items)
        payload = self._load_first_node_payload(project_dir, ("prop_dedupe", "prop_extract"))
        items = self._list_items_index(payload.get("props"), episode_key)
        if not items:
            items = self._mapping_index(payload.get("generated_prop_intro"))
        return self._format_asset_index(items)

    def layout_index_context(self, project_dir: Path, state: ProjectState, episode_key: str) -> str:
        items = self._state_assets_index(state.layouts, episode_key, intro_attr="desc")
        if items:
            return self._format_asset_index(items)
        payload = self._load_first_node_payload(project_dir, ("layout_dedupe_review", "layout_extract"))
        items = self._list_items_index(payload.get("layouts"), episode_key)
        if not items:
            items = self._mapping_index(payload.get("generated_layout_intro"))
        return self._format_asset_index(items)

    @staticmethod
    def _dedupe_texts(values: list[str]) -> list[str]:
        seen: set[str] = set()
        cleaned: list[str] = []
        for value in values:
            text = " ".join(str(value or "").split()).strip()
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned.append(text)
        return cleaned

    def validate_clip_segments(
        self,
        output: ClipSegmentOutput,
        state: ProjectState,
        *,
        episode_key: str,
    ) -> dict[str, object]:
        target_duration = self.script_service.episode_duration_seconds(state)
        suggested_max_clip_count = max(
            1,
            int((target_duration + self.MIN_CLIP_SECONDS - 1) // self.MIN_CLIP_SECONDS),
        )
        logger = getattr(self, "logger", None)
        clips = dict(output.root)
        if not clips:
            raise ValueError(f"clip_segment must return at least one clip for {episode_key}")
        ordered_keys = sorted(clips, key=lambda value: int(value) if str(value).isdigit() else 999999)
        expected_clip_keys = [str(index) for index in range(1, len(ordered_keys) + 1)]
        if ordered_keys != expected_clip_keys:
            raise ValueError(f"clip_segment clip keys for {episode_key} must be 1-{len(ordered_keys)}; got {ordered_keys}")
        if len(ordered_keys) > suggested_max_clip_count * 2 and logger is not None:
            logger.warning(
                "clip_segment returned many clips for %s: got %d for %ss reference duration at %d-%ds guidance",
                episode_key,
                len(ordered_keys),
                target_duration,
                self.MIN_CLIP_SECONDS,
                self.MAX_CLIP_SECONDS,
            )
        ordered_clips: dict[str, object] = {}
        for key in ordered_keys:
            clip = clips[key]
            clip.text = " ".join(str(clip.text or "").split()).strip()
            if not clip.text:
                raise ValueError(f"clip_segment text is empty for {episode_key} clip {key}")
            if len(clip.text) < self.SHORT_TEXT_WARNING_CHARS and logger is not None:
                logger.warning(
                    "clip_segment text is short for %s clip %s: %d chars",
                    episode_key,
                    key,
                    len(clip.text),
                )
            if len(clip.text) > self.LONG_TEXT_WARNING_CHARS and logger is not None:
                logger.warning(
                    "clip_segment text is long for %s clip %s: %d chars",
                    episode_key,
                    key,
                    len(clip.text),
                )
            clip.role_names = self._dedupe_texts(clip.role_names)
            clip.prop_names = self._dedupe_texts(clip.prop_names)
            clip.layout_names = self._dedupe_texts(clip.layout_names)
            ordered_clips[key] = clip
        return ordered_clips

    def merge_clip_segment_outputs(
        self,
        *,
        project_dir: Path,
        generated_output: ClipSegmentNodeOutput,
        target_episode_keys: list[str],
        all_episode_keys: list[str],
    ) -> ClipSegmentNodeOutput:
        existing_path = self.layout.node_output_path(project_dir, self.name)
        by_episode: dict[str, dict[str, object]] = {}
        if existing_path.exists():
            try:
                existing = ClipSegmentNodeOutput.model_validate_json(existing_path.read_text(encoding="utf-8"))
                by_episode.update(dict(existing.root))
            except Exception as exc:
                self.logger.warning("clip_segment ignored invalid existing output %s: %s", existing_path, exc)
        by_episode.update(dict(generated_output.root))
        ordered_keys = all_episode_keys if not set(target_episode_keys).difference(all_episode_keys) else target_episode_keys
        return ClipSegmentNodeOutput({episode_key: by_episode[episode_key] for episode_key in ordered_keys if episode_key in by_episode})

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script", node_name=self.name)
        self.logger.info(
            "node=clip_segment provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.target_episode_keys(state)
        all_episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        self.validate_episode_keys("script_novel_extract.novel_extract", state.script.novel_extract, state)
        novel_full = self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            episode_keys,
            label="script_novel.novel_full",
        )
        novel_extract = self.script_contents.load_contents(
            project_dir,
            state.script.novel_extract,
            all_episode_keys,
            label="script_novel_extract.novel_extract",
        )
        novel_extract_all_episodes = self.script_service.format_json(novel_extract)
        generated: dict[str, dict[str, object]] = {}
        for episode_key in episode_keys:
            output = await self.script_service.clip_segment(
                state,
                provider,
                episode_key=episode_key,
                novel_full_this_episode=novel_full.get(episode_key, ""),
                novel_extract_all_episodes=novel_extract_all_episodes,
                role_index=self.role_index_context(project_dir, state, episode_key),
                prop_index=self.prop_index_context(project_dir, state, episode_key),
                layout_index=self.layout_index_context(project_dir, state, episode_key),
            )
            generated[episode_key] = self.validate_clip_segments(output, state, episode_key=episode_key)
            state.budget.used_text_calls += 1
        output = ClipSegmentNodeOutput(generated)
        merged = self.merge_clip_segment_outputs(
            project_dir=project_dir,
            generated_output=output,
            target_episode_keys=episode_keys,
            all_episode_keys=all_episode_keys,
        )
        self.repo.save_node_output(project_dir, self.name, merged)
        state.metadata["clip_segments"] = merged.model_dump(mode="json")
        state.metadata["clip_segment_path"] = self.layout.project_relative(
            project_dir,
            self.layout.node_output_path(project_dir, self.name),
        )
        state.metadata["clip_segment_episode_keys"] = list(merged.root)
        return state


def build_script_node_runners(workflow: Any) -> dict[str, ScriptNodeBase]:
    script_contents = getattr(workflow, "script_contents", None)
    if script_contents is None:
        script_contents = ScriptContentRepository(workflow.repo, workflow.layout)

    def force_getter() -> bool:
        return bool(getattr(workflow, "_force_pregen", False))

    deps = {
        "workflow": workflow,
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "script_contents": script_contents,
        "logger": getattr(workflow, "logger", None) or get_logger(),
        "force_getter": force_getter,
    }
    return {
        ScriptImportNode.name: ScriptImportNode(**deps),
        ScriptDetailExpandNode.name: ScriptDetailExpandNode(**deps),
        ScriptOutlineNode.name: ScriptOutlineNode(**deps),
        ScriptNovelNode.name: ScriptNovelNode(**deps),
        ScriptNovelExtractNode.name: ScriptNovelExtractNode(**deps),
        ClipSegmentNode.name: ClipSegmentNode(**deps),
    }


def build_script_nodes(workflow: Any, after_novel_nodes: list[WorkflowNode] | None = None) -> list[WorkflowNode]:
    runners = build_script_node_runners(workflow)
    after_novel_nodes = after_novel_nodes or []
    nodes: list[WorkflowNode] = []
    for node_name in SCRIPT_NODE_NAMES:
        nodes.append(WorkflowNode(name=node_name, run=runners[node_name].run))
        if node_name == "script_detail_expand":
            nodes.extend(after_novel_nodes)
    return nodes


__all__ = [
    "MANUAL_SCRIPT_NODE_NAMES",
    "SCRIPT_COMPLETION_ALIASES",
    "SCRIPT_NODE_NAMES",
    "ClipSegmentNode",
    "ScriptDetailExpandNode",
    "ScriptImportNode",
    "ScriptNovelExtractNode",
    "ScriptNovelNode",
    "ScriptOutlineNode",
    "build_script_node_runners",
    "build_script_nodes",
]
