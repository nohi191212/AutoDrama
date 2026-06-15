from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autodrama.core.schemas import ProjectState
from autodrama.logging import get_logger, log_context, setup_logging
from autodrama.postgen.edit_plan_validator import estimate_timeline_duration, validate_edit_plan
from autodrama.postgen.edit_prompt import build_postgen_edit_plan_prompt
from autodrama.postgen.ffmpeg_composer import PostgenFfmpegComposer
from autodrama.postgen.schemas import (
    POSTGEN_EDIT_PLAN_SCHEMA_VERSION,
    PostgenCompositionOutput,
    PostgenEditPlan,
    PostgenEditPlanGenerationItem,
    PostgenEditPlanGenerationOutput,
    PostgenEditPlanValidationItem,
    PostgenEditPlanValidationOutput,
    PostgenOutputSpec,
    PostgenSourceClip,
    PostgenSourceCollectItem,
    PostgenSourceCollectOutput,
    PostgenTimelineItem,
    PostgenTransition,
)
from autodrama.postgen.source_collect import collect_episode_source_clips
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.context import WorkflowRunContext
from autodrama.workflows.delegation import PregenWorkflowDelegateMixin
from autodrama.workflows.selection import normalize_shot_selectors, select_episode_keys


POSTGEN_NODES = [
    "postgen_source_collect",
    "postgen_edit_plan_generation",
    "postgen_edit_plan_validation",
    "postgen_video_composition",
]


class PostgenWorkflow(PregenWorkflowDelegateMixin):
    def __init__(
        self,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> None:
        self._init_pregen_delegate(repo=repo, router=router, prompts=prompts)

    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "postgen_video_composition",
        force: bool = False,
        episode_keys: list[str] | None = None,
        only: str | None = None,
        shot_selectors: list[str] | None = None,
    ) -> ProjectState:
        if until not in POSTGEN_NODES:
            raise ValueError(f"Unsupported postgen stop node: {until}")
        if only is not None and only not in POSTGEN_NODES:
            raise ValueError(f"Unsupported postgen only node: {only}")

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        selected_episode_keys = select_episode_keys(state, episode_keys)
        target_nodes = [only] if only else POSTGEN_NODES[: POSTGEN_NODES.index(until) + 1]
        logger.info(
            "workflow=postgen project_id=%s until=%s only=%s force=%s episodes=%s shots=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys) or "-",
            ",".join(shot_selectors or []) or "-",
        )

        previous_run_context = getattr(self, "_run_context", None)
        self._run_context = WorkflowRunContext(
            workflow_name="postgen",
            project_dir=project_dir,
            until=until,
            only=only,
            force=force,
            selected_episode_keys=selected_episode_keys,
            shot_selectors=normalize_shot_selectors(shot_selectors),
            burn_subtitles=self.repo.settings.postgen.burn_subtitles,
        )
        try:
            for index, node_name in enumerate(target_nodes, start=1):
                if (
                    not force
                    and node_name in state.completed_nodes
                    and self._node_outputs_exist(project_dir, node_name, selected_episode_keys)
                ):
                    logger.info("node %d/%d %s skipped", index, len(target_nodes), node_name)
                    continue
                logger.info("node %d/%d %s started", index, len(target_nodes), node_name)
                try:
                    state = await getattr(self, f"_run_{node_name}")(project_dir, state, selected_episode_keys)
                    state.mark_completed(node_name)
                    self.repo.save_state(project_dir, state)
                except Exception:
                    logger.exception("node %d/%d %s failed", index, len(target_nodes), node_name)
                    raise
                logger.info(
                    "node %d/%d %s completed current_node=%s",
                    index,
                    len(target_nodes),
                    node_name,
                    state.current_node,
                )
        finally:
            if previous_run_context is None:
                if hasattr(self, "_run_context"):
                    delattr(self, "_run_context")
            else:
                self._run_context = previous_run_context

        get_logger().info(
            "workflow=postgen completed project_id=%s current_node=%s episodes=%s",
            state.project_id,
            state.current_node,
            ",".join(selected_episode_keys) or "-",
        )
        return state

    def _node_outputs_exist(self, project_dir: Path, node_name: str, episode_keys: list[str]) -> bool:
        if node_name == "postgen_source_collect":
            return all(self._source_clips_path(project_dir, episode_key).exists() for episode_key in episode_keys)
        if node_name == "postgen_edit_plan_generation":
            return all(self._raw_plan_path(project_dir, episode_key).exists() for episode_key in episode_keys)
        if node_name == "postgen_edit_plan_validation":
            return all(self._validated_plan_path(project_dir, episode_key).exists() for episode_key in episode_keys)
        if node_name == "postgen_video_composition":
            return all(self._final_output_path(project_dir, episode_key).exists() for episode_key in episode_keys)
        return False

    def _postgen_json_dir(self, project_dir: Path) -> Path:
        return project_dir / "assets" / "json" / "postgen"

    def _source_clips_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._postgen_json_dir(project_dir) / "source_clips" / f"{episode_key}.json"

    def _raw_plan_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._postgen_json_dir(project_dir) / "edit_plans" / f"{episode_key}.raw.json"

    def _validated_plan_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._postgen_json_dir(project_dir) / "edit_plans" / f"{episode_key}.validated.json"

    def _final_output_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "videos" / f"{episode_key}_postgen.mp4"

    def _tmp_dir(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / ".tmp" / "postgen" / episode_key

    def _project_relative(self, project_dir: Path, path: Path) -> str:
        return self.layout.project_relative(project_dir, path)

    async def _run_postgen_source_collect(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str],
    ) -> ProjectState:
        output_items: list[PostgenSourceCollectItem] = []
        context = getattr(self, "_run_context", None)
        shot_selectors = sorted(context.shot_selectors) if context is not None else []
        for episode_key in episode_keys:
            clips, warnings = collect_episode_source_clips(
                self.repo,
                project_dir,
                episode_key,
                shot_selectors=shot_selectors,
                max_clips=self.repo.settings.postgen.max_source_clips_per_plan,
            )
            path = self._source_clips_path(project_dir, episode_key)
            self.repo.write_json(path, {"episode_key": episode_key, "source_clips": [clip.model_dump(mode="json") for clip in clips], "warnings": warnings})
            output_items.append(
                PostgenSourceCollectItem(
                    episode_key=episode_key,
                    source_clip_count=len(clips),
                    path=self._project_relative(project_dir, path),
                    warnings=warnings,
                )
            )
        self.repo.save_node_output(project_dir, "postgen_source_collect", PostgenSourceCollectOutput(episodes=output_items))
        state.metadata["postgen_source_clips"] = [item.model_dump(mode="json") for item in output_items]
        return state

    async def _run_postgen_edit_plan_generation(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str],
    ) -> ProjectState:
        output_items: list[PostgenEditPlanGenerationItem] = []
        for episode_key in episode_keys:
            clips = self._load_source_clips(project_dir, episode_key)
            output_path = self._project_relative(project_dir, self._final_output_path(project_dir, episode_key))
            plan = await self._generate_edit_plan(project_dir, state, episode_key, clips, output_path)
            raw_path = self._raw_plan_path(project_dir, episode_key)
            self.repo.write_json(raw_path, plan)
            output_items.append(
                PostgenEditPlanGenerationItem(
                    episode_key=episode_key,
                    raw_plan_path=self._project_relative(project_dir, raw_path),
                    source_clip_count=len(clips),
                    timeline_count=len(plan.timeline),
                    provider=str(plan.metadata.get("provider") or "-"),
                    model=plan.metadata.get("model"),
                )
            )
        self.repo.save_node_output(
            project_dir,
            "postgen_edit_plan_generation",
            PostgenEditPlanGenerationOutput(generated_plans=output_items),
        )
        state.metadata["postgen_raw_edit_plans"] = [item.model_dump(mode="json") for item in output_items]
        return state

    async def _generate_edit_plan(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        clips: list[PostgenSourceClip],
        output_path: str,
    ) -> PostgenEditPlan:
        settings = self.repo.settings.postgen
        if settings.edit_plan_mode == "deterministic":
            return self._deterministic_plan(state, episode_key, clips, output_path, provider="local", model="deterministic")

        provider = self.router.text("postgen_edit_plan", node_name="postgen_edit_plan_generation")
        prompt = build_postgen_edit_plan_prompt(
            project_title=state.title,
            episode_key=episode_key,
            source_clips=clips,
            output_path=output_path,
            width=settings.render_width,
            height=settings.render_height,
            fps=settings.fps,
        )
        with log_context(node_name="postgen_edit_plan_generation", episode_key=episode_key):
            plan = await provider.generate_json(
                prompt,
                PostgenEditPlan,
                temperature=0.2,
                metadata={
                    "node_name": "postgen_edit_plan_generation",
                    "episode_key": episode_key,
                    "source_clip_count": len(clips),
                    "temperature": 0.2,
                },
            )
        plan.metadata.update(
            {
                "provider": getattr(provider, "name", "unknown"),
                "model": getattr(provider, "model", None),
                "project_id": state.project_id,
                "mode": "llm",
            }
        )
        return plan

    def _deterministic_plan(
        self,
        state: ProjectState,
        episode_key: str,
        clips: list[PostgenSourceClip],
        output_path: str,
        *,
        provider: str,
        model: str,
    ) -> PostgenEditPlan:
        timeline: list[PostgenTimelineItem] = []
        for index, clip in enumerate(clips, start=1):
            trim_head = min(0.15, max(0.0, clip.duration_seconds * 0.08))
            trim_tail = min(0.15, max(0.0, clip.duration_seconds * 0.08))
            source_in = round(trim_head, 3)
            source_out = round(max(source_in + 0.25, clip.duration_seconds - trim_tail), 3)
            timeline.append(
                PostgenTimelineItem(
                    clip_id=f"{episode_key}_cut_{index:03d}",
                    shot_id=clip.shot_id,
                    source_in=source_in,
                    source_out=source_out,
                    speed=1.0,
                    transition_after=PostgenTransition(type="cut"),
                    rationale="deterministic postgen fallback trims tiny head/tail handles",
                )
            )
        return PostgenEditPlan(
            schema_version=POSTGEN_EDIT_PLAN_SCHEMA_VERSION,
            episode_key=episode_key,
            source_clips=clips,
            timeline=timeline,
            output=PostgenOutputSpec(
                path=output_path,
                width=self.repo.settings.postgen.render_width,
                height=self.repo.settings.postgen.render_height,
                fps=self.repo.settings.postgen.fps,
                burn_subtitles=self.repo.settings.postgen.burn_subtitles,
                audio=False,
            ),
            metadata={
                "provider": provider,
                "model": model,
                "project_id": state.project_id,
                "mode": "deterministic",
            },
        )

    async def _run_postgen_edit_plan_validation(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str],
    ) -> ProjectState:
        output_items: list[PostgenEditPlanValidationItem] = []
        for episode_key in episode_keys:
            clips = self._load_source_clips(project_dir, episode_key)
            raw_path = self._raw_plan_path(project_dir, episode_key)
            if not raw_path.exists():
                raise FileNotFoundError(f"raw postgen edit plan is missing: {raw_path}")
            raw_plan = PostgenEditPlan.model_validate_json(raw_path.read_text(encoding="utf-8"))
            output_path = self._project_relative(project_dir, self._final_output_path(project_dir, episode_key))
            validated = validate_edit_plan(
                raw_plan,
                project_dir=project_dir,
                episode_key=episode_key,
                expected_source_clips=clips,
                output_path=output_path,
                width=self.repo.settings.postgen.render_width,
                height=self.repo.settings.postgen.render_height,
                fps=self.repo.settings.postgen.fps,
            )
            validated_path = self._validated_plan_path(project_dir, episode_key)
            self.repo.write_json(validated_path, validated)
            output_items.append(
                PostgenEditPlanValidationItem(
                    episode_key=episode_key,
                    raw_plan_path=self._project_relative(project_dir, raw_path),
                    validated_plan_path=self._project_relative(project_dir, validated_path),
                    timeline_count=len(validated.timeline),
                    estimated_duration_seconds=estimate_timeline_duration(validated),
                    warnings=list(validated.warnings),
                )
            )
        self.repo.save_node_output(
            project_dir,
            "postgen_edit_plan_validation",
            PostgenEditPlanValidationOutput(validated_plans=output_items),
        )
        state.metadata["postgen_validated_edit_plans"] = [item.model_dump(mode="json") for item in output_items]
        return state

    async def _run_postgen_video_composition(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str],
    ) -> ProjectState:
        composer = PostgenFfmpegComposer(ffmpeg_path=self.repo.settings.runtime.ffmpeg_path)
        composed = []
        skipped: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            validated_path = self._validated_plan_path(project_dir, episode_key)
            if not validated_path.exists():
                raise FileNotFoundError(f"validated postgen edit plan is missing: {validated_path}")
            plan = PostgenEditPlan.model_validate_json(validated_path.read_text(encoding="utf-8"))
            if not plan.timeline:
                skipped.append({"episode_key": episode_key, "reason": "empty timeline"})
                continue
            item = await composer.compose(
                project_dir,
                plan,
                tmp_dir=self._tmp_dir(project_dir, episode_key),
                validated_plan_path=validated_path,
            )
            composed.append(item)
        if skipped and not composed:
            raise ValueError("No postgen videos composed: " + "; ".join(item["reason"] for item in skipped))
        output = PostgenCompositionOutput(composed_videos=composed, skipped_episodes=skipped)
        self.repo.save_node_output(project_dir, "postgen_video_composition", output)
        state.metadata["postgen_final_videos"] = [item.model_dump(mode="json") for item in composed]
        return state

    def _load_source_clips(self, project_dir: Path, episode_key: str) -> list[PostgenSourceClip]:
        path = self._source_clips_path(project_dir, episode_key)
        if not path.exists():
            raise FileNotFoundError(f"postgen source clips missing: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        clips = payload.get("source_clips") if isinstance(payload, dict) else None
        if not isinstance(clips, list):
            raise ValueError(f"Invalid postgen source clips file: {path}")
        return [PostgenSourceClip.model_validate(item) for item in clips]


__all__ = ["POSTGEN_NODES", "PostgenWorkflow"]
