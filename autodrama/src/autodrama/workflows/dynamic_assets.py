from __future__ import annotations

from pathlib import Path
from typing import Any

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
    StoryboardGenerationOutput,
)
from autodrama.logging import get_logger
from autodrama.workflows.storyboard_history import history_before_episode


class DynamicAssetNodeMixin:
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
        slots_dir = project_dir / "slots"
        slots_dir.mkdir(parents=True, exist_ok=True)
        output = await self.storyboard_service.storyboard_episode(
            state,
            provider,
            episode_key=episode_key,
            previous_storyboard_history=history_before_episode(project_dir, state, episode_key),
        )
        if output.episode_key != episode_key:
            raise ValueError(f"Storyboard episode_key must be {episode_key}; got {output.episode_key}")
        self.repo.write_json(slots_dir / f"{episode_key}.json", output)
        state.budget.used_text_calls += 1
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
        for shot in episode.shots:
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
        for shot in episode.shots:
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
        for shot in episode.shots:
            asset_id = normalize_id(f"{shot.shot_id}", "video")
            prompt = self._shot_video_prompt(state, episode, shot)
            result = await provider.generate_video(
                prompt,
                refs=self._shot_video_refs(project_dir, state, shot),
                duration=shot.duration_seconds,
                wait=True,
                metadata={
                    "node_name": "shot_video_generation",
                    "project_id": state.project_id,
                    "episode_key": episode.episode_key,
                    "shot_id": shot.shot_id,
                    "asset_id": asset_id,
                },
            )
            asset_path = await self._write_generated_video(
                project_dir,
                self._video_asset_path(project_dir, "shots", asset_id),
                result,
            )
            shot.video_asset_id = asset_id
            shot.video_asset_path = asset_path
            shot.video_provider = result.provider
            shot.video_model = result.model
            shot.video_task_id = result.task_id
            shot.video_task_status = result.task_status
            shot.video_request_id = result.request_id
            shot.video_usage = result.usage
            shot.video_raw_response = result.raw_response
            generated.append(
                ShotVideoGenerationItem(
                    episode_key=episode.episode_key,
                    shot_id=shot.shot_id,
                    asset_id=asset_id,
                    prompt=prompt,
                    duration_seconds=shot.duration_seconds,
                    asset_path=asset_path,
                    provider=result.provider,
                    model=result.model,
                    task_id=result.task_id,
                    task_status=result.task_status,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
            )
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
        for shot in episode.shots:
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
                            "prompt": shot.ref_frame_prompt,
                            "provider": shot.ref_frame_provider,
                            "model": shot.ref_frame_model,
                        },
                    )
                )
            if shot.video_asset_id:
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
                            "prompt": shot.video_prompt,
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
