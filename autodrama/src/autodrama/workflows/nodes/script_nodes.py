from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from autodrama.core.schemas import ClipSegmentNodeOutput, ClipSegmentOutput, ProjectState, ScriptNovelExtractOutput, ScriptNovelOutput
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.services.director_service import DirectorService
from autodrama.services.script_service import ScriptService
from autodrama.workflows.runner import WorkflowNode

SCRIPT_NODE_NAMES = [
    "script_outline",
    "script_novel",
    "script_novel_extract",
    "clip_segment",
]

SCRIPT_NOVEL_EXTRACT_BATCH_SIZE = 5


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
        return self.script_service.episode_keys(self.script_service.episode_count(state))

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

    def validate_script_outline(self, output_episode_count: int, output_duration: int, state: ProjectState) -> None:
        expected_episode_count = self.script_service.episode_count(state)
        expected_duration = self.script_service.episode_duration_seconds(state)
        if output_episode_count != expected_episode_count:
            raise ValueError(
                f"script_outline episode_count must be {expected_episode_count}; got {output_episode_count}"
            )
        if output_duration != expected_duration:
            raise ValueError(
                f"script_outline target_duration_seconds must be {expected_duration}; got {output_duration}"
            )

    @staticmethod
    def format_previous_novel_chapters(novel_full: dict[str, str], episode_keys: list[str]) -> str:
        chunks: list[str] = []
        for episode_key in episode_keys:
            text = str(novel_full.get(episode_key) or "").strip()
            if text:
                chunks.append(f"## {episode_key}\n{text}")
        return "\n\n".join(chunks) if chunks else "（暂无，当前是第一章。）"


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
        self.validate_script_outline(output.episode_count, output.target_duration_seconds, state)
        self.validate_episode_keys("script_outline.episode_outlines", output.episode_outlines, state)
        state.script.outline = output.outline
        state.script.episode_outlines = {}
        for episode_key, content in output.episode_outlines.items():
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
        state.budget.used_text_calls += 1
        self.repo.save_node_output(
            project_dir,
            self.name,
            {
                "logline": output.logline,
                "outline": output.outline,
                "episode_count": output.episode_count,
                "target_duration_seconds": output.target_duration_seconds,
                "episode_outlines": state.script.episode_outlines,
            },
        )
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
            if output.episode_key != episode_key:
                raise ValueError(f"script_novel episode_key must be {episode_key}; got {output.episode_key}")
            if output.target_char_count != target_char_count:
                raise ValueError(
                    f"script_novel target_char_count must be {target_char_count}; got {output.target_char_count}"
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
        return state


class ScriptNovelExtractNode(ScriptNodeBase):
    name = "script_novel_extract"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("script", node_name=self.name)
        self.logger.info(
            "node=script_novel_extract provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode_keys = self.expected_episode_keys(state)
        self.validate_episode_keys("script_novel.novel_full", state.script.novel_full, state)
        novel_contents = self.script_contents.load_contents(
            project_dir,
            state.script.novel_full,
            episode_keys,
            label="script_novel.novel_full",
        )
        outline_contents = self.script_contents.load_contents(
            project_dir,
            state.script.episode_outlines,
            episode_keys,
            label="script_outline.episode_outlines",
            allow_missing=True,
        )
        force_pregen = self.force_getter()
        if force_pregen:
            state.script.novel_extract = {episode_key: False for episode_key in episode_keys}
        else:
            state.script.novel_extract = {
                episode_key: state.script.novel_extract.get(episode_key) or False
                for episode_key in episode_keys
            }
        state.metadata["script_novel_extract_batch_size"] = SCRIPT_NOVEL_EXTRACT_BATCH_SIZE

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
        for batch_start in range(0, len(episode_keys), SCRIPT_NOVEL_EXTRACT_BATCH_SIZE):
            batch_keys = episode_keys[batch_start : batch_start + SCRIPT_NOVEL_EXTRACT_BATCH_SIZE]
            if not force_pregen and all(extract_contents.get(episode_key) for episode_key in batch_keys):
                self.logger.info(
                    "script_novel_extract batch %s already exists, skipped",
                    ",".join(batch_keys),
                )
                continue

            previous_keys = episode_keys[:batch_start]
            previous_extract = {
                episode_key: extract_contents[episode_key]
                for episode_key in previous_keys
                if extract_contents.get(episode_key)
            }
            extract_hints = {
                episode_key: outline_contents.get(episode_key, "")
                for episode_key in batch_keys
            }
            output = await self.script_service.script_novel_extract_batch(
                state,
                provider,
                batch_episode_keys=batch_keys,
                novel_full=novel_contents,
                previous_extract=previous_extract,
                extract_hints=extract_hints,
                director_prep=DirectorService.director_prep_context(state, episode_keys=batch_keys),
            )
            actual_keys = set(output.novel_extract)
            expected_keys = set(batch_keys)
            if actual_keys != expected_keys:
                raise ValueError(
                    "script_novel_extract.novel_extract must contain exactly "
                    f"{', '.join(batch_keys)}; got {', '.join(sorted(actual_keys)) or '-'}"
                )
            for episode_key in batch_keys:
                extracted_text = str(output.novel_extract.get(episode_key) or "").strip()
                if not extracted_text:
                    raise ValueError(f"script_novel_extract novel_extract is empty for {episode_key}")
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
                extract_contents[episode_key] = extracted_text
                state.script.novel_extract[episode_key] = self.script_contents.project_relative(project_dir, episode_path)

            state.budget.used_text_calls += 1
            self.repo.save_state(project_dir, state)
            self.logger.info(
                "script_novel_extract batch %s generated saved_count=%d",
                ",".join(batch_keys),
                len(batch_keys),
            )

        self.validate_episode_keys("script_novel_extract.novel_extract", state.script.novel_extract, state)
        output = ScriptNovelExtractOutput(novel_extract=state.script.novel_extract)
        self.repo.save_node_output(project_dir, self.name, output)
        return state


class ClipSegmentNode(ScriptNodeBase):
    name = "clip_segment"
    MIN_CLIP_SECONDS = 12
    MAX_CLIP_SECONDS = 15

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
        min_clip_count = max(1, int((target_duration + self.MAX_CLIP_SECONDS - 1) // self.MAX_CLIP_SECONDS))
        max_clip_count = max(min_clip_count, int((target_duration + self.MIN_CLIP_SECONDS - 1) // self.MIN_CLIP_SECONDS))
        clips = dict(output.root)
        if not clips:
            raise ValueError(f"clip_segment must return at least one clip for {episode_key}")
        ordered_keys = sorted(clips, key=lambda value: int(value) if str(value).isdigit() else 999999)
        expected_clip_keys = [str(index) for index in range(1, len(ordered_keys) + 1)]
        if ordered_keys != expected_clip_keys:
            raise ValueError(f"clip_segment clip keys for {episode_key} must be 1-{len(ordered_keys)}; got {ordered_keys}")
        if not min_clip_count <= len(ordered_keys) <= max_clip_count:
            raise ValueError(
                f"clip_segment clip count for {episode_key} should be {min_clip_count}-{max_clip_count} "
                f"for {target_duration}s at 12-15s per clip; got {len(ordered_keys)}"
            )
        ordered_clips: dict[str, object] = {}
        for key in ordered_keys:
            clip = clips[key]
            clip.text = " ".join(str(clip.text or "").split()).strip()
            if not clip.text:
                raise ValueError(f"clip_segment text is empty for {episode_key} clip {key}")
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
            episode_keys,
            label="script_novel_extract.novel_extract",
        )
        generated: dict[str, dict[str, object]] = {}
        for episode_key in episode_keys:
            output = await self.script_service.clip_segment(
                state,
                provider,
                episode_key=episode_key,
                novel_full=novel_full.get(episode_key, ""),
                novel_extract=novel_extract.get(episode_key, ""),
                director_prep=DirectorService.director_prep_context(state, episode_keys=[episode_key]),
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
        if node_name == "script_novel":
            nodes.extend(after_novel_nodes)
    return nodes


__all__ = [
    "SCRIPT_NODE_NAMES",
    "SCRIPT_NOVEL_EXTRACT_BATCH_SIZE",
    "ClipSegmentNode",
    "ScriptNovelExtractNode",
    "ScriptNovelNode",
    "ScriptOutlineNode",
    "build_script_node_runners",
    "build_script_nodes",
]
