from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from autodrama.core.schemas import ProjectState, StoryboardEpisodeOutput, StoryboardShot
from autodrama.logging import get_logger, setup_logging
from autodrama.workflows.pregen import PregenWorkflow

EDITING_NODES = [
    "edit_plan_generation",
    "final_video_composition",
]


class EditMissingAsset(BaseModel):
    asset_type: Literal["shot_video", "ref_frame", "dialogue_audio", "bgm"]
    episode_key: str
    shot_id: str | None = None
    asset_id: str | None = None
    path: str | None = None
    required: bool = True
    reason: str


class EditClip(BaseModel):
    clip_id: str
    episode_key: str
    shot_id: str
    shot_index: int
    title: str
    source_type: Literal["video", "image"]
    source_path: str | None = None
    preferred_video_path: str | None = None
    fallback_frame_path: str | None = None
    start_time: float
    source_start_time: float = 0.0
    target_duration_seconds: float
    layout_id: str
    role_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    dialogue_lines: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class EditAudioLayer(BaseModel):
    layer_id: str
    layer_type: Literal["dialogue", "bgm"]
    source_path: str
    start_time: float
    duration_seconds: float | None = None
    volume: float = 1.0
    fade_in_seconds: float = 0.0
    fade_out_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class EditSubtitleCue(BaseModel):
    index: int
    start_time: float
    end_time: float
    text: str
    role_name: str | None = None
    shot_id: str | None = None


class EpisodeEditPlan(BaseModel):
    episode_key: str
    output_video_path: str
    subtitle_srt_path: str
    subtitle_ass_path: str
    width: int = 1280
    height: int = 720
    fps: int = 25
    aspect_ratio: str = "16:9"
    estimated_duration_seconds: float
    clips: list[EditClip] = Field(default_factory=list)
    audio_layers: list[EditAudioLayer] = Field(default_factory=list)
    subtitle_cues: list[EditSubtitleCue] = Field(default_factory=list)
    missing_assets: list[EditMissingAsset] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EditPlanGenerationOutput(BaseModel):
    generated_plans: list[EpisodeEditPlan]


class FinalVideoCompositionItem(BaseModel):
    episode_key: str
    output_video_path: str
    edit_plan_path: str
    subtitle_srt_path: str
    subtitle_ass_path: str
    estimated_duration_seconds: float
    clip_count: int
    audio_layer_count: int
    subtitle_count: int
    subtitles_burned: bool
    ffmpeg_path: str


class FinalVideoCompositionOutput(BaseModel):
    composed_videos: list[FinalVideoCompositionItem]
    skipped_episodes: list[dict[str, Any]] = Field(default_factory=list)


class EditingWorkflow(PregenWorkflow):
    async def run(
        self,
        project_dir: Path,
        *,
        until: str = "final_video_composition",
        force: bool = False,
        episode_keys: list[str] | None = None,
        burn_subtitles: bool = True,
    ) -> ProjectState:
        if until not in EDITING_NODES:
            raise ValueError(f"Unsupported editing stop node: {until}")

        logger = setup_logging(project_dir)
        state = self.repo.load_state(project_dir)
        self._apply_script_plan_settings(state)
        selected_episode_keys = self._select_episode_keys(state, episode_keys)
        stop_index = EDITING_NODES.index(until)
        target_nodes = EDITING_NODES[: stop_index + 1]
        logger.info(
            "workflow=editing project_id=%s until=%s force=%s episodes=%s",
            state.project_id,
            until,
            force,
            ",".join(selected_episode_keys) or "-",
        )

        previous_active_episode_keys = getattr(self, "_active_episode_keys", None)
        self._active_episode_keys = set(selected_episode_keys)
        previous_burn_subtitles = getattr(self, "_burn_subtitles", None)
        self._burn_subtitles = burn_subtitles
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
                    state = await getattr(self, f"_run_{node_name}")(project_dir, state)
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
            if previous_active_episode_keys is None:
                delattr(self, "_active_episode_keys")
            else:
                self._active_episode_keys = previous_active_episode_keys
            if previous_burn_subtitles is None:
                delattr(self, "_burn_subtitles")
            else:
                self._burn_subtitles = previous_burn_subtitles

        get_logger().info(
            "workflow=editing completed project_id=%s current_node=%s episodes=%s",
            state.project_id,
            state.current_node,
            ",".join(selected_episode_keys) or "-",
        )
        return state

    def _select_episode_keys(self, state: ProjectState, episode_keys: list[str] | None) -> list[str]:
        expected_episode_keys = self._expected_episode_keys(state)
        if not episode_keys:
            return expected_episode_keys

        requested = [key for key in episode_keys if key]
        unknown = sorted(set(requested).difference(expected_episode_keys))
        if unknown:
            raise ValueError(f"Unknown episode keys: {', '.join(unknown)}")
        requested_set = set(requested)
        return [key for key in expected_episode_keys if key in requested_set]

    def _node_outputs_exist(self, project_dir: Path, node_name: str, episode_keys: list[str]) -> bool:
        if node_name == "edit_plan_generation":
            return all(self._edit_plan_path(project_dir, episode_key).exists() for episode_key in episode_keys)
        if node_name == "final_video_composition":
            return all(self._episode_output_path(project_dir, episode_key).exists() for episode_key in episode_keys)
        return False

    @staticmethod
    def _edit_plan_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "edit_plans" / f"{episode_key}.json"

    @staticmethod
    def _episode_output_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "videos" / f"{episode_key}.mp4"

    @staticmethod
    def _subtitle_srt_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "subtitles" / f"{episode_key}.srt"

    @staticmethod
    def _subtitle_ass_path(project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "subtitles" / f"{episode_key}.ass"

    @staticmethod
    def _editing_tmp_dir(project_dir: Path, episode_key: str) -> Path:
        return project_dir / ".tmp" / "editing" / episode_key

    def _target_profile(self) -> tuple[int, int, int, str]:
        ratio = self._configured_video_ratio()
        resolution = self._configured_video_resolution()
        long_side = 720 if "720" in resolution else 1080 if "1080" in resolution else 720
        if ratio == "9:16":
            return long_side, int(long_side * 16 / 9), 25, ratio
        if ratio == "1:1":
            return long_side, long_side, 25, ratio
        return int(long_side * 16 / 9), long_side, 25, "16:9"

    def _configured_video_ratio(self) -> str:
        for provider_name in ("volcengine", "aliyun"):
            provider = self.repo.settings.providers.get(provider_name)
            if not provider:
                continue
            options = provider.options
            value = options.get("video_ratio") or options.get("ratio")
            if isinstance(value, str) and value.strip() in {"16:9", "9:16", "1:1"}:
                return value.strip()
        return "16:9"

    def _configured_video_resolution(self) -> str:
        for provider_name in ("volcengine", "aliyun"):
            provider = self.repo.settings.providers.get(provider_name)
            if not provider:
                continue
            value = provider.options.get("video_resolution") or provider.options.get("resolution")
            if isinstance(value, str) and value.strip():
                return value.strip().lower()
        return "720p"

    def _resolve_project_path(self, project_dir: Path, path: str | None) -> Path | None:
        if not path:
            return None
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return project_dir / candidate

    def _project_path_exists(self, project_dir: Path, path: str | None) -> bool:
        candidate = self._resolve_project_path(project_dir, path)
        return bool(candidate and candidate.exists())

    async def _run_edit_plan_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        get_logger().info("node=edit_plan_generation provider=local model=deterministic")
        plans: list[EpisodeEditPlan] = []
        for episode in self._iter_storyboard_episodes(project_dir, state):
            plan = self._build_episode_edit_plan(project_dir, state, episode)
            self.repo.write_json(self._edit_plan_path(project_dir, episode.episode_key), plan)
            plans.append(plan)

        state.metadata["edit_plans"] = [
            {
                "episode_key": plan.episode_key,
                "path": self._project_relative(project_dir, self._edit_plan_path(project_dir, plan.episode_key)),
                "output_video_path": plan.output_video_path,
                "estimated_duration_seconds": plan.estimated_duration_seconds,
                "missing_required_assets": sum(1 for item in plan.missing_assets if item.required),
            }
            for plan in plans
        ]
        self.repo.save_node_output(project_dir, "edit_plan_generation", EditPlanGenerationOutput(generated_plans=plans))
        return state

    def _build_episode_edit_plan(
        self,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
    ) -> EpisodeEditPlan:
        width, height, fps, aspect_ratio = self._target_profile()
        clips: list[EditClip] = []
        audio_layers: list[EditAudioLayer] = []
        subtitle_cues: list[EditSubtitleCue] = []
        missing_assets: list[EditMissingAsset] = []
        warnings: list[str] = []
        cursor = 0.0

        for shot in sorted(episode.shots, key=lambda item: item.index):
            duration = self._positive_duration(shot.duration_seconds)
            clip, clip_missing = self._build_clip(project_dir, episode.episode_key, shot, cursor, duration)
            clips.append(clip)
            missing_assets.extend(clip_missing)

            dialogue_layers, dialogue_missing = self._build_dialogue_layers(
                project_dir,
                episode.episode_key,
                shot,
                cursor,
                duration,
            )
            audio_layers.extend(dialogue_layers)
            missing_assets.extend(dialogue_missing)
            shot_bgm_layers, shot_bgm_missing = self._build_shot_bgm_layers(
                project_dir,
                episode.episode_key,
                shot,
                cursor,
                duration,
            )
            audio_layers.extend(shot_bgm_layers)
            missing_assets.extend(shot_bgm_missing)
            subtitle_cues.extend(self._build_subtitle_cues(shot, cursor, duration, len(subtitle_cues)))
            cursor += duration

        bgm_layer, bgm_missing, bgm_warnings = self._build_bgm_layer(project_dir, state, episode, cursor)
        if bgm_layer is not None:
            audio_layers.insert(0, bgm_layer)
        missing_assets.extend(bgm_missing)
        warnings.extend(bgm_warnings)

        return EpisodeEditPlan(
            episode_key=episode.episode_key,
            output_video_path=self._project_relative(project_dir, self._episode_output_path(project_dir, episode.episode_key)),
            subtitle_srt_path=self._project_relative(project_dir, self._subtitle_srt_path(project_dir, episode.episode_key)),
            subtitle_ass_path=self._project_relative(project_dir, self._subtitle_ass_path(project_dir, episode.episode_key)),
            width=width,
            height=height,
            fps=fps,
            aspect_ratio=aspect_ratio,
            estimated_duration_seconds=round(cursor, 3),
            clips=clips,
            audio_layers=audio_layers,
            subtitle_cues=subtitle_cues,
            missing_assets=missing_assets,
            warnings=warnings,
            metadata={
                "project_id": state.project_id,
                "title": state.title,
                "source_shot_path": f"shots/{episode.episode_key}.json",
            },
        )

    @staticmethod
    def _positive_duration(value: float | int | None) -> float:
        try:
            duration = float(value or 0)
        except (TypeError, ValueError):
            duration = 0.0
        return max(0.25, round(duration, 3))

    def _build_clip(
        self,
        project_dir: Path,
        episode_key: str,
        shot: StoryboardShot,
        start_time: float,
        duration: float,
    ) -> tuple[EditClip, list[EditMissingAsset]]:
        missing: list[EditMissingAsset] = []
        notes: list[str] = []
        source_type: Literal["video", "image"] = "video"
        source_path = shot.video_asset_path

        if not self._project_path_exists(project_dir, source_path):
            if source_path:
                missing.append(
                    EditMissingAsset(
                        asset_type="shot_video",
                        episode_key=episode_key,
                        shot_id=shot.shot_id,
                        asset_id=shot.video_asset_id,
                        path=source_path,
                        required=False,
                        reason="Shot video path is recorded but the file is missing; fallback frame will be used if available.",
                    )
                )
            elif shot.video_asset_id:
                missing.append(
                    EditMissingAsset(
                        asset_type="shot_video",
                        episode_key=episode_key,
                        shot_id=shot.shot_id,
                        asset_id=shot.video_asset_id,
                        required=False,
                        reason="Shot video asset has no path; fallback frame will be used if available.",
                    )
                )

            if self._project_path_exists(project_dir, shot.ref_frame_asset_path):
                source_type = "image"
                source_path = shot.ref_frame_asset_path
                notes.append("Using ref frame as a still-image fallback because shot video is unavailable.")
            else:
                source_path = shot.video_asset_path or shot.ref_frame_asset_path
                missing.append(
                    EditMissingAsset(
                        asset_type="ref_frame",
                        episode_key=episode_key,
                        shot_id=shot.shot_id,
                        asset_id=shot.ref_frame_asset_id,
                        path=shot.ref_frame_asset_path,
                        required=True,
                        reason="No usable shot video or fallback ref frame exists for this clip.",
                    )
                )

        return (
            EditClip(
                clip_id=f"{shot.shot_id}_clip",
                episode_key=episode_key,
                shot_id=shot.shot_id,
                shot_index=shot.index,
                title=shot.title,
                source_type=source_type,
                source_path=source_path,
                preferred_video_path=shot.video_asset_path,
                fallback_frame_path=shot.ref_frame_asset_path,
                start_time=start_time,
                target_duration_seconds=duration,
                layout_id=shot.layout_id,
                role_ids=shot.role_ids,
                prop_ids=shot.prop_ids,
                dialogue_lines=shot.dialogue,
                notes=notes,
            ),
            missing,
        )

    def _build_dialogue_layers(
        self,
        project_dir: Path,
        episode_key: str,
        shot: StoryboardShot,
        shot_start: float,
        shot_duration: float,
    ) -> tuple[list[EditAudioLayer], list[EditMissingAsset]]:
        layers: list[EditAudioLayer] = []
        missing: list[EditMissingAsset] = []
        assets = list(shot.dialogue_audio_assets)
        if not assets:
            if shot.dialogue:
                missing.append(
                    EditMissingAsset(
                        asset_type="dialogue_audio",
                        episode_key=episode_key,
                        shot_id=shot.shot_id,
                        required=False,
                        reason="Shot has dialogue text but no generated dialogue audio assets.",
                    )
                )
            return layers, missing

        line_duration = shot_duration / max(len(assets), 1)
        for index, asset in enumerate(assets):
            if not self._project_path_exists(project_dir, asset.asset_path):
                missing.append(
                    EditMissingAsset(
                        asset_type="dialogue_audio",
                        episode_key=episode_key,
                        shot_id=shot.shot_id,
                        asset_id=asset.asset_id,
                        path=asset.asset_path,
                        required=False,
                        reason="Dialogue audio file is missing; subtitles will still be emitted.",
                    )
                )
                continue
            layers.append(
                EditAudioLayer(
                    layer_id=f"{shot.shot_id}_dialogue_{asset.line_index:02d}",
                    layer_type="dialogue",
                    source_path=asset.asset_path or "",
                    start_time=round(shot_start + line_duration * index + min(0.25, line_duration * 0.2), 3),
                    volume=1.0,
                    metadata={
                        "shot_id": shot.shot_id,
                        "line_index": asset.line_index,
                        "role_id": asset.role_id,
                        "role_name": asset.role_name,
                        "text": asset.text,
                    },
                )
            )
        return layers, missing

    def _build_shot_bgm_layers(
        self,
        project_dir: Path,
        episode_key: str,
        shot: StoryboardShot,
        shot_start: float,
        shot_duration: float,
    ) -> tuple[list[EditAudioLayer], list[EditMissingAsset]]:
        layers: list[EditAudioLayer] = []
        missing: list[EditMissingAsset] = []
        for index, asset in enumerate(shot.shot_bgm_assets, start=1):
            if not self._project_path_exists(project_dir, asset.asset_path):
                missing.append(
                    EditMissingAsset(
                        asset_type="bgm",
                        episode_key=episode_key,
                        shot_id=shot.shot_id,
                        asset_id=asset.asset_id,
                        path=asset.asset_path,
                        required=False,
                        reason="Shot BGM file is missing; final video will be composed without this shot BGM layer.",
                    )
                )
                continue
            layers.append(
                EditAudioLayer(
                    layer_id=f"{shot.shot_id}_bgm_{index:02d}",
                    layer_type="bgm",
                    source_path=asset.asset_path or "",
                    start_time=round(shot_start, 3),
                    duration_seconds=round(shot_duration, 3),
                    volume=0.75,
                    fade_in_seconds=min(0.25, shot_duration / 6),
                    fade_out_seconds=min(0.35, shot_duration / 5),
                    metadata={
                        "shot_id": shot.shot_id,
                        "asset_id": asset.asset_id,
                        "source_node": "shot_bgm_generation",
                    },
                )
            )
        return layers, missing

    def _build_subtitle_cues(
        self,
        shot: StoryboardShot,
        shot_start: float,
        shot_duration: float,
        existing_count: int,
    ) -> list[EditSubtitleCue]:
        if not shot.dialogue:
            return []

        cues: list[EditSubtitleCue] = []
        line_duration = shot_duration / max(len(shot.dialogue), 1)
        for index, raw_line in enumerate(shot.dialogue):
            start = shot_start + line_duration * index + min(0.2, line_duration * 0.2)
            end = min(shot_start + shot_duration, start + max(1.0, line_duration * 0.75))
            role_name, text = self._parse_dialogue_line(raw_line)
            cues.append(
                EditSubtitleCue(
                    index=existing_count + index + 1,
                    start_time=round(start, 3),
                    end_time=round(max(start + 0.25, end), 3),
                    text=text,
                    role_name=role_name,
                    shot_id=shot.shot_id,
                )
            )
        return cues

    @staticmethod
    def _parse_dialogue_line(raw_line: str) -> tuple[str | None, str]:
        line = str(raw_line).strip()
        if not line:
            return None, ""
        for separator in ("::", ":", "："):
            if separator in line:
                role_name, text = line.split(separator, 1)
                role_name = role_name.strip()
                text = text.strip()
                if role_name and text:
                    return role_name, text
        return None, line

    def _build_bgm_layer(
        self,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        total_duration: float,
    ) -> tuple[EditAudioLayer | None, list[EditMissingAsset], list[str]]:
        missing: list[EditMissingAsset] = []
        warnings: list[str] = []
        if total_duration <= 0:
            return None, missing, warnings

        bgm_id = self._select_bgm_id(state, episode)
        if not bgm_id:
            warnings.append("No BGM asset is available in state.")
            return None, missing, warnings

        bgm = state.bgms.get(bgm_id)
        if not bgm:
            missing.append(
                EditMissingAsset(
                    asset_type="bgm",
                    episode_key=episode.episode_key,
                    asset_id=bgm_id,
                    required=False,
                    reason="Selected BGM id is not present in state.bgms.",
                )
            )
            return None, missing, warnings

        if not self._project_path_exists(project_dir, bgm.asset_path):
            missing.append(
                EditMissingAsset(
                    asset_type="bgm",
                    episode_key=episode.episode_key,
                    asset_id=bgm.id,
                    path=bgm.asset_path,
                    required=False,
                    reason="BGM file is missing; final video will be composed without music.",
                )
            )
            return None, missing, warnings

        return (
            EditAudioLayer(
                layer_id=f"{episode.episode_key}_bgm",
                layer_type="bgm",
                source_path=bgm.asset_path or "",
                start_time=0.0,
                duration_seconds=round(total_duration, 3),
                volume=0.22,
                fade_in_seconds=min(1.5, total_duration / 4),
                fade_out_seconds=min(1.5, total_duration / 4),
                metadata={"bgm_id": bgm.id, "name": bgm.name, "mood": bgm.mood},
            ),
            missing,
            warnings,
        )

    @staticmethod
    def _select_bgm_id(state: ProjectState, episode: StoryboardEpisodeOutput) -> str | None:
        del episode
        return next(iter(state.bgms), None)

    async def _run_final_video_composition(self, project_dir: Path, state: ProjectState) -> ProjectState:
        get_logger().info("node=final_video_composition provider=local model=ffmpeg")
        composed: list[FinalVideoCompositionItem] = []
        skipped: list[dict[str, Any]] = []
        burn_subtitles = bool(getattr(self, "_burn_subtitles", True))

        for episode_key in self._active_episode_keys_in_order(state):
            plan = self._load_or_create_edit_plan(project_dir, state, episode_key)
            required_missing = [item for item in plan.missing_assets if item.required]
            if required_missing:
                reasons = "; ".join(f"{item.shot_id or '-'}:{item.reason}" for item in required_missing)
                skipped.append({"episode_key": episode_key, "reason": reasons})
                continue

            item = await self._compose_episode(project_dir, plan, burn_subtitles=burn_subtitles)
            composed.append(item)

        if skipped and not composed:
            raise ValueError("No episodes could be composed: " + "; ".join(item["reason"] for item in skipped))

        output = FinalVideoCompositionOutput(composed_videos=composed, skipped_episodes=skipped)
        state.metadata["final_videos"] = [item.model_dump(mode="json") for item in composed]
        self.repo.save_node_output(project_dir, "final_video_composition", output)
        return state

    def _active_episode_keys_in_order(self, state: ProjectState) -> list[str]:
        active_episode_keys = getattr(self, "_active_episode_keys", None)
        expected = self._expected_episode_keys(state)
        if active_episode_keys is None:
            return expected
        return [episode_key for episode_key in expected if episode_key in active_episode_keys]

    def _load_or_create_edit_plan(self, project_dir: Path, state: ProjectState, episode_key: str) -> EpisodeEditPlan:
        path = self._edit_plan_path(project_dir, episode_key)
        if path.exists():
            return EpisodeEditPlan.model_validate_json(path.read_text(encoding="utf-8"))
        episode = self._load_storyboard_episode(project_dir, episode_key)
        plan = self._build_episode_edit_plan(project_dir, state, episode)
        self.repo.write_json(path, plan)
        return plan

    async def _compose_episode(
        self,
        project_dir: Path,
        plan: EpisodeEditPlan,
        *,
        burn_subtitles: bool,
    ) -> FinalVideoCompositionItem:
        tmp_dir = self._editing_tmp_dir(project_dir, plan.episode_key)
        tmp_dir.mkdir(parents=True, exist_ok=True)
        normalized_paths: list[Path] = []
        for clip in sorted(plan.clips, key=lambda item: item.shot_index):
            source_path = self._resolve_project_path(project_dir, clip.source_path)
            if source_path is None or not source_path.exists():
                raise FileNotFoundError(f"Clip source not found for {clip.shot_id}: {clip.source_path}")
            normalized_path = tmp_dir / f"{clip.shot_index:03d}_{self._safe_filename(clip.shot_id)}.mp4"
            await self._normalize_clip(project_dir, plan, clip, source_path, normalized_path)
            normalized_paths.append(normalized_path)

        concat_path = tmp_dir / "concat.txt"
        base_video_path = tmp_dir / "video_concat.mp4"
        self._write_concat_file(concat_path, normalized_paths)
        await self._concat_videos(concat_path, base_video_path)

        srt_path = self._resolve_required_project_path(project_dir, plan.subtitle_srt_path)
        ass_path = self._resolve_required_project_path(project_dir, plan.subtitle_ass_path)
        self._write_srt(srt_path, plan.subtitle_cues)
        self._write_ass(ass_path, plan)

        output_path = self._resolve_required_project_path(project_dir, plan.output_video_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subtitles_burned = await self._mux_audio_and_subtitles(
            project_dir,
            plan,
            base_video_path,
            ass_path,
            output_path,
            burn_subtitles=burn_subtitles,
        )

        return FinalVideoCompositionItem(
            episode_key=plan.episode_key,
            output_video_path=self._project_relative(project_dir, output_path),
            edit_plan_path=self._project_relative(project_dir, self._edit_plan_path(project_dir, plan.episode_key)),
            subtitle_srt_path=self._project_relative(project_dir, srt_path),
            subtitle_ass_path=self._project_relative(project_dir, ass_path),
            estimated_duration_seconds=plan.estimated_duration_seconds,
            clip_count=len(plan.clips),
            audio_layer_count=len(plan.audio_layers),
            subtitle_count=len(plan.subtitle_cues),
            subtitles_burned=subtitles_burned,
            ffmpeg_path=self.repo.settings.runtime.ffmpeg_path,
        )

    def _resolve_required_project_path(self, project_dir: Path, path: str) -> Path:
        resolved = self._resolve_project_path(project_dir, path)
        if resolved is None:
            raise ValueError("Expected a project-relative or absolute path, got empty value")
        return resolved

    async def _normalize_clip(
        self,
        project_dir: Path,
        plan: EpisodeEditPlan,
        clip: EditClip,
        source_path: Path,
        output_path: Path,
    ) -> None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        vf = (
            f"scale={plan.width}:{plan.height}:force_original_aspect_ratio=increase,"
            f"crop={plan.width}:{plan.height},setsar=1,"
            f"fps={plan.fps},format=yuv420p"
        )
        duration = self._ffmpeg_seconds(clip.target_duration_seconds)
        command = self._ffmpeg_base_command()
        if clip.source_type == "image":
            command.extend(["-loop", "1", "-t", duration, "-i", str(source_path)])
        else:
            vf = f"{vf},trim=duration={duration},setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={duration}"
            command.extend(["-i", str(source_path)])
        command.extend(
            [
                "-t",
                duration,
                "-vf",
                vf,
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        await self._run_ffmpeg(command, cwd=project_dir)

    @staticmethod
    def _ffmpeg_seconds(value: float | int) -> str:
        return f"{max(0.001, float(value)):.3f}"

    @staticmethod
    def _safe_filename(value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
        return safe.strip("._") or "clip"

    @staticmethod
    def _write_concat_file(path: Path, video_paths: list[Path]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for video_path in video_paths:
            normalized = str(video_path.resolve()).replace("\\", "/").replace("'", "'\\''")
            lines.append(f"file '{normalized}'")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    async def _concat_videos(self, concat_path: Path, output_path: Path) -> None:
        command = [
            *self._ffmpeg_base_command(),
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c",
            "copy",
            str(output_path),
        ]
        await self._run_ffmpeg(command, cwd=concat_path.parent)

    async def _mux_audio_and_subtitles(
        self,
        project_dir: Path,
        plan: EpisodeEditPlan,
        base_video_path: Path,
        ass_path: Path,
        output_path: Path,
        *,
        burn_subtitles: bool,
    ) -> bool:
        try:
            await self._mux_audio_and_subtitles_once(
                project_dir,
                plan,
                base_video_path,
                ass_path,
                output_path,
                burn_subtitles=burn_subtitles,
            )
            return burn_subtitles and bool(plan.subtitle_cues)
        except RuntimeError as exc:
            if not burn_subtitles or not plan.subtitle_cues:
                raise
            get_logger().warning(
                "Burning subtitles failed for %s; retrying mux without burned subtitles: %s",
                plan.episode_key,
                exc,
            )
            await self._mux_audio_and_subtitles_once(
                project_dir,
                plan,
                base_video_path,
                ass_path,
                output_path,
                burn_subtitles=False,
            )
            return False

    async def _mux_audio_and_subtitles_once(
        self,
        project_dir: Path,
        plan: EpisodeEditPlan,
        base_video_path: Path,
        ass_path: Path,
        output_path: Path,
        *,
        burn_subtitles: bool,
    ) -> None:
        command = [*self._ffmpeg_base_command(), "-i", str(base_video_path)]
        audio_layers = self._existing_audio_layers(project_dir, plan.audio_layers)
        for layer in audio_layers:
            if layer.layer_type == "bgm":
                command.extend(["-stream_loop", "-1"])
            command.extend(["-i", str(self._resolve_required_project_path(project_dir, layer.source_path))])

        filter_parts: list[str] = []
        video_map = "0:v:0"
        if burn_subtitles and plan.subtitle_cues:
            filter_parts.append(f"[0:v]subtitles=filename={self._filter_path(ass_path)}[vout]")
            video_map = "[vout]"
        filter_parts.append(self._audio_filter(plan, audio_layers))

        command.extend(
            [
                "-filter_complex",
                ";".join(filter_parts),
                "-map",
                video_map,
                "-map",
                "[aout]",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
        )
        await self._run_ffmpeg(command, cwd=project_dir)

    def _existing_audio_layers(self, project_dir: Path, layers: list[EditAudioLayer]) -> list[EditAudioLayer]:
        return [
            layer
            for layer in layers
            if self._project_path_exists(project_dir, layer.source_path)
        ]

    def _audio_filter(self, plan: EpisodeEditPlan, layers: list[EditAudioLayer]) -> str:
        if not layers:
            duration = self._ffmpeg_seconds(plan.estimated_duration_seconds)
            return f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=duration={duration}[aout]"

        chains: list[str] = []
        labels: list[str] = []
        for index, layer in enumerate(layers, start=1):
            output_label = f"a{index}"
            labels.append(f"[{output_label}]")
            filters = ["aresample=44100", "aformat=channel_layouts=stereo", "asetpts=PTS-STARTPTS"]
            if layer.duration_seconds is not None:
                filters.insert(0, f"atrim=duration={self._ffmpeg_seconds(layer.duration_seconds)}")
            filters.append(f"volume={max(0.0, min(float(layer.volume), 4.0)):.3f}")
            if layer.fade_in_seconds > 0:
                filters.append(f"afade=t=in:st=0:d={self._ffmpeg_seconds(layer.fade_in_seconds)}")
            if layer.fade_out_seconds > 0 and layer.duration_seconds:
                start = max(0.0, layer.duration_seconds - layer.fade_out_seconds)
                filters.append(f"afade=t=out:st={self._ffmpeg_seconds(start)}:d={self._ffmpeg_seconds(layer.fade_out_seconds)}")
            delay_ms = max(0, int(round(layer.start_time * 1000)))
            if delay_ms:
                filters.append(f"adelay={delay_ms}:all=1")
            chains.append(f"[{index}:a]{','.join(filters)}[{output_label}]")

        chains.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0[aout]")
        return ";".join(chains)

    @staticmethod
    def _filter_path(path: Path) -> str:
        value = str(path.resolve()).replace("\\", "/")
        value = value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        return f"'{value}'"

    def _ffmpeg_base_command(self) -> list[str]:
        return [
            self.repo.settings.runtime.ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
        ]

    @staticmethod
    def _srt_time(value: float) -> str:
        milliseconds = int(round(max(0.0, value) * 1000))
        hours, remainder = divmod(milliseconds, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, millis = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    @staticmethod
    def _ass_time(value: float) -> str:
        centiseconds = int(round(max(0.0, value) * 100))
        hours, remainder = divmod(centiseconds, 360_000)
        minutes, remainder = divmod(remainder, 6_000)
        seconds, cents = divmod(remainder, 100)
        return f"{hours:d}:{minutes:02d}:{seconds:02d}.{cents:02d}"

    @staticmethod
    def _subtitle_text(cue: EditSubtitleCue) -> str:
        text = cue.text.strip()
        if cue.role_name:
            return f"{cue.role_name}: {text}"
        return text

    def _write_srt(self, path: Path, cues: list[EditSubtitleCue]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        blocks: list[str] = []
        for index, cue in enumerate(cues, start=1):
            blocks.append(
                "\n".join(
                    [
                        str(index),
                        f"{self._srt_time(cue.start_time)} --> {self._srt_time(cue.end_time)}",
                        self._subtitle_text(cue),
                    ]
                )
            )
        path.write_text("\n\n".join(blocks) + ("\n" if blocks else ""), encoding="utf-8")

    def _write_ass(self, path: Path, plan: EpisodeEditPlan) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        font_size = max(24, int(plan.height * 0.055))
        margin_v = max(28, int(plan.height * 0.075))
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {plan.width}",
            f"PlayResY: {plan.height}",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
            "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,Microsoft YaHei,{font_size},&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
            f"0,0,0,0,100,100,0,0,1,2,1,2,60,60,{margin_v},1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]
        for cue in plan.subtitle_cues:
            text = self._ass_escape(self._subtitle_text(cue))
            lines.append(
                "Dialogue: "
                f"0,{self._ass_time(cue.start_time)},{self._ass_time(cue.end_time)},"
                f"Default,,0,0,0,,{text}"
            )
        path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    @staticmethod
    def _ass_escape(value: str) -> str:
        return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}").replace("\n", r"\N")

    async def _run_ffmpeg(self, command: list[str], *, cwd: Path) -> None:
        get_logger().debug("ffmpeg command: %s", " ".join(command))
        process = await asyncio.to_thread(
            subprocess.run,
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if process.returncode != 0:
            detail = (process.stderr or process.stdout).strip()
            if len(detail) > 4000:
                detail = detail[-4000:]
            raise RuntimeError(f"ffmpeg failed with exit code {process.returncode}: {detail}")
