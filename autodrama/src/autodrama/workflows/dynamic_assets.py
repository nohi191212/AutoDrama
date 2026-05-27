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
    RefFrameGenerationItem,
    RefFrameGenerationOutput,
    RefFrameSpatialPlan,
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

        max_shots = getattr(getattr(self, "_run_context", None), "max_shots", None)
        if max_shots is None:
            max_shots = self.settings.generation.max_shots
        output = await self.storyboard_service.storyboard_episode(
            state,
            provider,
            episode_key=episode_key,
            novel_extract_all=self._episode_stories(project_dir, state),
            current_novel_full=self._novel_full_contents(project_dir, state, [episode_key]).get(episode_key, ""),
            on_shot_generated=save_storyboard_progress,
            max_shots=max_shots,
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

    @staticmethod
    def _spatial_index_path(project_dir: Path) -> Path:
        return project_dir / "assets" / "json" / "spatial_ref_frame_index.json"

    def _load_spatial_ref_frame_index(self, project_dir: Path) -> dict[str, Any]:
        path = self._spatial_index_path(project_dir)
        if not path.exists():
            return {"schema_version": 1, "spaces": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            get_logger().warning("invalid spatial ref frame index ignored: %s", path)
            return {"schema_version": 1, "spaces": {}}
        if not isinstance(payload, dict):
            return {"schema_version": 1, "spaces": {}}
        payload.setdefault("schema_version", 1)
        payload.setdefault("spaces", {})
        return payload

    def _write_spatial_ref_frame_index(self, project_dir: Path, index: dict[str, Any]) -> None:
        index["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.repo.write_json(self._spatial_index_path(project_dir), index)

    @staticmethod
    def _shot_spatial_brief(shot: StoryboardShot | None, *, include_asset: bool = False) -> dict[str, Any] | None:
        if shot is None:
            return None
        payload: dict[str, Any] = {
            "shot_id": shot.shot_id,
            "index": shot.index,
            "layout_id": shot.layout_id,
            "title": shot.title,
            "ref_frame_prompt": shot.ref_frame_prompt,
            "video_prompt": shot.video_prompt,
            "dialogue": shot.dialogue,
            "role_ids": shot.role_ids,
            "prop_ids": shot.prop_ids,
            "physical_space_key": shot.physical_space_key,
            "physical_space_note": shot.physical_space_note,
            "spatial_structure_summary": shot.spatial_structure_summary,
            "spatial_constraints": shot.spatial_constraints,
        }
        if shot.source_coverage:
            payload["source_coverage"] = shot.source_coverage.model_dump(mode="json")
        if include_asset:
            payload["ref_frame_asset_id"] = shot.ref_frame_asset_id
            payload["ref_frame_asset_path"] = shot.ref_frame_asset_path
            payload["ref_frame_asset_url"] = shot.ref_frame_asset_url
        return {key: value for key, value in payload.items() if value not in (None, "", [])}

    def _spatial_ref_frame_candidates(
        self,
        project_dir: Path,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        *,
        max_candidates: int = 30,
    ) -> list[dict[str, Any]]:
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()

        def add_candidate(item: dict[str, Any]) -> None:
            episode_key = str(item.get("episode_key") or episode.episode_key)
            shot_id = str(item.get("shot_id") or "")
            asset_path = str(item.get("ref_frame_asset_path") or "")
            asset_url = str(item.get("ref_frame_asset_url") or "")
            if not shot_id or not asset_path:
                return
            if episode_key == episode.episode_key:
                try:
                    if int(item.get("shot_index") or 0) >= int(shot.index):
                        return
                except (TypeError, ValueError):
                    pass
            path = Path(asset_path)
            if not path.is_absolute():
                path = project_dir / path
            if not path.exists() or not path.is_file():
                return
            key = (episode_key, shot_id)
            if key in seen:
                return
            seen.add(key)
            candidates.append(
                {
                    "episode_key": episode_key,
                    "shot_id": shot_id,
                    "shot_index": item.get("shot_index"),
                    "layout_id": item.get("layout_id"),
                    "physical_space_key": item.get("physical_space_key"),
                    "physical_space_note": item.get("physical_space_note"),
                    "spatial_structure_summary": item.get("spatial_structure_summary"),
                    "ref_frame_asset_path": asset_path,
                    "ref_frame_asset_url": asset_url or None,
                }
            )

        for candidate_shot in sorted(episode.shots, key=lambda value: int(value.index)):
            if int(candidate_shot.index) >= int(shot.index):
                continue
            if not candidate_shot.ref_frame_asset_path:
                continue
            add_candidate(
                {
                    "episode_key": episode.episode_key,
                    "shot_id": candidate_shot.shot_id,
                    "shot_index": candidate_shot.index,
                    "layout_id": candidate_shot.layout_id,
                    "physical_space_key": candidate_shot.physical_space_key,
                    "physical_space_note": candidate_shot.physical_space_note,
                    "spatial_structure_summary": candidate_shot.spatial_structure_summary,
                    "ref_frame_asset_path": candidate_shot.ref_frame_asset_path,
                    "ref_frame_asset_url": candidate_shot.ref_frame_asset_url,
                }
            )

        index = self._load_spatial_ref_frame_index(project_dir)
        spaces = index.get("spaces", {})
        if isinstance(spaces, dict):
            for space_key, space_item in spaces.items():
                if not isinstance(space_item, dict):
                    continue
                for raw_item in space_item.get("shots", []):
                    if not isinstance(raw_item, dict):
                        continue
                    item = dict(raw_item)
                    item.setdefault("physical_space_key", space_key)
                    item.setdefault("physical_space_note", space_item.get("physical_space_note"))
                    add_candidate(item)

        return candidates[:max_candidates]

    def _ref_frame_spatial_text_provider(self):
        try:
            provider = self.router.text("ref_frame_spatial")
            if getattr(provider, "name", None) == "deepseek":
                settings = getattr(provider, "settings", None)
                models = getattr(settings, "models", {}) if settings is not None else {}
                options = getattr(settings, "options", {}) if settings is not None else {}
                if "ref_frame_spatial" not in models:
                    provider.model = "deepseek-v4-flash"
                if "ref_frame_spatial_reasoning_effort" not in options:
                    provider.reasoning_effort = "low"
                if "ref_frame_spatial_thinking_enabled" not in options:
                    provider.thinking_enabled = False
            return provider
        except KeyError:
            pass

        if "deepseek" in getattr(self.settings, "providers", {}):
            from autodrama.providers.deepseek.text.deepseek import DeepSeekTextProvider

            provider_settings = self.settings.providers["deepseek"].model_copy(deep=True)
            provider_settings.models = dict(provider_settings.models)
            provider_settings.options = dict(provider_settings.options)
            provider_settings.models.setdefault("ref_frame_spatial", "deepseek-v4-flash")
            provider_settings.options.setdefault("ref_frame_spatial_reasoning_effort", "low")
            provider_settings.options.setdefault("ref_frame_spatial_thinking_enabled", False)
            return DeepSeekTextProvider(provider_settings, self.settings.runtime, model_key="ref_frame_spatial")
        return self.router.text("storyboard")

    async def _plan_ref_frame_spatial_continuity(
        self,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
    ) -> RefFrameSpatialPlan:
        provider = self._ref_frame_spatial_text_provider()
        previous_shot = self._previous_shot(episode, shot)
        candidates = self._spatial_ref_frame_candidates(project_dir, episode, shot)
        prompt = self.prompts.render(
            "ref_frame_spatial_plan",
            title=state.title,
            episode_key=episode.episode_key,
            current_shot=json.dumps(self._shot_spatial_brief(shot), ensure_ascii=False, indent=2),
            previous_shot=json.dumps(
                self._shot_spatial_brief(previous_shot, include_asset=True),
                ensure_ascii=False,
                indent=2,
            ),
            spatial_candidates=json.dumps(candidates, ensure_ascii=False, indent=2),
        )
        plan = await provider.generate_json(
            prompt,
            RefFrameSpatialPlan,
            temperature=0.1,
            metadata={
                "node_name": "ref_frame_spatial_planning",
                "project_id": state.project_id,
                "episode_key": episode.episode_key,
                "shot_id": shot.shot_id,
                "previous_shot_id": previous_shot.shot_id if previous_shot else None,
                "candidate_shot_ids": [item.get("shot_id") for item in candidates],
            },
        )
        state.budget.used_text_calls += 1
        return self._normalize_ref_frame_spatial_plan(state, episode, shot, plan, candidates=candidates)

    def _normalize_ref_frame_spatial_plan(
        self,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        plan: RefFrameSpatialPlan,
        *,
        candidates: list[dict[str, Any]] | None = None,
    ) -> RefFrameSpatialPlan:
        layout = state.layouts.get(shot.layout_id)
        layout_name = layout.name if layout else shot.layout_id
        physical_space_note = str(plan.physical_space_note or "").strip() or str(layout_name)
        physical_space_key = str(plan.physical_space_key or "").strip() or f"{shot.layout_id}::{physical_space_note}"
        previous_shot = self._previous_shot(episode, shot)
        same_as_previous = bool(plan.same_physical_space_as_previous and previous_shot is not None)
        reference_ids: list[str] = []
        available_ids = {str(item.get("shot_id")) for item in candidates or [] if item.get("shot_id")}
        for shot_id in plan.reference_shot_ids:
            value = str(shot_id).strip()
            if value and value in available_ids and value not in reference_ids:
                reference_ids.append(value)
            if len(reference_ids) >= 10:
                break
        if not same_as_previous and plan.continuity_mode != "reuse_prior_space":
            reference_ids = []
        if same_as_previous:
            prepend_ids: list[str] = []
            if previous_shot and previous_shot.shot_id in available_ids:
                prepend_ids.append(previous_shot.shot_id)
                previous_previous_shot = self._previous_shot(episode, previous_shot)
                if (
                    previous_previous_shot
                    and previous_previous_shot.shot_id in available_ids
                    and previous_previous_shot.physical_space_key
                    and (
                        previous_previous_shot.physical_space_key == physical_space_key
                        or previous_previous_shot.physical_space_key == previous_shot.physical_space_key
                    )
                ):
                    prepend_ids.append(previous_previous_shot.shot_id)
            for shot_id in reversed(prepend_ids):
                if shot_id in reference_ids:
                    reference_ids.remove(shot_id)
                reference_ids.insert(0, shot_id)
            reference_ids = reference_ids[:10]
        summary = str(plan.spatial_structure_summary or "").strip() or (
            f"{physical_space_note}内的人物、关键物体和背景人群保持清晰稳定的相对空间关系。"
        )
        constraints = [str(item).strip() for item in plan.spatial_constraints if str(item).strip()]
        if not constraints:
            constraints = [
                "人物与关键物体的左右、前后、远近关系不能无故反转。",
                "背景人群保持大致一致的站位区域、密度、朝向和围观/队列结构。",
            ]
        if same_as_previous:
            continuity_mode = "previous_shot"
        elif reference_ids:
            continuity_mode = "reuse_prior_space"
        else:
            continuity_mode = "new_space"
        return RefFrameSpatialPlan(
            same_physical_space_as_previous=same_as_previous,
            confidence=float(plan.confidence),
            continuity_mode=continuity_mode,
            physical_space_key=physical_space_key,
            physical_space_note=physical_space_note,
            reference_shot_ids=reference_ids,
            spatial_structure_summary=summary,
            spatial_constraints=constraints,
            movement_allowed=bool(plan.movement_allowed),
            movement_reason=plan.movement_reason,
        )

    @staticmethod
    def _apply_ref_frame_spatial_plan(shot: StoryboardShot, plan: RefFrameSpatialPlan) -> None:
        shot.physical_space_key = plan.physical_space_key
        shot.physical_space_note = plan.physical_space_note
        shot.spatial_continuity_mode = plan.continuity_mode
        shot.spatial_reference_shot_ids = list(plan.reference_shot_ids[:10])
        shot.spatial_structure_summary = plan.spatial_structure_summary
        shot.spatial_constraints = list(plan.spatial_constraints)
        shot.spatial_movement_allowed = plan.movement_allowed
        shot.spatial_movement_reason = plan.movement_reason
        shot.spatial_plan_confidence = plan.confidence

    def _spatial_ref_frame_refs(
        self,
        project_dir: Path,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
        plan: RefFrameSpatialPlan,
    ) -> list:
        from autodrama.providers.base import AssetRef

        refs: list[AssetRef] = []
        shots_by_id = {item.shot_id: item for item in episode.shots}
        candidates = {
            str(item.get("shot_id")): item
            for item in self._spatial_ref_frame_candidates(project_dir, episode, shot, max_candidates=100)
            if item.get("shot_id")
        }
        for index, shot_id in enumerate(plan.reference_shot_ids[:10], start=1):
            candidate_shot = shots_by_id.get(shot_id)
            asset_path = candidate_shot.ref_frame_asset_path if candidate_shot else None
            asset_url = candidate_shot.ref_frame_asset_url if candidate_shot else None
            item = candidates.get(shot_id, {})
            if asset_path is None:
                asset_path = item.get("ref_frame_asset_path")
            if asset_url is None:
                asset_url = item.get("ref_frame_asset_url")
            if not asset_path:
                continue
            path = Path(str(asset_path))
            if not path.is_absolute():
                path = project_dir / path
            if not path.exists() or not path.is_file():
                continue
            refs.append(
                AssetRef(
                    id=f"{shot_id}_spatial_ref",
                    type="image",
                    path=str(path),
                    url=str(asset_url) if asset_url else None,
                    metadata={
                        "asset_type": "continuity_ref_frame",
                        "reference_priority": index,
                        "reference_for": shot.shot_id,
                        "source_shot_id": shot_id,
                        "physical_space_key": plan.physical_space_key,
                        "physical_space_note": plan.physical_space_note,
                    },
                )
            )
        return refs

    def _record_spatial_ref_frame(
        self,
        project_dir: Path,
        state: ProjectState,
        episode: StoryboardEpisodeOutput,
        shot: StoryboardShot,
    ) -> None:
        if not shot.physical_space_key or not shot.ref_frame_asset_path:
            return
        index = self._load_spatial_ref_frame_index(project_dir)
        index["schema_version"] = 1
        index["project_id"] = state.project_id
        spaces = index.setdefault("spaces", {})
        if not isinstance(spaces, dict):
            spaces = {}
            index["spaces"] = spaces

        for space_item in spaces.values():
            if not isinstance(space_item, dict):
                continue
            existing_shots = space_item.get("shots", [])
            if isinstance(existing_shots, list):
                space_item["shots"] = [
                    item
                    for item in existing_shots
                    if not (
                        isinstance(item, dict)
                        and item.get("episode_key") == episode.episode_key
                        and item.get("shot_id") == shot.shot_id
                    )
                ]

        space = spaces.setdefault(
            shot.physical_space_key,
            {
                "physical_space_key": shot.physical_space_key,
                "physical_space_note": shot.physical_space_note,
                "shots": [],
            },
        )
        space["physical_space_note"] = shot.physical_space_note
        shots = space.setdefault("shots", [])
        if not isinstance(shots, list):
            shots = []
            space["shots"] = shots
        shots.append(
            {
                "episode_key": episode.episode_key,
                "shot_id": shot.shot_id,
                "shot_index": shot.index,
                "layout_id": shot.layout_id,
                "ref_frame_asset_id": shot.ref_frame_asset_id,
                "ref_frame_asset_path": shot.ref_frame_asset_path,
                "ref_frame_asset_url": shot.ref_frame_asset_url,
                "physical_space_key": shot.physical_space_key,
                "physical_space_note": shot.physical_space_note,
                "spatial_structure_summary": shot.spatial_structure_summary,
                "spatial_constraints": shot.spatial_constraints,
            }
        )
        shots.sort(key=lambda item: (str(item.get("episode_key") or ""), int(item.get("shot_index") or 0)))
        self._write_spatial_ref_frame_index(project_dir, index)

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
                spatial_plan = await self._plan_ref_frame_spatial_continuity(project_dir, state, episode, shot)
                self._apply_ref_frame_spatial_plan(shot, spatial_plan)
                prompt = self._shot_ref_frame_prompt(state, episode, shot)
                refs = None
                if getattr(provider, "supports_reference_images", False):
                    refs = [
                        *self._spatial_ref_frame_refs(project_dir, episode, shot, spatial_plan),
                        *self._shot_ref_asset_refs(project_dir, state, shot),
                    ]
                result = await provider.generate_image(
                    prompt,
                    refs=refs,
                    metadata={
                        "node_name": "ref_frame_generation",
                        "project_id": state.project_id,
                        "episode_key": episode.episode_key,
                        "shot_id": shot.shot_id,
                        "asset_id": asset_id,
                        "physical_space_key": shot.physical_space_key,
                        "physical_space_note": shot.physical_space_note,
                        "spatial_continuity_mode": shot.spatial_continuity_mode,
                        "spatial_reference_shot_ids": shot.spatial_reference_shot_ids,
                    },
                )
                asset_path = await self._write_first_generated_image(
                    project_dir,
                    self._image_asset_path(project_dir, "ref_frames", asset_id),
                    result,
                )
                asset_url = result.image_urls[0] if result.image_urls else None
                shot.ref_frame_asset_id = asset_id
                shot.ref_frame_asset_path = asset_path
                shot.ref_frame_asset_url = asset_url
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
                        asset_url=asset_url,
                        provider=result.provider,
                        model=result.model,
                        request_id=result.request_id,
                        usage=result.usage,
                        raw_response=result.raw_response,
                        physical_space_key=shot.physical_space_key,
                        physical_space_note=shot.physical_space_note,
                        spatial_continuity_mode=shot.spatial_continuity_mode,
                        spatial_reference_shot_ids=shot.spatial_reference_shot_ids,
                        spatial_structure_summary=shot.spatial_structure_summary,
                        spatial_constraints=shot.spatial_constraints,
                    )
                )
                self._save_storyboard_episode(project_dir, episode)
                self._record_spatial_ref_frame(project_dir, state, episode, shot)
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
        force_generation = bool(getattr(self, "_force_generation", False))
        for shot in shots:
            asset_id = normalize_id(f"{shot.shot_id}", "video")
            prompt = self._shot_video_prompt(state, episode, shot, provider=provider)
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

            if not force_generation and existing_status in success_statuses and self._path_exists(project_dir, saved_asset_path):
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
                continue

            task: dict[str, Any]
            last_logged_status: str | None
            if not force_generation and existing_task and existing_task.get("task_id") and existing_status not in failure_statuses:
                task = existing_task
                last_logged_status = existing_status
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
                    raise ProviderError(message)

                if existing_task and existing_status in failure_statuses:
                    get_logger().info(
                        "%s previous video task %s ended with status=%s; submitting a new task",
                        shot.shot_id,
                        existing_task.get("task_id"),
                        existing_status,
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
                raise ProviderError(f"Missing video task_id for {shot.shot_id}; queue={task_file}")

            completed = False
            for poll_index in range(1, max_polls + 1):
                result = await provider.query_video_task(task_id)
                status = (result.task_status or "").strip().lower()
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
                        raise ProviderError(f"Video task {task_id} succeeded but returned no video URL or data")
                    last_frame_asset_path = await self._write_video_last_frame(
                        project_dir,
                        asset_id,
                        result,
                    )
                    self._apply_shot_video_result(
                        shot,
                        asset_id=asset_id,
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
                    task = upsert_generation_task(registry, completed_task_item)
                    save_generation_tasks(self.repo, project_dir, registry)
                    generated.append(
                        self._shot_video_item(
                            episode_key=episode.episode_key,
                            shot=shot,
                            asset_id=asset_id,
                            prompt=prompt,
                            duration_seconds=shot.duration_seconds,
                            asset_path=asset_path,
                            result=result,
                            provider=provider,
                        )
                    )
                    get_logger().info(
                        "%s generated successfully, saved in %s",
                        shot.shot_id,
                        asset_path,
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                    completed = True
                    break

                if status in failure_statuses:
                    task = upsert_generation_task(
                        registry,
                        {
                            "task_key": task_key,
                            "task_status": result.task_status,
                            "failed_at": now_iso(),
                        },
                    )
                    save_generation_tasks(self.repo, project_dir, registry)
                    get_logger().error(
                        "%s video task %s failed with status=%s queue=%s",
                        shot.shot_id,
                        task_id,
                        result.task_status,
                        self._project_relative(project_dir, task_file),
                        extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                    )
                    raise ProviderError(f"Video task {task_id} ended with status {result.task_status}; queue={task_file}")

                if poll_index < max_polls:
                    await asyncio.sleep(poll_interval_seconds)

            if not completed:
                last_status = task_status(task) or "timeout"
                task = upsert_generation_task(
                    registry,
                    {
                        "task_key": task_key,
                        "task_status": "timeout",
                        "previous_task_status": last_status,
                        "timed_out_at": now_iso(),
                    },
                )
                save_generation_tasks(self.repo, project_dir, registry)
                get_logger().error(
                    "%s video task %s timed out after %d polls; queue=%s",
                    shot.shot_id,
                    task.get("task_id"),
                    max_polls,
                    self._project_relative(project_dir, task_file),
                    extra={"episode_key": episode.episode_key, "shot_id": shot.shot_id},
                )
                raise ProviderError(
                    f"Video task {task.get('task_id')} did not finish after {max_polls} polls; "
                    f"saved in {task_file}. Rerun shot_video_generation to resume."
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
        video_provider = self.router.video("shot")
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
                            "asset_url": shot.ref_frame_asset_url,
                            "physical_space_key": shot.physical_space_key,
                            "physical_space_note": shot.physical_space_note,
                            "spatial_continuity_mode": shot.spatial_continuity_mode,
                            "spatial_reference_shot_ids": shot.spatial_reference_shot_ids,
                            "spatial_structure_summary": shot.spatial_structure_summary,
                            "spatial_constraints": shot.spatial_constraints,
                        },
                    )
                )
            if shot.video_asset_id:
                video_generation_prompt = self._shot_video_prompt(state, episode, shot, provider=video_provider)
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
