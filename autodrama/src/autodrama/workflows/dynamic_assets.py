from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from autodrama.core.errors import ProviderError
from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    DynamicAssetSolidificationItem,
    DynamicAssetSolidificationOutput,
    ProjectState,
    ShotDialogueAudioGenerationItem,
    ShotDialogueAudioGenerationOutput,
    ShotVideoGenerationItem,
    ShotVideoGenerationOutput,
    StoryboardEpisodeOutput,
    StoryboardShot,
)
from autodrama.logging import get_logger, log_context
from autodrama.providers.base import ImageGenerationResult, VideoGenerationResult
from autodrama.repositories.dynamic_asset_repo import DynamicAssetRepository
from autodrama.workflows.generation_tasks import (
    find_generation_task,
    generation_tasks_path,
    load_generation_tasks,
    now_iso,
    save_generation_tasks,
    task_status,
    upsert_generation_task,
)
from autodrama.workflows.selection import (
    active_shots_for_episode,
    normalize_shot_selectors,
    shot_matches_selectors,
)


class DynamicAssetNodeMixin:
    def _active_shots_for_episode(self, episode: StoryboardEpisodeOutput) -> list[StoryboardShot]:
        context = getattr(self, "_run_context", None)
        if context is not None and getattr(context, "has_shot_selectors", False):
            return active_shots_for_episode(episode, context.shot_selectors)
        return active_shots_for_episode(episode, getattr(self, "_active_shot_selectors", None))

    @staticmethod
    def _shot_matches_selectors(
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        selectors: set[str],
    ) -> bool:
        return shot_matches_selectors(episode, shot, normalize_shot_selectors(selectors))

    @staticmethod
    def _internal_shot_logs_suppressed(workflow: object) -> bool:
        return bool(getattr(workflow, "_suppress_internal_generation_shot_logs", False))

    @staticmethod
    def _bounded_concurrency(provider: object, *option_names: str, default: int = 1, cap: int = 5) -> int:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        settings = getattr(provider, "settings", None)
        options = getattr(settings, "options", {}) if settings is not None else {}
        value: object = None
        for source in (params, options):
            if not isinstance(source, dict):
                continue
            for name in option_names:
                if name in source:
                    value = source[name]
                    break
            if value is not None:
                break
        if value is None:
            for name in option_names:
                value = getattr(provider, name, None)
                if value is not None:
                    break
        if value is None:
            value = default
        try:
            resolved = int(value)
        except (TypeError, ValueError):
            resolved = default
        return max(1, min(cap, resolved))

    @staticmethod
    def _prompt_log_safe_name(value: object, *, fallback: str) -> str:
        text = str(value or "").strip() or fallback
        invalid = '<>:"/\\|?*'
        cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in text)
        cleaned = cleaned.strip(" .")
        return cleaned or fallback

    @classmethod
    def _prompt_log_shot_dir(
        cls,
        shot: StoryboardShot | None = None,
        *,
        shot_index: int | None = None,
        shot_id: str | None = None,
    ) -> str:
        if shot is not None:
            shot_index = shot.index
            shot_id = shot.shot_id
        try:
            return f"shot_{int(shot_index):03d}"
        except (TypeError, ValueError):
            pass
        return cls._prompt_log_safe_name(shot_id, fallback="shot_unknown")

    @classmethod
    def _generation_prompt_log_path(
        cls,
        project_dir: Path,
        *,
        episode_key: str,
        node_name: str,
        shot: StoryboardShot | None = None,
        shot_index: int | None = None,
        shot_id: str | None = None,
    ) -> Path:
        episode_dir = cls._prompt_log_safe_name(episode_key, fallback="episode_unknown")
        shot_dir = cls._prompt_log_shot_dir(shot, shot_index=shot_index, shot_id=shot_id)
        node_file = cls._prompt_log_safe_name(node_name, fallback="node")
        return project_dir / "logs" / episode_dir / shot_dir / f"{node_file}.log"

    @staticmethod
    def _asset_refs_for_prompt_log(refs: list | None) -> list[dict[str, Any]]:
        logged_refs: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for ref in refs or []:
            ref_type = getattr(ref, "type", None)
            type_key = str(ref_type or "ref")
            counts[type_key] = counts.get(type_key, 0) + 1
            url = getattr(ref, "url", None)
            if isinstance(url, str) and url.startswith("data:") and len(url) > 240:
                url = f"{url[:240]}...[truncated]"
            item = {
                "slot": f"{type_key}_{counts[type_key]}",
                "id": getattr(ref, "id", None),
                "type": ref_type,
                "path": getattr(ref, "path", None),
                "url": url,
                "metadata": getattr(ref, "metadata", {}) or {},
            }
            logged_refs.append({key: value for key, value in item.items() if value not in (None, "", {})})
        return logged_refs

    def _write_generation_prompt_log(
        self,
        project_dir: Path,
        *,
        episode_key: str,
        node_name: str,
        shot: StoryboardShot | None = None,
        shot_index: int | None = None,
        shot_id: str | None = None,
        prompt: str | None = None,
        sections: list[tuple[str, object]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        path = self._generation_prompt_log_path(
            project_dir,
            episode_key=episode_key,
            node_name=node_name,
            shot=shot,
            shot_index=shot_index,
            shot_id=shot_id,
        )
        path.parent.mkdir(parents=True, exist_ok=True)

        resolved_shot_index = shot.index if shot is not None else shot_index
        resolved_shot_id = shot.shot_id if shot is not None else shot_id
        header: dict[str, Any] = {
            "node_name": node_name,
            "episode_key": episode_key,
            "shot_index": resolved_shot_index,
            "shot_id": resolved_shot_id,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }
        if metadata:
            header["metadata"] = metadata

        body: list[str] = [
            "# generation prompt log",
            json.dumps({key: value for key, value in header.items() if value not in (None, "", {})}, ensure_ascii=False, indent=2, default=str),
        ]
        if prompt is not None:
            body.extend(["", "## prompt", str(prompt)])
        for title, value in sections or []:
            body.extend(["", f"## {title}"])
            if isinstance(value, (dict, list)):
                body.append(json.dumps(value, ensure_ascii=False, indent=2, default=str))
            else:
                body.append(str(value))
        path.write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        return path

    def _log_generation_shot_started(self, episode_key: str, shot: StoryboardShot, node_name: str) -> None:
        if self._internal_shot_logs_suppressed(self):
            return
        get_logger().info(
            "%s shot %d %s started",
            episode_key,
            shot.index,
            node_name,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    def _log_generation_shot_finished(
        self,
        episode_key: str,
        shot: StoryboardShot,
        node_name: str,
        saved_path: str,
    ) -> None:
        if self._internal_shot_logs_suppressed(self):
            return
        get_logger().info(
            "%s shot %d %s finished successfully, saved in %s",
            episode_key,
            shot.index,
            node_name,
            saved_path,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    def _log_generation_shot_failed(
        self,
        episode_key: str,
        shot: StoryboardShot,
        node_name: str,
        exc: Exception,
    ) -> None:
        if self._internal_shot_logs_suppressed(self):
            return
        get_logger().error(
            "%s shot %d %s failed, %s",
            episode_key,
            shot.index,
            node_name,
            exc,
            extra={"episode_key": episode_key, "shot_id": shot.shot_id, "shot_index": shot.index},
        )

    async def _run_shot_dialogue_audio_generation_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
    ) -> ShotDialogueAudioGenerationOutput:
        provider = self.router.audio("speech", node_name="shot_dialogue_audio_generation")
        get_logger().info(
            "node=shot_dialogue_audio_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[ShotDialogueAudioGenerationItem] = []
        skipped: list[dict[str, Any]] = []
        episode = self._load_storyboard_episode(project_dir, episode_key)
        changed = False
        for shot in self._active_shots_for_episode(episode):
            with log_context(episode_key=episode.episode_key, shot_id=shot.shot_id):
                if shot.dialogue_audio_assets:
                    changed = True
                shot.dialogue_audio_assets = []
                for line_index, line in enumerate(shot.dialogue, start=1):
                    asset, skip = await self._generate_shot_dialogue_audio(
                        provider=provider,
                        project_dir=project_dir,
                        state=state,
                        episode_key=episode.episode_key,
                        shot=shot,
                        line_index=line_index,
                        line=line,
                    )
                    if skip is not None:
                        skipped.append(skip)
                        continue
                    if asset is None:
                        continue
                    shot.dialogue_audio_assets.append(asset)
                    generated.append(
                        ShotDialogueAudioGenerationItem(
                            episode_key=episode.episode_key,
                            shot_id=shot.shot_id,
                            asset=asset,
                        )
                    )
                    changed = True
        if changed:
            self._save_storyboard_episode(project_dir, episode)

        return ShotDialogueAudioGenerationOutput(
            generated_dialogue_audios=generated,
            skipped_dialogue_lines=skipped,
        )

    async def _run_shot_dialogue_audio_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        generated: list[ShotDialogueAudioGenerationItem] = []
        skipped: list[dict[str, Any]] = []
        for episode_key in self._active_episode_keys_in_order(state):
            output = await self._run_shot_dialogue_audio_generation_for_episode(project_dir, state, episode_key)
            generated.extend(output.generated_dialogue_audios)
            skipped.extend(output.skipped_dialogue_lines)

        self.repo.save_node_output(
            project_dir,
            "shot_dialogue_audio_generation",
            ShotDialogueAudioGenerationOutput(
                generated_dialogue_audios=generated,
                skipped_dialogue_lines=skipped,
            ),
        )
        return state

    @staticmethod
    def _video_success_statuses(provider) -> set[str]:
        statuses = getattr(provider, "_TERMINAL_SUCCESS", {"succeeded", "success", "completed", "done"})
        return {str(status).strip().lower() for status in statuses}

    @staticmethod
    def _video_failure_statuses(provider) -> set[str]:
        statuses = getattr(
            provider,
            "_TERMINAL_FAILURE",
            {"failed", "fail", "error", "expired", "cancelled", "canceled"},
        )
        return {str(status).strip().lower() for status in statuses}

    @staticmethod
    def _shot_video_task_key(episode_key: str, shot_id: str) -> str:
        return f"shot_video_generation:{episode_key}:{shot_id}"

    @staticmethod
    def _path_exists(project_dir: Path, path_value: str | None) -> bool:
        if not path_value:
            return False
        path = Path(path_value)
        if not path.is_absolute():
            path = project_dir / path
        return path.exists() and path.is_file()

    @staticmethod
    def _apply_shot_video_result(
        shot: StoryboardShot,
        *,
        asset_id: str,
        result: VideoGenerationResult,
        provider,
        asset_path: str | None = None,
        last_frame_asset_path: str | None = None,
    ) -> None:
        shot.video_asset_id = asset_id
        if asset_path is not None:
            shot.video_asset_path = asset_path
        shot.video_provider = result.provider or getattr(provider, "name", None)
        shot.video_model = result.model or getattr(provider, "model", None)
        shot.video_task_id = result.task_id
        shot.video_task_status = result.task_status
        shot.video_request_id = result.request_id
        if last_frame_asset_path is not None:
            shot.video_last_frame_asset_path = last_frame_asset_path
        shot.video_usage = result.usage
        shot.video_raw_response = result.raw_response

    @staticmethod
    def _shot_video_item(
        *,
        episode_key: str,
        shot: StoryboardShot,
        asset_id: str,
        prompt: str,
        duration_seconds: float | None,
        asset_path: str | None,
        result: VideoGenerationResult,
        provider,
    ) -> ShotVideoGenerationItem:
        return ShotVideoGenerationItem(
            episode_key=episode_key,
            shot_id=shot.shot_id,
            asset_id=asset_id,
            prompt=prompt,
            duration_seconds=duration_seconds,
            asset_path=asset_path,
            last_frame_asset_path=shot.video_last_frame_asset_path,
            provider=result.provider or getattr(provider, "name", "unknown"),
            model=result.model or getattr(provider, "model", ""),
            task_id=result.task_id,
            task_status=result.task_status,
            request_id=result.request_id,
            usage=result.usage,
            raw_response=result.raw_response,
        )

    @staticmethod
    def _shot_video_task_item(
        *,
        task_key: str,
        state: ProjectState,
        episode_key: str,
        shot: StoryboardShot,
        asset_id: str,
        asset_path: str,
        prompt: str,
        result: VideoGenerationResult | None,
        provider,
        task_status_value: str | None = None,
        completed: bool = False,
    ) -> dict[str, Any]:
        item: dict[str, Any] = {
            "task_key": task_key,
            "node_name": "shot_video_generation",
            "project_id": state.project_id,
            "provider": getattr(provider, "name", "unknown"),
            "model": getattr(provider, "model", ""),
            "episode_key": episode_key,
            "shot_id": shot.shot_id,
            "asset_id": asset_id,
            "asset_path": asset_path,
            "duration_seconds": shot.duration_seconds,
            "prompt": prompt,
        }
        if shot.video_last_frame_asset_path:
            item["last_frame_asset_path"] = shot.video_last_frame_asset_path
        if result is not None:
            item.update(
                {
                    "provider": result.provider or item["provider"],
                    "model": result.model or item["model"],
                    "task_id": result.task_id,
                    "task_status": task_status_value or result.task_status,
                    "video_url": result.video_url,
                    "request_id": result.request_id,
                    "last_frame_url": result.last_frame_url,
                    "usage": result.usage,
                    "raw_response": result.raw_response,
                }
            )
        elif task_status_value is not None:
            item["task_status"] = task_status_value
        if completed:
            item["completed_at"] = now_iso()
        return item

    async def _write_video_last_frame(
        self,
        project_dir: Path,
        asset_id: str,
        result: VideoGenerationResult,
    ) -> str | None:
        if not result.last_frame_url and not result.last_frame_data:
            return None
        image_result = ImageGenerationResult(
            provider=result.provider,
            model=result.model,
            image_urls=[result.last_frame_url] if result.last_frame_url else [],
            image_data=[result.last_frame_data] if result.last_frame_data else [],
            request_id=result.request_id,
            usage=result.usage,
            raw_response=result.raw_response,
        )
        return await self._write_first_generated_image(
            project_dir,
            self._image_asset_path(project_dir, "video_last_frames", f"{asset_id}_last_frame"),
            image_result,
        )

    def _shot_video_inheritance_ready(
        self,
        project_dir: Path,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
    ) -> bool:
        if shot.start_frame_source != "previous_shot_last_frame":
            return True
        previous_shot = self._previous_shot(episode, shot)
        if previous_shot is None:
            return False
        return self._path_exists(project_dir, previous_shot.video_last_frame_asset_path)

    def _shot_video_inheritance_blocked_message(
        self,
        project_dir: Path,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
    ) -> str:
        previous_shot = self._previous_shot(episode, shot)
        if previous_shot is None:
            return (
                f"{shot.shot_id} is configured to start from previous_shot_last_frame, "
                "but there is no previous shot."
            )
        if not previous_shot.video_last_frame_asset_path:
            return (
                f"{shot.shot_id} is configured to start from {previous_shot.shot_id}'s last frame, "
                f"but {previous_shot.shot_id} has no saved last frame yet."
            )
        return (
            f"{shot.shot_id} is configured to start from {previous_shot.shot_id}'s last frame, "
            f"but the file is missing: {project_dir / previous_shot.video_last_frame_asset_path}"
        )

    async def _run_shot_video_generation_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
    ) -> ShotVideoGenerationOutput:
        provider = self.router.video("shot", node_name="shot_video_generation")
        get_logger().info(
            "node=shot_video_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        episode = self._load_storyboard_episode(project_dir, episode_key)
        shots = self._active_shots_for_episode(episode)
        concurrency = self._bounded_concurrency(
            provider,
            "shot_video_generation_concurrency",
            "video_generation_concurrency",
            "max_concurrent_videos",
            default=1,
            cap=5,
        )
        get_logger().info(
            "node=shot_video_generation episode=%s total_videos=%d concurrency=%d",
            episode.episode_key,
            len(shots),
            concurrency,
        )
        registry = load_generation_tasks(project_dir, project_id=state.project_id)
        task_file = generation_tasks_path(project_dir)
        success_statuses = self._video_success_statuses(provider)
        failure_statuses = self._video_failure_statuses(provider)
        non_resumable_statuses = failure_statuses | {"stale"}
        max_polls = int(getattr(provider, "max_polls", 120))
        poll_interval_seconds = float(getattr(provider, "poll_interval_seconds", 5))
        status_log_interval_polls = max(1, int(getattr(provider, "status_log_interval_polls", 6)))
        force_generation = bool(getattr(self, "_force_generation", False))
        semaphore = asyncio.Semaphore(concurrency)
        registry_lock = asyncio.Lock()
        episode_lock = asyncio.Lock()
        reference_mode = self._video_reference_mode(provider)
        waits_for_previous_video = self._video_reference_mode_uses_previous_scene_video(reference_mode)

        async def task_snapshot(task_key: str) -> dict[str, Any] | None:
            async with registry_lock:
                task = find_generation_task(registry, task_key)
                return dict(task) if task is not None else None

        async def save_task_item(task_item: dict[str, Any], *, clear_stale: bool = False) -> dict[str, Any]:
            async with registry_lock:
                task = upsert_generation_task(registry, task_item)
                if clear_stale:
                    for stale_key in (
                        "previous_task_status",
                        "stale_at",
                        "stale_reason",
                        "stale_task_id",
                        "stale_asset_path",
                    ):
                        task.pop(stale_key, None)
                save_generation_tasks(self.repo, project_dir, registry)
                return dict(task)

        async def apply_result_and_save(
            shot: StoryboardShot,
            *,
            asset_id: str,
            result: VideoGenerationResult,
            asset_path: str | None = None,
            last_frame_asset_path: str | None = None,
        ) -> None:
            async with episode_lock:
                self._apply_shot_video_result(
                    shot,
                    asset_id=asset_id,
                    result=result,
                    provider=provider,
                    asset_path=asset_path,
                    last_frame_asset_path=last_frame_asset_path,
                )
                self._save_storyboard_episode(project_dir, episode)

        async def process_shot(shot: StoryboardShot) -> ShotVideoGenerationItem:
            self._log_generation_shot_started(episode.episode_key, shot, "shot_video_generation")
            asset_id = normalize_id(f"{shot.shot_id}", "video")
            task_key = self._shot_video_task_key(episode.episode_key, shot.shot_id)
            output_path = self._video_asset_path(project_dir, "shots", asset_id)
            planned_asset_path = self._project_relative(project_dir, output_path)
            existing_task = await task_snapshot(task_key)
            existing_status = task_status(existing_task)
            saved_asset_path = (
                str((existing_task or {}).get("asset_path") or shot.video_asset_path or "")
                if existing_task or shot.video_asset_path
                else ""
            )
            stale_success_missing_asset = (
                not force_generation
                and existing_task is not None
                and existing_status in success_statuses
                and not self._path_exists(project_dir, saved_asset_path)
            )
            if stale_success_missing_asset:
                get_logger().warning(
                    "%s previous video task %s is terminal success but local asset is missing (%s); "
                    "marking task stale and submitting a new task",
                    shot.shot_id,
                    existing_task.get("task_id") or "-",
                    saved_asset_path or planned_asset_path,
                    extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                )
                existing_task = await save_task_item(
                    {
                        "task_key": task_key,
                        "task_status": "stale",
                        "previous_task_status": existing_status,
                        "stale_at": now_iso(),
                        "stale_reason": "terminal_success_asset_missing",
                        "stale_task_id": existing_task.get("task_id"),
                        "stale_asset_path": saved_asset_path or planned_asset_path,
                    },
                )
                existing_status = task_status(existing_task)

            if not force_generation and existing_status in success_statuses and self._path_exists(project_dir, saved_asset_path):
                recorded_prompt = str(
                    (existing_task or {}).get("prompt")
                    or shot.video_generation_prompt
                    or self._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)
                )
                shot.video_generation_prompt = recorded_prompt
                self._write_generation_prompt_log(
                    project_dir,
                    episode_key=episode.episode_key,
                    shot=shot,
                    node_name="shot_video_generation",
                    prompt=recorded_prompt,
                    metadata={
                        "provider": getattr(provider, "name", "unknown"),
                        "model": getattr(provider, "model", "-"),
                        "model_call": "reused existing successful video task",
                        "asset_id": asset_id,
                        "task_id": (existing_task or {}).get("task_id"),
                        "task_status": existing_status,
                    },
                )
                result = VideoGenerationResult(
                    provider=str(
                        (existing_task or {}).get("provider")
                        or shot.video_provider
                        or getattr(provider, "name", "unknown")
                    ),
                    model=str((existing_task or {}).get("model") or shot.video_model or getattr(provider, "model", "")),
                    task_id=str((existing_task or {}).get("task_id") or shot.video_task_id or "") or None,
                    task_status=str((existing_task or {}).get("task_status") or shot.video_task_status or "succeeded"),
                    video_url=str((existing_task or {}).get("video_url") or "") or None,
                    last_frame_url=str((existing_task or {}).get("last_frame_url") or "") or None,
                    request_id=str((existing_task or {}).get("request_id") or shot.video_request_id or "") or None,
                    usage=dict((existing_task or {}).get("usage") or shot.video_usage or {}),
                    raw_response=dict((existing_task or {}).get("raw_response") or shot.video_raw_response or {}),
                )
                last_frame_asset_path = (
                    str((existing_task or {}).get("last_frame_asset_path") or shot.video_last_frame_asset_path or "")
                    or None
                )
                if last_frame_asset_path and not self._path_exists(project_dir, last_frame_asset_path):
                    last_frame_asset_path = None
                if not last_frame_asset_path:
                    last_frame_asset_path = await self._write_video_last_frame(project_dir, asset_id, result)
                    if last_frame_asset_path:
                        await save_task_item(
                            {
                                "task_key": task_key,
                                "last_frame_asset_path": last_frame_asset_path,
                            },
                        )
                await apply_result_and_save(
                    shot,
                    asset_id=asset_id,
                    result=result,
                    asset_path=saved_asset_path,
                    last_frame_asset_path=last_frame_asset_path,
                )
                item = self._shot_video_item(
                    episode_key=episode.episode_key,
                    shot=shot,
                    asset_id=asset_id,
                    prompt=recorded_prompt,
                    duration_seconds=shot.duration_seconds,
                    asset_path=saved_asset_path,
                    result=result,
                    provider=provider,
                )
                self._log_generation_shot_finished(
                    episode.episode_key,
                    shot,
                    "shot_video_generation",
                    saved_asset_path,
                )
                return item

            task: dict[str, Any]
            last_logged_status: str | None
            if (
                not force_generation
                and existing_task
                and existing_task.get("task_id")
                and existing_status not in non_resumable_statuses
            ):
                task = existing_task
                last_logged_status = existing_status
                prompt = str(
                    existing_task.get("prompt")
                    or shot.video_generation_prompt
                    or self._shot_video_prompt(state, episode, shot, provider=provider, project_dir=project_dir)
                )
                shot.video_generation_prompt = prompt
                async with episode_lock:
                    self._save_storyboard_episode(project_dir, episode)
                self._write_generation_prompt_log(
                    project_dir,
                    episode_key=episode.episode_key,
                    shot=shot,
                    node_name="shot_video_generation",
                    prompt=prompt,
                    metadata={
                        "provider": getattr(provider, "name", "unknown"),
                        "model": getattr(provider, "model", "-"),
                        "model_call": "resumed existing video task",
                        "asset_id": asset_id,
                        "task_id": existing_task.get("task_id"),
                        "task_status": existing_status,
                    },
                )
                get_logger().info(
                    "%s video task resumed: %s status=%s queue=%s",
                    shot.shot_id,
                    existing_task.get("task_id"),
                    existing_status or "-",
                    self._project_relative(project_dir, task_file),
                    extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                )
            else:
                if not self._shot_video_inheritance_ready(project_dir, episode, shot):
                    message = self._shot_video_inheritance_blocked_message(project_dir, episode, shot)
                    get_logger().error("%s", message)
                    self._log_generation_shot_failed(
                        episode.episode_key,
                        shot,
                        "shot_video_generation",
                        ProviderError(message),
                    )
                    raise ProviderError(message)

                if existing_task and existing_status in failure_statuses:
                    get_logger().info(
                        "%s previous video task %s ended with status=%s; submitting a new task",
                        shot.shot_id,
                        existing_task.get("task_id"),
                        existing_status,
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                elif existing_task and existing_status == "stale":
                    get_logger().info(
                        "%s previous video task %s is stale reason=%s; submitting a new task",
                        shot.shot_id,
                        existing_task.get("stale_task_id") or existing_task.get("task_id") or "-",
                        existing_task.get("stale_reason") or "-",
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                elif force_generation and existing_task:
                    get_logger().info(
                        "%s forced video regeneration; ignoring previous task %s status=%s",
                        shot.shot_id,
                        existing_task.get("task_id") or "-",
                        existing_status or "-",
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )

                try:
                    prompt = await self._shot_video_prompt_async(
                        state,
                        episode,
                        shot,
                        provider=provider,
                        project_dir=project_dir,
                    )
                    video_refs = self._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode)
                    video_refs = self._shot_video_refs_for_provider(video_refs, provider=provider)
                    reference_plan = self._shot_video_reference_plan(video_refs, provider=provider)
                    shot.video_generation_prompt = prompt
                    async with episode_lock:
                        self._save_storyboard_episode(project_dir, episode)
                    self._write_generation_prompt_log(
                        project_dir,
                        episode_key=episode.episode_key,
                        shot=shot,
                        node_name="shot_video_generation",
                        prompt=prompt,
                        sections=[
                            ("reference_plan", reference_plan),
                            ("reference_assets", self._asset_refs_for_prompt_log(video_refs)),
                        ],
                        metadata={
                            "provider": getattr(provider, "name", "unknown"),
                            "model": getattr(provider, "model", "-"),
                            "model_call": "video.submit_video",
                            "asset_id": asset_id,
                            "duration_seconds": shot.duration_seconds,
                        },
                    )
                    result = await provider.submit_video(
                        prompt,
                        refs=video_refs,
                        duration=shot.duration_seconds,
                        metadata={
                            "node_name": "shot_video_generation",
                            "project_id": state.project_id,
                            "episode_key": episode.episode_key,
                            "shot_id": shot.shot_id,
                            "asset_id": asset_id,
                            "reference_plan": reference_plan,
                        },
                    )
                except Exception as exc:
                    self._log_generation_shot_failed(episode.episode_key, shot, "shot_video_generation", exc)
                    raise
                if not result.task_id:
                    error = ProviderError(f"Video provider submit result has no task_id for {shot.shot_id}")
                    self._log_generation_shot_failed(
                        episode.episode_key,
                        shot,
                        "shot_video_generation",
                        error,
                    )
                    raise error
                task = await save_task_item(
                    self._shot_video_task_item(
                        task_key=task_key,
                        state=state,
                        episode_key=episode.episode_key,
                        shot=shot,
                        asset_id=asset_id,
                        asset_path=planned_asset_path,
                        prompt=prompt,
                        result=result,
                        provider=provider,
                    ),
                    clear_stale=True,
                )
                await apply_result_and_save(shot, asset_id=asset_id, result=result)
                last_logged_status = task_status(task)
                get_logger().info(
                    "%s video task submitted: %s queue=%s",
                    shot.shot_id,
                    result.task_id,
                    self._project_relative(project_dir, task_file),
                    extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                )

            task_id = str(task.get("task_id") or "")
            if not task_id:
                error = ProviderError(f"Missing video task_id for {shot.shot_id}; queue={task_file}")
                self._log_generation_shot_failed(episode.episode_key, shot, "shot_video_generation", error)
                raise error

            completed = False
            for poll_index in range(1, max_polls + 1):
                result = await provider.query_video_task(task_id)
                status = (result.task_status or "").strip().lower()
                task = await save_task_item(
                    self._shot_video_task_item(
                        task_key=task_key,
                        state=state,
                        episode_key=episode.episode_key,
                        shot=shot,
                        asset_id=asset_id,
                        asset_path=planned_asset_path,
                        prompt=prompt,
                        result=result,
                        provider=provider,
                    ),
                )
                await apply_result_and_save(shot, asset_id=asset_id, result=result)

                if status != last_logged_status or poll_index == 1 or poll_index % status_log_interval_polls == 0:
                    get_logger().info(
                        "%s video task %s status=%s poll=%d/%d",
                        shot.shot_id,
                        task_id,
                        status or "-",
                        poll_index,
                        max_polls,
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                    last_logged_status = status

                if status in success_statuses:
                    asset_path = await self._write_generated_video(
                        project_dir,
                        output_path,
                        result,
                    )
                    if not asset_path:
                        error = ProviderError(f"Video task {task_id} succeeded but returned no video URL or data")
                        self._log_generation_shot_failed(episode.episode_key, shot, "shot_video_generation", error)
                        raise error
                    last_frame_asset_path = await self._write_video_last_frame(
                        project_dir,
                        asset_id,
                        result,
                    )
                    await apply_result_and_save(
                        shot,
                        asset_id=asset_id,
                        result=result,
                        asset_path=asset_path,
                        last_frame_asset_path=last_frame_asset_path,
                    )
                    completed_task_item = self._shot_video_task_item(
                        task_key=task_key,
                        state=state,
                        episode_key=episode.episode_key,
                        shot=shot,
                        asset_id=asset_id,
                        asset_path=asset_path,
                        prompt=prompt,
                        result=result,
                        provider=provider,
                        completed=True,
                    )
                    if last_frame_asset_path:
                        completed_task_item["last_frame_asset_path"] = last_frame_asset_path
                    task = await save_task_item(completed_task_item)
                    item = self._shot_video_item(
                        episode_key=episode.episode_key,
                        shot=shot,
                        asset_id=asset_id,
                        prompt=prompt,
                        duration_seconds=shot.duration_seconds,
                        asset_path=asset_path,
                        result=result,
                        provider=provider,
                    )
                    self._log_generation_shot_finished(
                        episode.episode_key,
                        shot,
                        "shot_video_generation",
                        asset_path,
                    )
                    completed = True
                    return item

                if status in failure_statuses:
                    task = await save_task_item(
                        {
                            "task_key": task_key,
                            "task_status": result.task_status,
                            "failed_at": now_iso(),
                        },
                    )
                    get_logger().error(
                        "%s video task %s failed with status=%s queue=%s",
                        shot.shot_id,
                        task_id,
                        result.task_status,
                        self._project_relative(project_dir, task_file),
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                    error = ProviderError(f"Video task {task_id} ended with status {result.task_status}; queue={task_file}")
                    self._log_generation_shot_failed(
                        episode.episode_key,
                        shot,
                        "shot_video_generation",
                        error,
                    )
                    raise error

                if poll_index < max_polls:
                    await asyncio.sleep(poll_interval_seconds)

            if not completed:
                last_status = task_status(task) or "timeout"
                task = await save_task_item(
                    {
                        "task_key": task_key,
                        "task_status": "timeout",
                        "previous_task_status": last_status,
                        "timed_out_at": now_iso(),
                    },
                )
                get_logger().error(
                    "%s video task %s timed out after %d polls; queue=%s",
                    shot.shot_id,
                    task.get("task_id"),
                    max_polls,
                    self._project_relative(project_dir, task_file),
                    extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                )
                error = ProviderError(
                    f"Video task {task.get('task_id')} did not finish after {max_polls} polls; "
                    f"saved in {task_file}. Rerun shot_video_generation to resume."
                )
                self._log_generation_shot_failed(
                    episode.episode_key,
                    shot,
                    "shot_video_generation",
                    error,
                )
                raise error

            raise ProviderError(f"Video task for {shot.shot_id} ended without returning an item")

        async def run_shot(
            shot: StoryboardShot,
            previous_task: asyncio.Task[ShotVideoGenerationItem] | None,
        ) -> ShotVideoGenerationItem:
            if previous_task is not None:
                await previous_task
            async with semaphore:
                return await process_shot(shot)

        tasks: list[asyncio.Task[ShotVideoGenerationItem]] = []
        tasks_by_shot_id: dict[str, asyncio.Task[ShotVideoGenerationItem]] = {}
        previous_active_task: asyncio.Task[ShotVideoGenerationItem] | None = None
        for shot in shots:
            previous_task: asyncio.Task[ShotVideoGenerationItem] | None = None
            if waits_for_previous_video:
                previous_task = previous_active_task
            elif shot.start_frame_source == "previous_shot_last_frame":
                previous_shot = self._previous_shot(episode, shot)
                if previous_shot is not None:
                    previous_task = tasks_by_shot_id.get(previous_shot.shot_id)
            task = asyncio.create_task(run_shot(shot, previous_task))
            tasks.append(task)
            tasks_by_shot_id[shot.shot_id] = task
            previous_active_task = task

        try:
            generated = list(await asyncio.gather(*tasks))
        except Exception:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        async with episode_lock:
            self._save_storyboard_episode(project_dir, episode)

        return ShotVideoGenerationOutput(generated_videos=generated)

    async def _run_shot_video_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        generated: list[ShotVideoGenerationItem] = []
        for episode_key in self._active_episode_keys_in_order(state):
            output = await self._run_shot_video_generation_for_episode(project_dir, state, episode_key)
            generated.extend(output.generated_videos)

        self.repo.save_node_output(
            project_dir,
            "shot_video_generation",
            ShotVideoGenerationOutput(generated_videos=generated),
        )
        return state

    async def _run_dynamic_asset_solidification_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
    ) -> DynamicAssetSolidificationOutput:
        get_logger().info("node=dynamic_asset_solidification provider=local model=-")
        solidified: list[DynamicAssetSolidificationItem] = []
        episode = self._load_storyboard_episode(project_dir, episode_key)
        video_provider = self.router.video("shot", node_name="shot_video_generation")
        for shot in self._active_shots_for_episode(episode):
            self._log_generation_shot_started(episode.episode_key, shot, "dynamic_asset_solidification")
            try:
                shot.solidified_asset_ids = []
                prompt_log_sections: list[tuple[str, object]] = [
                    (
                        "node_note",
                        "dynamic_asset_solidification is a local asset-indexing node; it does not submit a new prompt to a model.",
                    )
                ]
                for audio in shot.dialogue_audio_assets:
                    shot.solidified_asset_ids.append(audio.asset_id)
                    solidified.append(
                        DynamicAssetSolidificationItem(
                            asset_id=audio.asset_id,
                            asset_type="shot_dialogue_audio",
                            episode_key=episode.episode_key,
                            shot_id=shot.shot_id,
                            asset_path=audio.asset_path,
                            source_node="shot_dialogue_audio_generation",
                            metadata={
                                "role_id": audio.role_id,
                                "role_name": audio.role_name,
                                "line_index": audio.line_index,
                                "text": audio.text,
                                "emotion": audio.emotion,
                                "duration_seconds": audio.duration_seconds,
                                "original_duration_seconds": audio.original_duration_seconds,
                                "duration_limited": audio.duration_limited,
                            },
                        )
                    )
                if shot.video_asset_id:
                    video_generation_prompt = shot.video_generation_prompt or self._shot_video_prompt(
                        state,
                        episode,
                        shot,
                        provider=video_provider,
                        project_dir=project_dir,
                    )
                    prompt_log_sections.append(("shot_video_generation_prompt_snapshot", video_generation_prompt))
                    per_second_content = shot.per_second_content or shot.video_prompt
                    shot.solidified_asset_ids.append(shot.video_asset_id)
                    solidified.append(
                        DynamicAssetSolidificationItem(
                            asset_id=shot.video_asset_id,
                            asset_type="shot_video",
                            episode_key=episode.episode_key,
                            shot_id=shot.shot_id,
                            asset_path=shot.video_asset_path,
                            source_node="shot_video_generation",
                            metadata={
                                "prompt": video_generation_prompt,
                                "per_second_content": per_second_content,
                                "source_prompt": shot.video_prompt,
                                "source_per_second_content": shot.per_second_content or shot.video_prompt,
                                "generation_prompt": video_generation_prompt,
                                "provider": shot.video_provider,
                                "model": shot.video_model,
                                "task_id": shot.video_task_id,
                                "task_status": shot.video_task_status,
                                "duration_seconds": shot.duration_seconds,
                            },
                        )
                    )
                self._write_generation_prompt_log(
                    project_dir,
                    episode_key=episode.episode_key,
                    shot=shot,
                    node_name="dynamic_asset_solidification",
                    sections=prompt_log_sections,
                    metadata={
                        "provider": "local",
                        "model": "-",
                        "model_call": "none",
                        "solidified_asset_ids": list(shot.solidified_asset_ids),
                    },
                )
                self._save_storyboard_episode(project_dir, episode)
                self._log_generation_shot_finished(
                    episode.episode_key,
                    shot,
                    "dynamic_asset_solidification",
                    self._project_relative(project_dir, self._shot_path(project_dir, episode_key)),
                )
            except Exception as exc:
                self._log_generation_shot_failed(
                    episode.episode_key,
                    shot,
                    "dynamic_asset_solidification",
                    exc,
                )
                raise
        self._save_storyboard_episode(project_dir, episode)
        return DynamicAssetSolidificationOutput(solidified_assets=solidified)

    async def _run_dynamic_asset_solidification(self, project_dir: Path, state: ProjectState) -> ProjectState:
        solidified: list[DynamicAssetSolidificationItem] = []
        for episode_key in self._active_episode_keys_in_order(state):
            output = await self._run_dynamic_asset_solidification_for_episode(project_dir, state, episode_key)
            solidified.extend(output.solidified_assets)

        active_episode_keys = set(self._active_episode_keys_in_order(state))
        dynamic_assets = getattr(self, "dynamic_assets", None)
        if dynamic_assets is None:
            dynamic_assets = DynamicAssetRepository(self.repo, self.layout)
        dynamic_assets.merge_assets(
            project_dir,
            solidified,
            replace_episode_keys=active_episode_keys,
        )
        self.repo.save_node_output(
            project_dir,
            "dynamic_asset_solidification",
            DynamicAssetSolidificationOutput(solidified_assets=solidified),
        )
        return state
