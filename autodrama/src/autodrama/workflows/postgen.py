from __future__ import annotations

import json
from pathlib import Path
import shutil
from typing import Any

from autodrama.core.schemas import ProjectState
from autodrama.logging import get_logger, log_context, setup_logging
from autodrama.postgen.audio_pipeline import (
    convert_and_rebuild_vocals,
    diarize_audio,
    extract_audio,
    mix_and_mux,
    separate_stems,
)
from autodrama.postgen.audit import audit_final_video, audit_source_clips, build_review_reel
from autodrama.postgen.edit_plan_validator import (
    estimate_timeline_duration,
    resolve_project_path,
    validate_edit_plan,
)
from autodrama.postgen.edit_prompt import build_postgen_edit_plan_prompt
from autodrama.postgen.ffmpeg_composer import PostgenFfmpegComposer, probe_video_dimensions
from autodrama.postgen.schemas import (
    POSTGEN_EDIT_PLAN_SCHEMA_VERSION,
    PostgenEditPlan,
    PostgenFinalAuditReport,
    PostgenOutputSpec,
    PostgenSourceAuditReport,
    PostgenSourceClip,
    PostgenSourceClipAudit,
    PostgenTimelineItem,
    PostgenTransition,
)
from autodrama.postgen.source_collect import collect_episode_source_clips
from autodrama.postgen.subtitle_pipeline import build_cues, burn_subtitles, transcribe, write_subtitle_files
from autodrama.providers.base import AssetRef
from autodrama.providers.router import ProviderRouter
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.context import WorkflowRunContext
from autodrama.workflows.delegation import PregenWorkflowDelegateMixin
from autodrama.workflows.output_scope import (
    configured_output_shot_ids,
    pending_expected_output_shot_ids,
    synchronize_expected_output_scope,
)
from autodrama.workflows.selection import normalize_shot_selectors, select_episode_keys


POSTGEN_NODES = [
    "postgen_source_collect",
    "postgen_source_asr",
    "postgen_source_audit",
    "postgen_edit_plan_generation",
    "postgen_edit_plan_validation",
    "postgen_video_composition",
    "postgen_audio_separation",
    "postgen_speaker_diarization",
    "postgen_voice_conversion",
    "postgen_audio_remix",
    "postgen_subtitle_asr",
    "postgen_subtitle_render",
    "postgen_final_audit",
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
        until: str = "postgen_final_audit",
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
        self.router.set_prompt_audit_project_dir(project_dir)
        state = self.repo.load_state(project_dir)
        scope_sync = synchronize_expected_output_scope(
            self.repo,
            project_dir,
            state,
            self._expected_episode_keys(state),
        )
        selected_episode_keys = select_episode_keys(state, episode_keys)
        target_nodes = [only] if only else POSTGEN_NODES[: POSTGEN_NODES.index(until) + 1]
        if only is None and not self.repo.settings.postgen.voice_alignment.enabled:
            target_nodes = [
                node
                for node in target_nodes
                if node
                not in {
                    "postgen_audio_separation",
                    "postgen_speaker_diarization",
                    "postgen_voice_conversion",
                }
            ]
        explicit_shot_selectors = normalize_shot_selectors(shot_selectors)
        pending_scope = pending_expected_output_shot_ids(
            state,
            selected_episode_keys,
        )
        if pending_scope and not explicit_shot_selectors:
            pending_count = sum(len(values) for values in pending_scope.values())
            raise RuntimeError(
                "expected_output_seconds now includes "
                f"{pending_count} shot(s) whose keyframe/manifest chain is pending; "
                "run pregen through shot_manifest_generation, then generation, before postgen"
            )
        effective_shot_selectors = set(explicit_shot_selectors)
        automatic_output_scope = False
        if (
            not explicit_shot_selectors
            and self.repo.settings.generation.expected_output_seconds > 0
        ):
            effective_shot_selectors, selections = configured_output_shot_ids(
                self.repo,
                project_dir,
                selected_episode_keys,
            )
            automatic_output_scope = True
            state.metadata["expected_output_selection_path"] = self.layout.project_relative(
                project_dir,
                self.layout.expected_output_selection_path(project_dir),
            )
            logger.info(
                "workflow=postgen automatic_output_scope expected_seconds=%d episodes=%s",
                self.repo.settings.generation.expected_output_seconds,
                ";".join(
                    f"{item.episode_key}:{item.selected_clip_count}/{item.total_clip_count}"
                    f" clips,{item.planned_output_seconds:.3f}s"
                    for item in selections
                ),
            )
        if scope_sync.config_changed:
            logger.info(
                "workflow=postgen expected_output_scope changed %s->%s added_shots=%d removed_shots=%d invalidated=%s",
                scope_sync.previous_expected_output_seconds,
                scope_sync.expected_output_seconds,
                sum(len(values) for values in scope_sync.added_shot_ids_by_episode.values()),
                sum(len(values) for values in scope_sync.removed_shot_ids_by_episode.values()),
                ",".join(scope_sync.invalidated_nodes) or "-",
            )
        logger.info(
            "workflow=postgen project_id=%s until=%s only=%s force=%s episodes=%s shots=%s scope=%s",
            state.project_id,
            until,
            only or "-",
            force,
            ",".join(selected_episode_keys) or "-",
            ",".join(sorted(effective_shot_selectors)) or "-",
            "expected_output_seconds" if automatic_output_scope else (
                "explicit" if explicit_shot_selectors else "all"
            ),
        )

        previous_context = getattr(self, "_run_context", None)
        self._run_context = WorkflowRunContext(
            workflow_name="postgen",
            project_dir=project_dir,
            until=until,
            only=only,
            force=force,
            selected_episode_keys=selected_episode_keys,
            shot_selectors=effective_shot_selectors,
            automatic_output_scope=automatic_output_scope,
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
                logger.info("node %d/%d %s completed", index, len(target_nodes), node_name)
        finally:
            if previous_context is None:
                delattr(self, "_run_context")
            else:
                self._run_context = previous_context

        get_logger().info("workflow=postgen completed project_id=%s", state.project_id)
        return state

    def _json_dir(self, project_dir: Path) -> Path:
        return project_dir / "assets" / "json" / "postgen"

    def _audio_dir(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "assets" / "audios" / "postgen" / episode_key

    def _subtitle_dir(self, project_dir: Path) -> Path:
        return project_dir / "assets" / "subtitles"

    def _tmp_dir(self, project_dir: Path, episode_key: str, stage: str = "") -> Path:
        path = project_dir / ".tmp" / "postgen" / episode_key
        return path / stage if stage else path

    def _source_clips_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "source_clips" / f"{episode_key}.json"

    def _source_audit_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "audits" / f"{episode_key}.source.json"

    def _source_transcript_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "transcripts" / f"{episode_key}.source.json"

    def _raw_plan_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "edit_plans" / f"{episode_key}.raw.json"

    def _validated_plan_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "edit_plans" / f"{episode_key}.validated.json"

    def _edited_video_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "videos" / f"{episode_key}_edited.mp4"

    def _aligned_video_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "videos" / f"{episode_key}_voice_aligned.mp4"

    def _final_video_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "videos" / f"{episode_key}_preview.mp4"

    def _deliverable_video_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "videos" / f"{episode_key}_deliverable.mp4"

    def _audio_manifest_path(self, project_dir: Path, episode_key: str, stage: str) -> Path:
        return self._json_dir(project_dir) / "audio" / stage / f"{episode_key}.json"

    def _transcript_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "transcripts" / f"{episode_key}.whisperx.json"

    def _subtitle_manifest_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "subtitles" / f"{episode_key}.json"

    def _final_audit_path(self, project_dir: Path, episode_key: str) -> Path:
        return self._json_dir(project_dir) / "audits" / f"{episode_key}.final.json"

    def _relative(self, project_dir: Path, path: Path) -> str:
        return self.layout.project_relative(project_dir, path)

    def _node_outputs_exist(self, project_dir: Path, node_name: str, episode_keys: list[str]) -> bool:
        path_for = {
            "postgen_source_collect": self._source_clips_path,
            "postgen_source_asr": self._source_transcript_path,
            "postgen_source_audit": self._source_audit_path,
            "postgen_edit_plan_generation": self._raw_plan_path,
            "postgen_edit_plan_validation": self._validated_plan_path,
            "postgen_video_composition": self._edited_video_path,
            "postgen_audio_separation": lambda root, key: self._audio_manifest_path(root, key, "separation"),
            "postgen_speaker_diarization": lambda root, key: self._audio_manifest_path(root, key, "diarization"),
            "postgen_voice_conversion": lambda root, key: self._audio_manifest_path(root, key, "conversion"),
            "postgen_audio_remix": self._aligned_video_path,
            "postgen_subtitle_asr": self._transcript_path,
            "postgen_subtitle_render": self._final_video_path,
            "postgen_final_audit": self._final_audit_path,
        }.get(node_name)
        return bool(path_for) and all(path_for(project_dir, key).exists() for key in episode_keys)

    async def _run_postgen_source_collect(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        outputs: list[dict[str, Any]] = []
        selectors = sorted(self._run_context.shot_selectors)
        # max_source_clips_per_plan is a per-audit/model batch size, not a
        # truncation rule for the deterministic source set.
        max_clips = None
        for episode_key in episode_keys:
            clips, warnings = collect_episode_source_clips(
                self.repo,
                project_dir,
                episode_key,
                shot_selectors=selectors,
                max_clips=max_clips,
            )
            path = self._source_clips_path(project_dir, episode_key)
            self.repo.write_json(path, {"episode_key": episode_key, "source_clips": [item.model_dump(mode="json") for item in clips], "warnings": warnings})
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, path), "source_clip_count": len(clips), "warnings": warnings})
        self.repo.save_node_output(project_dir, "postgen_source_collect", {"episodes": outputs})
        state.metadata["postgen_source_clips"] = outputs
        return state

    async def _run_postgen_source_asr(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_keys: list[str],
    ) -> ProjectState:
        audit_settings = self.repo.settings.postgen.audit
        asr_settings = self.repo.settings.postgen.subtitles
        enabled = bool(audit_settings.enabled and audit_settings.source_asr)
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            clips = self._load_source_clips(project_dir, episode_key)
            clip_results: list[dict[str, Any]] = []
            for clip in clips:
                if enabled:
                    source_path = Path(clip.source_path)
                    if not source_path.is_absolute():
                        source_path = project_dir / source_path
                    audio_path = self._audio_dir(project_dir, episode_key) / "source_asr" / f"{clip.shot_id}.wav"
                    await extract_audio(
                        source_path,
                        audio_path,
                        ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
                    )
                    transcript = await transcribe(audio_path, asr_settings)
                else:
                    transcript = {"language": asr_settings.language, "segments": []}
                clip_results.append(
                    {
                        "shot_id": clip.shot_id,
                        "source_path": clip.source_path,
                        "expected_dialogue_lines": clip.dialogue_lines,
                        "transcript": transcript,
                    }
                )
            path = self._source_transcript_path(project_dir, episode_key)
            manifest = {"episode_key": episode_key, "enabled": enabled, "clips": clip_results}
            self.repo.write_json(path, manifest)
            outputs.append(
                {
                    "episode_key": episode_key,
                    "path": self._relative(project_dir, path),
                    "enabled": enabled,
                    "clip_count": len(clip_results),
                }
            )
        self.repo.save_node_output(project_dir, "postgen_source_asr", {"episodes": outputs})
        state.metadata["postgen_source_transcripts"] = outputs
        return state

    async def _run_postgen_source_audit(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.audit
        provider = None
        if settings.enabled and settings.source_audit:
            provider = self.router.text("postgen_audit", node_name="postgen_source_audit")
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            clips = self._load_source_clips(project_dir, episode_key)
            transcript_path = self._source_transcript_path(project_dir, episode_key)
            source_transcripts = self._read_json(transcript_path) if transcript_path.exists() else {"clips": []}
            if provider is None:
                report = PostgenSourceAuditReport(
                    episode_key=episode_key,
                    clips=[PostgenSourceClipAudit(shot_id=item.shot_id, usable_end=item.duration_seconds) for item in clips],
                    overall_notes="source audit disabled",
                )
                mode = "disabled"
            else:
                batch_size = self.repo.settings.postgen.max_source_clips_per_plan
                batches: list[list[PostgenSourceClip]] = []
                start = 0
                while start < len(clips):
                    end = min(len(clips), start + batch_size)
                    batches.append(clips[start:end])
                    if end >= len(clips):
                        break
                    start = end - 1 if batch_size > 1 else end
                reports: list[PostgenSourceAuditReport] = []
                for batch_index, batch in enumerate(batches, start=1):
                    with log_context(node_name="postgen_source_audit", episode_key=episode_key):
                        batch_report = await audit_source_clips(
                            provider,
                            episode_key=episode_key,
                            clips=batch,
                            project_dir=project_dir,
                            work_dir=self._tmp_dir(
                                project_dir,
                                episode_key,
                                f"source_audit/batch_{batch_index:03d}",
                            ),
                            settings=settings,
                            ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
                            source_transcripts=source_transcripts,
                        )
                    self._validate_source_audit(batch_report, batch)
                    reports.append(batch_report)
                audit_by_shot = {
                    item.shot_id: item
                    for batch_report in reports
                    for item in batch_report.clips
                }
                report = PostgenSourceAuditReport(
                    episode_key=episode_key,
                    clips=[
                        audit_by_shot[clip.shot_id]
                        for clip in clips
                        if clip.shot_id in audit_by_shot
                    ],
                    continuity_notes=[
                        note
                        for batch_report in reports
                        for note in batch_report.continuity_notes
                    ],
                    overall_notes=" | ".join(
                        batch_report.overall_notes
                        for batch_report in reports
                        if batch_report.overall_notes
                    ),
                )
                self._validate_source_audit(report, clips)
                mode = "gemini_batched" if len(batches) > 1 else "gemini"
            path = self._source_audit_path(project_dir, episode_key)
            self.repo.write_json(path, report)
            for clip in report.clips:
                if clip.verdict != "reject":
                    continue
                self.repo.append_audit_rejection(
                    project_dir,
                    node_name="postgen_source_audit",
                    asset_id=clip.shot_id,
                    attempt=1,
                    issues=[
                        f"[{issue.category}/{issue.severity}] {issue.description}"
                        for issue in clip.issues
                    ],
                    rationale=clip.edit_guidance,
                    current_prompt="",
                )
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, path), "mode": mode})
        self.repo.save_node_output(project_dir, "postgen_source_audit", {"episodes": outputs})
        state.metadata["postgen_source_audits"] = outputs
        return state

    @staticmethod
    def _validate_source_audit(report: PostgenSourceAuditReport, clips: list[PostgenSourceClip]) -> None:
        if clips and report.episode_key != clips[0].episode_key:
            raise ValueError(
                f"source audit episode mismatch: expected {clips[0].episode_key}, got {report.episode_key}"
            )
        expected = {clip.shot_id: clip for clip in clips}
        actual = {item.shot_id: item for item in report.clips}
        if set(expected) != set(actual):
            raise ValueError(f"source audit shot ids mismatch: expected {sorted(expected)}, got {sorted(actual)}")
        for shot_id, item in actual.items():
            duration = expected[shot_id].duration_seconds
            end = duration if item.usable_end is None else item.usable_end
            if item.usable_start < 0 or end > duration + 0.05 or end <= item.usable_start:
                raise ValueError(f"source audit usable range is invalid for {shot_id}: {item.usable_start}-{end}, duration={duration}")

    async def _run_postgen_edit_plan_generation(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            clips = self._load_source_clips(project_dir, episode_key)
            audit = PostgenSourceAuditReport.model_validate_json(self._source_audit_path(project_dir, episode_key).read_text(encoding="utf-8"))
            output_path = self._relative(project_dir, self._edited_video_path(project_dir, episode_key))
            plan = await self._generate_edit_plan(project_dir, state, episode_key, clips, audit, output_path)
            path = self._raw_plan_path(project_dir, episode_key)
            self.repo.write_json(path, plan)
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, path), "timeline_count": len(plan.timeline), "mode": plan.metadata.get("mode")})
        self.repo.save_node_output(project_dir, "postgen_edit_plan_generation", {"episodes": outputs})
        state.metadata["postgen_raw_edit_plans"] = outputs
        return state

    async def _generate_edit_plan(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        clips: list[PostgenSourceClip],
        audit: PostgenSourceAuditReport,
        output_path: str,
    ) -> PostgenEditPlan:
        settings = self.repo.settings.postgen
        width, height = await self._inherited_source_dimensions(project_dir, clips)
        if settings.edit_plan_mode == "deterministic":
            return self._deterministic_plan(
                state,
                episode_key,
                clips,
                audit,
                output_path,
                width=width,
                height=height,
            )
        provider = self.router.text("postgen_edit_plan", node_name="postgen_edit_plan_generation")
        prompt = build_postgen_edit_plan_prompt(
            source_clips=clips,
            source_audit=audit,
            output_path=output_path,
            width=width,
            height=height,
            fps=settings.fps,
        )
        review_reel, review_ranges = await build_review_reel(
            clips,
            project_dir=project_dir,
            work_dir=self._tmp_dir(project_dir, episode_key, "edit_plan_review"),
            ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
        )
        prompt += (
            "\n\n你还会收到一条名为 episode_review_reel 的连续审片视频，它按 source_clips 顺序拼接，"
            "包含全部源视频和声音。必须直接观看视频后再决定 source_in/source_out；重点检查每段头尾的"
            "静止余量、动作是否完整、相邻段人物姿态与视线、构图、光线、运动方向和声音衔接。"
            "优先选择动作结果能接续下一段动作起点的切点，让整体观看更丝滑；不得只依据文字描述机械拼接。"
            "审片视频时间范围如下：\n"
            f"{json.dumps(review_ranges, ensure_ascii=False, indent=2)}"
        )
        with log_context(node_name="postgen_edit_plan_generation", episode_key=episode_key):
            plan = await provider.generate_json(
                prompt,
                PostgenEditPlan,
                temperature=0.2,
                refs=[AssetRef(id="episode_review_reel", type="video", path=str(review_reel))],
                metadata={"node_name": "postgen_edit_plan_generation", "episode_key": episode_key},
            )
        plan.metadata.update({"provider": getattr(provider, "name", "unknown"), "model": getattr(provider, "model", None), "mode": "llm"})
        return plan

    async def _inherited_source_dimensions(
        self,
        project_dir: Path,
        clips: list[PostgenSourceClip],
    ) -> tuple[int, int]:
        dimensions_by_shot: dict[str, tuple[int, int]] = {}
        for clip in clips:
            source_path = resolve_project_path(project_dir, clip.source_path)
            dimensions = await probe_video_dimensions(
                source_path,
                ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
            )
            if dimensions is None:
                raise ValueError(f"cannot read source video dimensions for {clip.shot_id}: {source_path}")
            dimensions_by_shot[clip.shot_id] = dimensions
        unique_dimensions = set(dimensions_by_shot.values())
        if len(unique_dimensions) != 1:
            details = ", ".join(
                f"{shot_id}={width}x{height}"
                for shot_id, (width, height) in dimensions_by_shot.items()
            )
            raise ValueError(f"postgen source videos must share one frame size: {details}")
        return next(iter(unique_dimensions))

    def _deterministic_plan(
        self,
        state: ProjectState,
        episode_key: str,
        clips: list[PostgenSourceClip],
        audit: PostgenSourceAuditReport,
        output_path: str,
        *,
        width: int,
        height: int,
    ) -> PostgenEditPlan:
        audit_by_shot = {item.shot_id: item for item in audit.clips}
        timeline: list[PostgenTimelineItem] = []
        for clip in clips:
            review = audit_by_shot[clip.shot_id]
            if review.verdict == "reject":
                continue
            handle = min(0.15, clip.duration_seconds * 0.08)
            source_in = max(handle, review.usable_start)
            source_out = min(clip.duration_seconds - handle, review.usable_end or clip.duration_seconds)
            if source_out - source_in < 0.25:
                continue
            timeline.append(
                PostgenTimelineItem(
                    clip_id=f"{episode_key}_cut_{len(timeline) + 1:03d}",
                    shot_id=clip.shot_id,
                    source_in=round(source_in, 3),
                    source_out=round(source_out, 3),
                    speed=1.0,
                    transition_after=PostgenTransition(type="cut"),
                    rationale=review.edit_guidance or "按源素材审计结果裁掉头尾生成余量",
                )
            )
        if not timeline:
            raise ValueError(f"all source clips were rejected or too short after audit for {episode_key}")
        return PostgenEditPlan(
            schema_version=POSTGEN_EDIT_PLAN_SCHEMA_VERSION,
            episode_key=episode_key,
            source_clips=clips,
            timeline=timeline,
            output=PostgenOutputSpec(
                path=output_path,
                width=width,
                height=height,
                fps=self.repo.settings.postgen.fps,
                burn_subtitles=self.repo.settings.postgen.burn_subtitles,
                audio=True,
            ),
            metadata={"provider": "local", "model": "audit-aware-deterministic", "mode": "deterministic"},
        )

    async def _run_postgen_edit_plan_validation(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            clips = self._load_source_clips(project_dir, episode_key)
            width, height = await self._inherited_source_dimensions(project_dir, clips)
            raw_path = self._raw_plan_path(project_dir, episode_key)
            raw = PostgenEditPlan.model_validate_json(raw_path.read_text(encoding="utf-8"))
            audit = PostgenSourceAuditReport.model_validate_json(
                self._source_audit_path(project_dir, episode_key).read_text(encoding="utf-8")
            )
            validated = validate_edit_plan(
                raw,
                project_dir=project_dir,
                episode_key=episode_key,
                expected_source_clips=clips,
                source_audit=audit,
                output_path=self._relative(project_dir, self._edited_video_path(project_dir, episode_key)),
                width=width,
                height=height,
                fps=self.repo.settings.postgen.fps,
            )
            path = self._validated_plan_path(project_dir, episode_key)
            self.repo.write_json(path, validated)
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, path), "duration_seconds": estimate_timeline_duration(validated)})
        self.repo.save_node_output(project_dir, "postgen_edit_plan_validation", {"episodes": outputs})
        state.metadata["postgen_validated_edit_plans"] = outputs
        return state

    async def _run_postgen_video_composition(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        composer = PostgenFfmpegComposer(ffmpeg_path=self.repo.settings.runtime.ffmpeg_path)
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            plan_path = self._validated_plan_path(project_dir, episode_key)
            plan = PostgenEditPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
            item = await composer.compose(project_dir, plan, tmp_dir=self._tmp_dir(project_dir, episode_key, "composition"), validated_plan_path=plan_path)
            outputs.append(item.model_dump(mode="json"))
        self.repo.save_node_output(project_dir, "postgen_video_composition", {"composed_videos": outputs})
        state.metadata["postgen_edited_videos"] = outputs
        return state

    async def _run_postgen_audio_separation(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.voice_alignment
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            manifest_path = self._audio_manifest_path(project_dir, episode_key, "separation")
            if not settings.enabled:
                manifest = {"episode_key": episode_key, "enabled": False, "reason": "voice alignment disabled"}
            else:
                audio_dir = self._audio_dir(project_dir, episode_key)
                raw = audio_dir / "audio_raw.wav"
                await extract_audio(self._edited_video_path(project_dir, episode_key), raw, ffmpeg_path=self.repo.settings.runtime.ffmpeg_path)
                vocals, background = await separate_stems(
                    raw,
                    output_dir=audio_dir / "demucs",
                    settings=settings,
                    runtime=self.repo.settings.runtime,
                )
                manifest = {
                    "episode_key": episode_key,
                    "enabled": True,
                    "audio_raw": self._relative(project_dir, raw),
                    "vocal_raw": self._relative(project_dir, vocals),
                    "bgm_sfx": self._relative(project_dir, background),
                }
            self.repo.write_json(manifest_path, manifest)
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, manifest_path), **manifest})
        self.repo.save_node_output(project_dir, "postgen_audio_separation", {"episodes": outputs})
        return state

    async def _run_postgen_speaker_diarization(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.voice_alignment
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            path = self._audio_manifest_path(project_dir, episode_key, "diarization")
            if not settings.enabled:
                manifest = {"episode_key": episode_key, "enabled": False, "segments": []}
            else:
                separation = self._read_json(self._audio_manifest_path(project_dir, episode_key, "separation"))
                vocal_path = project_dir / separation["vocal_raw"]
                segments = await diarize_audio(vocal_path, settings)
                manifest = {"episode_key": episode_key, "enabled": True, "segments": segments}
            self.repo.write_json(path, manifest)
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, path), "segment_count": len(manifest["segments"])})
        self.repo.save_node_output(project_dir, "postgen_speaker_diarization", {"episodes": outputs})
        return state

    async def _run_postgen_voice_conversion(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.voice_alignment
        config_dir = (self.repo.settings.config_path or project_dir).parent if self.repo.settings.config_path else project_dir
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            path = self._audio_manifest_path(project_dir, episode_key, "conversion")
            if not settings.enabled:
                manifest = {"episode_key": episode_key, "enabled": False, "segments": []}
            else:
                separation = self._read_json(self._audio_manifest_path(project_dir, episode_key, "separation"))
                diarization = self._read_json(self._audio_manifest_path(project_dir, episode_key, "diarization"))
                if not diarization["segments"]:
                    raise RuntimeError(f"PyAnnote returned no speaker segments for {episode_key}")
                episode_settings = settings.model_copy(deep=True)
                resolved_models: dict[str, Path] = {}
                speakers = {str(item["speaker"]) for item in diarization["segments"]}
                for speaker in speakers:
                    scoped_key = f"{episode_key}:{speaker}"
                    direct_model = settings.speaker_rvc_models.get(scoped_key) or settings.speaker_rvc_models.get(speaker)
                    role_id = settings.speaker_role_map.get(scoped_key) or settings.speaker_role_map.get(speaker)
                    role_model = settings.role_rvc_models.get(role_id) if role_id else None
                    model = role_model or direct_model
                    if model is not None:
                        resolved_models[speaker] = model
                episode_settings.speaker_rvc_models = resolved_models
                output_vocal = self._audio_dir(project_dir, episode_key) / "vocal_target_final.wav"
                items = await convert_and_rebuild_vocals(
                    project_dir / separation["vocal_raw"],
                    diarization["segments"],
                    output_path=output_vocal,
                    work_dir=self._tmp_dir(project_dir, episode_key, "rvc"),
                    settings=episode_settings,
                    config_dir=config_dir,
                    ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
                )
                manifest = {"episode_key": episode_key, "enabled": True, "vocal_target_final": self._relative(project_dir, output_vocal), "segments": items}
            self.repo.write_json(path, manifest)
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, path), "segment_count": len(manifest["segments"])})
        self.repo.save_node_output(project_dir, "postgen_voice_conversion", {"episodes": outputs})
        return state

    async def _run_postgen_audio_remix(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.voice_alignment
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            source = self._edited_video_path(project_dir, episode_key)
            output = self._aligned_video_path(project_dir, episode_key)
            output.parent.mkdir(parents=True, exist_ok=True)
            if not settings.enabled:
                shutil.copyfile(source, output)
            else:
                separation = self._read_json(self._audio_manifest_path(project_dir, episode_key, "separation"))
                conversion = self._read_json(self._audio_manifest_path(project_dir, episode_key, "conversion"))
                await mix_and_mux(
                    source,
                    project_dir / conversion["vocal_target_final"],
                    project_dir / separation["bgm_sfx"],
                    output,
                    settings=settings,
                    ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
                )
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, output), "voice_aligned": settings.enabled})
        self.repo.save_node_output(project_dir, "postgen_audio_remix", {"episodes": outputs})
        state.metadata["postgen_voice_aligned_videos"] = outputs
        return state

    async def _run_postgen_subtitle_asr(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.subtitles
        audit = self.repo.settings.postgen.audit
        enabled = bool(settings.enabled or (audit.enabled and audit.final_audit))
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            path = self._transcript_path(project_dir, episode_key)
            if not enabled:
                result: dict[str, Any] = {"enabled": False, "language": settings.language, "segments": []}
            else:
                audio_path = self._audio_dir(project_dir, episode_key) / "subtitle_audio.wav"
                await extract_audio(self._aligned_video_path(project_dir, episode_key), audio_path, ffmpeg_path=self.repo.settings.runtime.ffmpeg_path)
                result = await transcribe(audio_path, settings)
                result["enabled"] = True
                result["purpose"] = "subtitles_and_final_audit" if settings.enabled else "final_audit"
            self.repo.write_json(path, result)
            outputs.append({"episode_key": episode_key, "path": self._relative(project_dir, path), "segment_count": len(result.get("segments", []))})
        self.repo.save_node_output(project_dir, "postgen_subtitle_asr", {"episodes": outputs})
        return state

    async def _run_postgen_subtitle_render(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.subtitles
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            source = self._aligned_video_path(project_dir, episode_key)
            output = self._final_video_path(project_dir, episode_key)
            output.parent.mkdir(parents=True, exist_ok=True)
            srt_path = self._subtitle_dir(project_dir) / f"{episode_key}.srt"
            ass_path = self._subtitle_dir(project_dir) / f"{episode_key}.ass"
            cue_count = 0
            burned = False
            if settings.enabled:
                transcript = self._read_json(self._transcript_path(project_dir, episode_key))
                cues = build_cues(transcript, settings)
                cue_count = len(cues)
                validated_plan = PostgenEditPlan.model_validate_json(
                    self._validated_plan_path(project_dir, episode_key).read_text(encoding="utf-8")
                )
                write_subtitle_files(
                    cues,
                    srt_path=srt_path,
                    ass_path=ass_path,
                    width=validated_plan.output.width,
                    height=validated_plan.output.height,
                    settings=settings,
                )
                if self.repo.settings.postgen.burn_subtitles:
                    await burn_subtitles(source, ass_path, output, ffmpeg_path=self.repo.settings.runtime.ffmpeg_path)
                    burned = True
                else:
                    shutil.copyfile(source, output)
            else:
                shutil.copyfile(source, output)
            manifest = {
                "episode_key": episode_key,
                "enabled": settings.enabled,
                "cue_count": cue_count,
                "srt_path": self._relative(project_dir, srt_path) if srt_path.exists() else None,
                "ass_path": self._relative(project_dir, ass_path) if ass_path.exists() else None,
                "output_video_path": self._relative(project_dir, output),
                "subtitles_burned": burned,
                "quality_tier": "review_preview",
                "deliverable": False,
            }
            self.repo.write_json(self._subtitle_manifest_path(project_dir, episode_key), manifest)
            outputs.append(manifest)
        self.repo.save_node_output(project_dir, "postgen_subtitle_render", {"episodes": outputs})
        state.metadata["postgen_final_videos"] = outputs
        return state

    async def _run_postgen_final_audit(self, project_dir: Path, state: ProjectState, episode_keys: list[str]) -> ProjectState:
        settings = self.repo.settings.postgen.audit
        provider = None
        if settings.enabled and settings.final_audit:
            provider = self.router.text("postgen_audit", node_name="postgen_final_audit")
        outputs: list[dict[str, Any]] = []
        for episode_key in episode_keys:
            if provider is None:
                report = PostgenFinalAuditReport(episode_key=episode_key, verdict="warn", score=0, summary="final audit disabled")
                mode = "disabled"
            else:
                transcript = self._read_json(self._transcript_path(project_dir, episode_key))
                with log_context(node_name="postgen_final_audit", episode_key=episode_key):
                    report = await audit_final_video(
                        provider,
                        episode_key=episode_key,
                        video_path=self._final_video_path(project_dir, episode_key),
                        transcript=transcript,
                        work_dir=self._tmp_dir(project_dir, episode_key, "final_audit"),
                        settings=settings,
                        ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
                    )
                mode = "gemini"
            path = self._final_audit_path(project_dir, episode_key)
            self.repo.write_json(path, report)
            if report.verdict == "fail":
                self.repo.append_audit_rejection(
                    project_dir,
                    node_name="postgen_final_audit",
                    asset_id=episode_key,
                    attempt=1,
                    issues=[
                        f"[{issue.category}/{issue.severity}] {issue.description}"
                        for issue in report.issues
                    ],
                    rationale=report.summary,
                    current_prompt="",
                )
            deliverable_path = None
            if report.verdict == "pass":
                deliverable = self._deliverable_video_path(project_dir, episode_key)
                deliverable.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(self._final_video_path(project_dir, episode_key), deliverable)
                deliverable_path = self._relative(project_dir, deliverable)
            outputs.append(
                {
                    "episode_key": episode_key,
                    "path": self._relative(project_dir, path),
                    "mode": mode,
                    "verdict": report.verdict,
                    "score": report.score,
                    "deliverable": deliverable_path is not None,
                    "deliverable_video_path": deliverable_path,
                    "preview_video_path": self._relative(project_dir, self._final_video_path(project_dir, episode_key)),
                }
            )
            if settings.fail_on_reject and report.verdict == "fail":
                raise RuntimeError(f"final Gemini audit rejected {episode_key}; see {path}")
        self.repo.save_node_output(project_dir, "postgen_final_audit", {"episodes": outputs})
        state.metadata["postgen_final_audits"] = outputs
        return state

    def _load_source_clips(self, project_dir: Path, episode_key: str) -> list[PostgenSourceClip]:
        payload = self._read_json(self._source_clips_path(project_dir, episode_key))
        clips = [PostgenSourceClip.model_validate(item) for item in payload.get("source_clips", [])]
        if not clips:
            raise ValueError(f"no source clips found for {episode_key}")
        return clips

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(f"required postgen artifact is missing: {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"postgen artifact is not a JSON object: {path}")
        return payload


__all__ = ["POSTGEN_NODES", "PostgenWorkflow"]
