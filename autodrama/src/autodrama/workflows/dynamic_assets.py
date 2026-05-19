from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from autodrama.core.errors import ProviderError
from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    DynamicAssetSolidificationItem,
    DynamicAssetSolidificationOutput,
    ProjectState,
    RefFrameGenerationItem,
    RefFrameGenerationOutput,
    ShotDialogueAudioGenerationItem,
    ShotDialogueAudioGenerationOutput,
    ShotVideoGenerationItem,
    ShotVideoGenerationOutput,
    StoryboardEpisodeOutput,
    StoryboardGenerationOutput,
    StoryboardShot,
)
from autodrama.logging import get_logger, log_context
from autodrama.providers.base import ImageGenerationResult, VideoGenerationResult
from autodrama.workflows.generation_tasks import (
    find_generation_task,
    generation_tasks_path,
    load_generation_tasks,
    now_iso,
    save_generation_tasks,
    task_status,
    upsert_generation_task,
)
from autodrama.workflows.storyboard_history import history_before_episode


class DynamicAssetNodeMixin:
    def _active_shots_for_episode(self, episode: StoryboardEpisodeOutput) -> list[StoryboardShot]:
        selectors = getattr(self, "_active_shot_selectors", None)
        if not selectors:
            return list(episode.shots)

        normalized_selectors = {
            str(selector).strip().lower().replace("-", "_")
            for selector in selectors
            if str(selector).strip()
        }
        selected = [
            shot
            for shot in episode.shots
            if self._shot_matches_selectors(episode, shot, normalized_selectors)
        ]
        if not selected:
            raise ValueError(
                f"No shots matched selectors {', '.join(sorted(normalized_selectors))} "
                f"for {episode.episode_key}"
            )
        return selected

    @staticmethod
    def _shot_matches_selectors(
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        selectors: set[str],
    ) -> bool:
        shot_id = str(shot.shot_id).lower().replace("-", "_")
        keys = {
            shot_id,
            str(shot.index),
            f"{shot.index:03d}",
            f"shot_{shot.index}",
            f"shot_{shot.index:03d}",
            f"{episode.episode_key}_shot_{shot.index}",
            f"{episode.episode_key}_shot_{shot.index:03d}",
        }
        return bool(keys.intersection(selectors))

    async def _run_storyboard_generation_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
    ) -> StoryboardGenerationOutput:
        provider = self.router.text("storyboard")
        get_logger().info(
            "node=storyboard_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        shots_dir = project_dir / "shots"
        shots_dir.mkdir(parents=True, exist_ok=True)
        shot_path = shots_dir / f"{episode_key}.json"

        def save_storyboard_progress(episode: StoryboardEpisodeOutput, shot: StoryboardShot) -> None:
            self.repo.write_json(shot_path, episode.model_dump(mode="json", exclude_none=True))
            get_logger().info(
                "episode %s shot %s generated, saved in %s",
                episode.episode_key,
                shot.shot_id,
                self._project_relative(project_dir, shot_path),
                extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
            )

        output = await self.storyboard_service.storyboard_episode(
            state,
            provider,
            episode_key=episode_key,
            previous_storyboard_history=history_before_episode(project_dir, state, episode_key),
            on_shot_generated=save_storyboard_progress,
        )
        if output.episode_key != episode_key:
            raise ValueError(f"Storyboard episode_key must be {episode_key}; got {output.episode_key}")
        self.repo.write_json(shot_path, output.model_dump(mode="json", exclude_none=True))
        state.budget.used_text_calls += max(1, int(getattr(self.storyboard_service, "last_text_call_count", 1)))
        return StoryboardGenerationOutput(generated_episodes=[episode_key])

    async def _run_storyboard_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        generated_episode_keys: list[str] = []
        for episode_key in self._active_episode_keys_in_order(state):
            output = await self._run_storyboard_generation_for_episode(project_dir, state, episode_key)
            generated_episode_keys.extend(output.generated_episodes)

        self.repo.save_node_output(
            project_dir,
            "storyboard_generation",
            StoryboardGenerationOutput(generated_episodes=generated_episode_keys),
        )
        return state

    async def _run_shot_dialogue_audio_generation_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
    ) -> ShotDialogueAudioGenerationOutput:
        provider = self.router.audio("speech")
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

    async def _run_ref_frame_generation_for_episode(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
    ) -> RefFrameGenerationOutput:
        provider = self.router.image("ref_frame")
        get_logger().info(
            "node=ref_frame_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[RefFrameGenerationItem] = []
        episode = self._load_storyboard_episode(project_dir, episode_key)
        shots = self._active_shots_for_episode(episode)
        get_logger().info(
            "node=ref_frame_generation episode=%s total_images=%d",
            episode.episode_key,
            len(shots),
        )
        for shot in shots:
            with log_context(episode_key=episode.episode_key, shot_id=shot.shot_id):
                asset_id = normalize_id(f"{shot.shot_id}", "ref_frame")
                prompt = self._shot_ref_frame_prompt(state, episode, shot)
                refs = None
                if getattr(provider, "supports_reference_images", False):
                    refs = self._shot_ref_asset_refs(project_dir, state, shot)
                result = await provider.generate_image(
                    prompt,
                    refs=refs,
                    metadata={
                        "node_name": "ref_frame_generation",
                        "project_id": state.project_id,
                        "episode_key": episode.episode_key,
                        "shot_id": shot.shot_id,
                        "asset_id": asset_id,
                    },
                )
                asset_path = await self._write_first_generated_image(
                    project_dir,
                    self._image_asset_path(project_dir, "ref_frames", asset_id),
                    result,
                )
                shot.ref_frame_asset_id = asset_id
                shot.ref_frame_asset_path = asset_path
                shot.ref_frame_provider = result.provider
                shot.ref_frame_model = result.model
                shot.ref_frame_request_id = result.request_id
                shot.ref_frame_usage = result.usage
                shot.ref_frame_raw_response = result.raw_response
                generated.append(
                    RefFrameGenerationItem(
                        episode_key=episode.episode_key,
                        shot_id=shot.shot_id,
                        asset_id=asset_id,
                        prompt=prompt,
                        asset_path=asset_path,
                        provider=result.provider,
                        model=result.model,
                        request_id=result.request_id,
                        usage=result.usage,
                        raw_response=result.raw_response,
                    )
                )
                get_logger().info("%s generated successfully, saved in %s", asset_id, asset_path)
        self._save_storyboard_episode(project_dir, episode)

        return RefFrameGenerationOutput(generated_ref_frames=generated)

    async def _run_ref_frame_generation(self, project_dir: Path, state: ProjectState) -> ProjectState:
        generated: list[RefFrameGenerationItem] = []
        for episode_key in self._active_episode_keys_in_order(state):
            output = await self._run_ref_frame_generation_for_episode(project_dir, state, episode_key)
            generated.extend(output.generated_ref_frames)

        self.repo.save_node_output(
            project_dir,
            "ref_frame_generation",
            RefFrameGenerationOutput(generated_ref_frames=generated),
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
        provider = self.router.video("shot")
        get_logger().info(
            "node=shot_video_generation provider=%s model=%s",
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
        )
        generated: list[ShotVideoGenerationItem] = []
        episode = self._load_storyboard_episode(project_dir, episode_key)
        shots = self._active_shots_for_episode(episode)
        get_logger().info(
            "node=shot_video_generation episode=%s total_videos=%d",
            episode.episode_key,
            len(shots),
        )
        registry = load_generation_tasks(project_dir, project_id=state.project_id)
        task_file = generation_tasks_path(project_dir)
        success_statuses = self._video_success_statuses(provider)
        failure_statuses = self._video_failure_statuses(provider)
        max_polls = int(getattr(provider, "max_polls", 120))
        poll_interval_seconds = float(getattr(provider, "poll_interval_seconds", 5))
        status_log_interval_polls = max(1, int(getattr(provider, "status_log_interval_polls", 6)))
        remaining_shots = list(shots)

        while remaining_shots:
            pending: dict[str, dict[str, Any]] = {}
            deferred_shots: list[StoryboardShot] = []
            made_progress = False

            for shot in remaining_shots:
                asset_id = normalize_id(f"{shot.shot_id}", "video")
                prompt = self._shot_video_prompt(state, episode, shot)
                task_key = self._shot_video_task_key(episode.episode_key, shot.shot_id)
                output_path = self._video_asset_path(project_dir, "shots", asset_id)
                planned_asset_path = self._project_relative(project_dir, output_path)
                existing_task = find_generation_task(registry, task_key)
                existing_status = task_status(existing_task)
                saved_asset_path = (
                    str((existing_task or {}).get("asset_path") or shot.video_asset_path or "")
                    if existing_task or shot.video_asset_path
                    else ""
                )

                if existing_status in success_statuses and self._path_exists(project_dir, saved_asset_path):
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
                            upsert_generation_task(
                                registry,
                                {
                                    "task_key": task_key,
                                    "last_frame_asset_path": last_frame_asset_path,
                                },
                            )
                            save_generation_tasks(self.repo, project_dir, registry)
                    self._apply_shot_video_result(
                        shot,
                        asset_id=asset_id,
                        result=result,
                        provider=provider,
                        asset_path=saved_asset_path,
                        last_frame_asset_path=last_frame_asset_path,
                    )
                    self._save_storyboard_episode(project_dir, episode)
                    generated.append(
                        self._shot_video_item(
                            episode_key=episode.episode_key,
                            shot=shot,
                            asset_id=asset_id,
                            prompt=prompt,
                            duration_seconds=shot.duration_seconds,
                            asset_path=saved_asset_path,
                            result=result,
                            provider=provider,
                        )
                    )
                    get_logger().info(
                        "%s already generated, saved in %s",
                        shot.shot_id,
                        saved_asset_path,
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                    made_progress = True
                    continue

                if existing_task and existing_task.get("task_id") and existing_status not in failure_statuses:
                    pending[task_key] = {
                        "shot": shot,
                        "asset_id": asset_id,
                        "prompt": prompt,
                        "output_path": output_path,
                        "planned_asset_path": planned_asset_path,
                        "task": existing_task,
                        "last_logged_status": existing_status,
                    }
                    get_logger().info(
                        "%s video task resumed: %s status=%s queue=%s",
                        shot.shot_id,
                        existing_task.get("task_id"),
                        existing_status or "-",
                        self._project_relative(project_dir, task_file),
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                    made_progress = True
                    continue

                if not self._shot_video_inheritance_ready(project_dir, episode, shot):
                    deferred_shots.append(shot)
                    continue

                if existing_task and existing_status in failure_statuses:
                    get_logger().info(
                        "%s previous video task %s ended with status=%s; submitting a new task",
                        shot.shot_id,
                        existing_task.get("task_id"),
                        existing_status,
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )

                result = await provider.submit_video(
                    prompt,
                    refs=self._shot_video_refs(project_dir, state, shot, provider=provider, episode=episode),
                    duration=shot.duration_seconds,
                    metadata={
                        "node_name": "shot_video_generation",
                        "project_id": state.project_id,
                        "episode_key": episode.episode_key,
                        "shot_id": shot.shot_id,
                        "asset_id": asset_id,
                    },
                )
                if not result.task_id:
                    raise ProviderError(f"Video provider submit result has no task_id for {shot.shot_id}")
                task = upsert_generation_task(
                    registry,
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
                save_generation_tasks(self.repo, project_dir, registry)
                self._apply_shot_video_result(shot, asset_id=asset_id, result=result, provider=provider)
                self._save_storyboard_episode(project_dir, episode)
                pending[task_key] = {
                    "shot": shot,
                    "asset_id": asset_id,
                    "prompt": prompt,
                    "output_path": output_path,
                    "planned_asset_path": planned_asset_path,
                    "task": task,
                    "last_logged_status": task_status(task),
                }
                get_logger().info(
                    "%s video task submitted: %s queue=%s",
                    shot.shot_id,
                    result.task_id,
                    self._project_relative(project_dir, task_file),
                    extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                )
                made_progress = True

            for poll_index in range(1, max_polls + 1):
                if not pending:
                    break
                completed_task_keys: list[str] = []
                for task_key, item in list(pending.items()):
                    task = item["task"]
                    task_id = str(task.get("task_id") or "")
                    if not task_id:
                        raise ProviderError(f"Missing video task_id for {item['shot'].shot_id}; queue={task_file}")

                    result = await provider.query_video_task(task_id)
                    status = (result.task_status or "").strip().lower()
                    task = upsert_generation_task(
                        registry,
                        self._shot_video_task_item(
                            task_key=task_key,
                            state=state,
                            episode_key=episode.episode_key,
                            shot=item["shot"],
                            asset_id=item["asset_id"],
                            asset_path=item["planned_asset_path"],
                            prompt=item["prompt"],
                            result=result,
                            provider=provider,
                        ),
                    )
                    item["task"] = task
                    save_generation_tasks(self.repo, project_dir, registry)
                    self._apply_shot_video_result(item["shot"], asset_id=item["asset_id"], result=result, provider=provider)
                    self._save_storyboard_episode(project_dir, episode)

                    if (
                        status != item.get("last_logged_status")
                        or poll_index == 1
                        or poll_index % status_log_interval_polls == 0
                    ):
                        get_logger().info(
                            "%s video task %s status=%s poll=%d/%d",
                            item["shot"].shot_id,
                            task_id,
                            status or "-",
                            poll_index,
                            max_polls,
                            extra={"episode_key": episode.episode_key, "shot_id": item["shot"].shot_id},
                        )
                        item["last_logged_status"] = status

                    if status in success_statuses:
                        asset_path = await self._write_generated_video(
                            project_dir,
                            item["output_path"],
                            result,
                        )
                        if not asset_path:
                            raise ProviderError(f"Video task {task_id} succeeded but returned no video URL or data")
                        last_frame_asset_path = await self._write_video_last_frame(
                            project_dir,
                            item["asset_id"],
                            result,
                        )
                        self._apply_shot_video_result(
                            item["shot"],
                            asset_id=item["asset_id"],
                            result=result,
                            provider=provider,
                            asset_path=asset_path,
                            last_frame_asset_path=last_frame_asset_path,
                        )
                        self._save_storyboard_episode(project_dir, episode)
                        completed_task_item = self._shot_video_task_item(
                            task_key=task_key,
                            state=state,
                            episode_key=episode.episode_key,
                            shot=item["shot"],
                            asset_id=item["asset_id"],
                            asset_path=asset_path,
                            prompt=item["prompt"],
                            result=result,
                            provider=provider,
                            completed=True,
                        )
                        if last_frame_asset_path:
                            completed_task_item["last_frame_asset_path"] = last_frame_asset_path
                        task = upsert_generation_task(registry, completed_task_item)
                        item["task"] = task
                        save_generation_tasks(self.repo, project_dir, registry)
                        generated.append(
                            self._shot_video_item(
                                episode_key=episode.episode_key,
                                shot=item["shot"],
                                asset_id=item["asset_id"],
                                prompt=item["prompt"],
                                duration_seconds=item["shot"].duration_seconds,
                                asset_path=asset_path,
                                result=result,
                                provider=provider,
                            )
                        )
                        get_logger().info(
                            "%s generated successfully, saved in %s",
                            item["shot"].shot_id,
                            asset_path,
                            extra={"episode_key": episode.episode_key, "shot_id": item["shot"].shot_id},
                        )
                        completed_task_keys.append(task_key)
                        continue

                    if status in failure_statuses:
                        task = upsert_generation_task(
                            registry,
                            {
                                "task_key": task_key,
                                "task_status": result.task_status,
                                "failed_at": now_iso(),
                            },
                        )
                        item["task"] = task
                        save_generation_tasks(self.repo, project_dir, registry)
                        get_logger().error(
                            "%s video task %s failed with status=%s queue=%s",
                            item["shot"].shot_id,
                            task_id,
                            result.task_status,
                            self._project_relative(project_dir, task_file),
                            extra={"episode_key": episode.episode_key, "shot_id": item["shot"].shot_id},
                        )
                        raise ProviderError(f"Video task {task_id} ended with status {result.task_status}; queue={task_file}")

                for task_key in completed_task_keys:
                    pending.pop(task_key, None)

                if pending and poll_index < max_polls:
                    await asyncio.sleep(poll_interval_seconds)

            if pending:
                for task_key, item in pending.items():
                    last_status = task_status(item["task"]) or "timeout"
                    task = upsert_generation_task(
                        registry,
                        {
                            "task_key": task_key,
                            "task_status": "timeout",
                            "previous_task_status": last_status,
                            "timed_out_at": now_iso(),
                        },
                    )
                    item["task"] = task
                save_generation_tasks(self.repo, project_dir, registry)
                task_ids = ", ".join(str(item["task"].get("task_id")) for item in pending.values())
                for item in pending.values():
                    get_logger().error(
                        "%s video task %s timed out after %d polls; queue=%s",
                        item["shot"].shot_id,
                        item["task"].get("task_id"),
                        max_polls,
                        self._project_relative(project_dir, task_file),
                        extra={"episode_key": episode.episode_key, "shot_id": item["shot"].shot_id},
                    )
                raise ProviderError(
                    f"Video task(s) {task_ids} did not finish after {max_polls} polls; "
                    f"saved in {task_file}. Rerun shot_video_generation to resume."
                )

            if deferred_shots and not made_progress:
                message = self._shot_video_inheritance_blocked_message(project_dir, episode, deferred_shots[0])
                get_logger().error("%s", message)
                raise ProviderError(message)

            remaining_shots = deferred_shots

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
        for shot in self._active_shots_for_episode(episode):
            shot.solidified_asset_ids = []
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
                        },
                    )
                )
            if shot.ref_frame_asset_id:
                ref_frame_generation_prompt = self._shot_ref_frame_prompt(state, episode, shot)
                shot.solidified_asset_ids.append(shot.ref_frame_asset_id)
                solidified.append(
                    DynamicAssetSolidificationItem(
                        asset_id=shot.ref_frame_asset_id,
                        asset_type="ref_frame",
                        episode_key=episode.episode_key,
                        shot_id=shot.shot_id,
                        asset_path=shot.ref_frame_asset_path,
                        source_node="ref_frame_generation",
                        metadata={
                            "prompt": ref_frame_generation_prompt,
                            "source_prompt": shot.ref_frame_prompt,
                            "generation_prompt": ref_frame_generation_prompt,
                            "provider": shot.ref_frame_provider,
                            "model": shot.ref_frame_model,
                        },
                    )
                )
            if shot.video_asset_id:
                video_generation_prompt = self._shot_video_prompt(state, episode, shot)
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
                            "source_prompt": shot.video_prompt,
                            "generation_prompt": video_generation_prompt,
                            "provider": shot.video_provider,
                            "model": shot.video_model,
                            "task_id": shot.video_task_id,
                            "task_status": shot.video_task_status,
                            "duration_seconds": shot.duration_seconds,
                        },
                    )
                )
        self._save_storyboard_episode(project_dir, episode)
        return DynamicAssetSolidificationOutput(solidified_assets=solidified)

    async def _run_dynamic_asset_solidification(self, project_dir: Path, state: ProjectState) -> ProjectState:
        solidified: list[DynamicAssetSolidificationItem] = []
        for episode_key in self._active_episode_keys_in_order(state):
            output = await self._run_dynamic_asset_solidification_for_episode(project_dir, state, episode_key)
            solidified.extend(output.solidified_assets)

        active_episode_keys = set(self._active_episode_keys_in_order(state))
        existing_dynamic_assets = [
            item
            for item in state.metadata.get("dynamic_assets", [])
            if str(item.get("episode_key")) not in active_episode_keys
        ]
        state.metadata["dynamic_assets"] = [
            *existing_dynamic_assets,
            *[item.model_dump(mode="json") for item in solidified],
        ]
        self.repo.save_node_output(
            project_dir,
            "dynamic_asset_solidification",
            DynamicAssetSolidificationOutput(solidified_assets=solidified),
        )
        return state
