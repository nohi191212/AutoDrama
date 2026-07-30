"""Shot-first pre-generation nodes with reusable background plates.

The chain is intentionally strict: plan shots, plan layout backgrounds,
generate backgrounds, compose keyframe prompts from real references, then
generate keyframes.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    ClipSegmentOutput,
    ClipShotPlan,
    ClipToShotsEpisodeOutput,
    ClipToShotsModelOutput,
    ClipToShotsOutput,
    LayoutBackgroundPromptModelOutput,
    LayoutToBackgroundPromptEpisodeOutput,
    LayoutToBackgroundPromptOutput,
    ProjectState,
    StaticAssetGenerationOutput,
    ShotBackgroundImageGenerationEpisodeOutput,
    ShotBackgroundImageGenerationItem,
    ShotBackgroundImageGenerationOutput,
    ShotBackgroundPromptItem,
    ShotKeyframeImageGenerationItem,
    ShotKeyframeImageGenerationOutput,
    ShotKeyframePromptItem,
    ShotKeyframePromptModelOutput,
    ShotKeyframePromptOutput,
    ShotManifestGenerationEpisodeItem,
    ShotManifestGenerationOutput,
    ShotPlanItem,
    ShotManifestEpisodeOutput,
    ShotManifestItem,
    ShotVideoInput,
    GateResult,
    ImageAssetAuditOutput,
    VisualStyleSpec,
)
from autodrama.core.visual_contract import (
    assert_duration_gate,
    identity_brief,
    normalize_shot_durations,
    render_visual_style_brief,
)
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef
from autodrama.workflows.nodes.static_asset_nodes import StaticAssetNodeBase
from autodrama.workflows.output_scope import configured_episode_output_selection
from autodrama.workflows.runner import WorkflowNode
from autodrama.workflows.selection import clip_matches_selectors, shot_matches_selectors


SHOT_ASSET_NODE_NAMES = [
    "clip_to_shots",
    "layout_to_background_prompt",
    "shot_background_image_generation",
    "shot_keyframe_prompt",
    "shot_keyframe_image_generation",
    "shot_manifest_generation",
]


class ShotAssetNodeBase(StaticAssetNodeBase):
    _LOCAL_SHOT_KEY = re.compile(r"^shot_(\d+)$")
    _LOCAL_BACKGROUND_KEY = re.compile(r"^background_(\d+)$")
    _FORBIDDEN = ("p01", "p12", "://", "\\\\", "/assets/", "model=")

    def active_episode_keys(self, state: ProjectState) -> list[str]:
        return super().active_episode_keys(state) or self.expected_episode_keys(state)

    @staticmethod
    def _clean(value: object) -> str:
        return " ".join(str(value or "").split()).strip()

    @staticmethod
    def _force(workflow: Any) -> bool:
        return bool(getattr(workflow, "_force_pregen", False))

    def _load_episode_output(self, project_dir: Path, node_name: str, episode_key: str, schema: Any):
        path = self.layout.node_episode_output_path(project_dir, node_name, episode_key)
        if not path.exists():
            raise FileNotFoundError(f"{node_name} output is missing for {episode_key}: {path}")
        return schema.model_validate_json(path.read_text(encoding="utf-8"))

    def _active_clip_selectors(self) -> set[str]:
        return set(getattr(self.workflow, "_active_clip_selectors", set()) or set())

    def _active_shot_selectors(self) -> set[str]:
        return set(getattr(self.workflow, "_active_shot_selectors", set()) or set())

    def _selected_shots(
        self,
        project_dir: Path,
        plan: ClipToShotsEpisodeOutput,
    ) -> list[ShotPlanItem]:
        episode_key = plan.episode_key
        shots = [shot for clip in plan.clips for shot in clip.shots]
        selectors = self._active_shot_selectors()
        if not selectors:
            selection = configured_episode_output_selection(
                self.repo,
                project_dir,
                plan,
            )
            selected_ids = set(selection.selected_shot_ids)
            return [shot for shot in shots if shot.shot_id in selected_ids]
        shadow = ShotManifestEpisodeOutput(
            episode_key=episode_key,
            shots=[
                ShotManifestItem(
                    shot_id=item.shot_id,
                    index=item.episode_shot_index,
                    title=item.shot_id,
                    duration_seconds=float(item.duration_seconds),
                    video_prompt=item.video_prompt,
                )
                for item in shots
            ],
        )
        selected = [
            item
            for item, candidate in zip(shots, shadow.shots)
            if shot_matches_selectors(shadow, candidate, selectors)
        ]
        if not selected:
            raise ValueError(f"No shot matched --shots for {episode_key}")
        return selected

    @staticmethod
    def _role_appearance_ref(project_dir: Path, role: Any, appearance: Any) -> AssetRef:
        path = appearance.asset_path or appearance.design_image_asset_path
        return AssetRef(
            id=appearance.asset_id or appearance.design_image_asset_id or f"{appearance.id}_roleboard",
            type="image",
            path=str(project_dir / path) if path else None,
            url=appearance.asset_url or appearance.design_image_asset_url,
            metadata={"asset_type": "roleboard", "role_id": role.id, "appearance_id": appearance.id},
        )

    def _hydrate_layout_asset_refs_from_generation_output(
        self,
        project_dir: Path,
        state: ProjectState,
    ) -> None:
        """Recover layout references after independently-run nodes save stale state."""
        missing = [layout for layout in state.layouts.values() if not (layout.asset_path or layout.asset_url)]
        if not missing:
            return
        path = self.layout.node_output_path(project_dir, "layout_image_generation")
        if not path.exists():
            return
        try:
            output = StaticAssetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning("could not hydrate layout references from %s: %s", path, exc)
            return
        by_asset_id = {item.asset_id: item for item in output.generated_assets}
        restored = 0
        for layout in missing:
            item = by_asset_id.get(layout.id) or by_asset_id.get(layout.asset_id or "")
            if item is not None and (item.asset_path or item.asset_url):
                layout.asset_id = item.asset_id
                layout.asset_path = item.asset_path
                layout.asset_url = item.asset_url
                layout.provider = item.provider
                layout.model = item.model
                layout.request_id = item.request_id
                layout.usage = item.usage
            else:
                image_path = self.layout.image_asset_path(project_dir, "layouts", layout.id)
                if not image_path.is_file():
                    continue
                layout.asset_id = layout.id
                layout.asset_path = self.layout.project_relative(project_dir, image_path)
            restored += 1
        if restored:
            self.logger.info(
                "hydrated %d missing layout reference(s) from layout_image_generation output",
                restored,
            )

    def _asset_index(
        self,
        project_dir: Path,
        state: ProjectState,
        *,
        clip: Any,
    ) -> tuple[str, dict[str, dict[str, Any]]]:
        self._hydrate_layout_asset_refs_from_generation_output(project_dir, state)
        wanted_roles = {self._clean(name) for name in getattr(clip, "role_names", [])}
        wanted_props = {self._clean(name) for name in getattr(clip, "prop_names", [])}
        wanted_layouts = {self._clean(name) for name in getattr(clip, "layout_names", [])}
        assets: dict[str, dict[str, Any]] = {}
        has_layout_match = any(
            layout.name in wanted_layouts or layout.group in wanted_layouts
            for layout in state.layouts.values()
        )
        for layout in state.layouts.values():
            if wanted_layouts and has_layout_match and layout.name not in wanted_layouts and layout.group not in wanted_layouts:
                continue
            assets[layout.id] = {"kind": "layout", "label": layout.desc or layout.name, "object": layout}
        for role in state.roles.values():
            if wanted_roles and role.name not in wanted_roles and not wanted_roles.intersection(set(role.aliases)):
                continue
            for appearance in role.appearances.values():
                asset_id = appearance.asset_id or appearance.design_image_asset_id or f"{appearance.id}_roleboard"
                assets[asset_id] = {
                    "kind": "roleboard",
                    "label": identity_brief(appearance),
                    "object": (role, appearance),
                }
        for prop in state.props.values():
            if wanted_props and prop.name not in wanted_props and not wanted_props.intersection(set(prop.aliases)):
                continue
            asset = prop.base_asset
            if asset is not None:
                assets[asset.asset_id or asset.id] = {
                    "kind": "prop",
                    "label": asset.desc or prop.intro or prop.name,
                    "object": (prop, asset),
                }
        index_rows: list[str] = []
        for asset_id, item in assets.items():
            metadata = ""
            if item["kind"] == "roleboard":
                role, appearance = item["object"]
                metadata = f" [role_id={role.id}; appearance_id={appearance.id}]"
            elif item["kind"] == "prop":
                prop, _asset = item["object"]
                metadata = f" [prop_id={prop.id}]"
            index_rows.append(f"{asset_id}: {item['label']}{metadata}")
        return "\n".join(index_rows) or "(none)", assets

    def _all_assets(self, project_dir: Path, state: ProjectState) -> dict[str, dict[str, Any]]:
        return self._asset_index(project_dir, state, clip=type("AllAssets", (), {})())[1]

    def _asset_ref(self, project_dir: Path, item: dict[str, Any]) -> AssetRef:
        kind = item["kind"]
        obj = item["object"]
        if kind == "layout":
            return AssetRef(
                id=obj.asset_id or obj.id,
                type="image",
                path=str(project_dir / obj.asset_path) if obj.asset_path else None,
                url=obj.asset_url,
                metadata={"asset_type": kind, "layout_id": obj.id},
            )
        if kind == "roleboard":
            return self._role_appearance_ref(project_dir, obj[0], obj[1])
        prop, asset = obj
        return AssetRef(
            id=asset.asset_id or asset.id,
            type="image",
            path=str(project_dir / asset.asset_path) if asset.asset_path else None,
            url=asset.asset_url,
            metadata={"asset_type": kind, "prop_id": prop.id},
        )

    def _shot_layout(self, shot: ShotPlanItem, assets: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        layouts = [(ref_id, assets[ref_id]) for ref_id in shot.ref_ids if ref_id in assets and assets[ref_id]["kind"] == "layout"]
        if len(layouts) != 1:
            raise ValueError(f"{shot.shot_id} must reference exactly one layout")
        return layouts[0]

    def _background_asset_ref(self, project_dir: Path, background: ShotBackgroundImageGenerationItem) -> AssetRef:
        path = project_dir / background.asset_path
        if not path.is_file() and not background.asset_url:
            raise FileNotFoundError(f"Background image is missing for {background.background_id}: {background.asset_path}")
        return AssetRef(
            id=background.background_id,
            type="image",
            path=str(path) if path.is_file() else None,
            url=background.asset_url,
            metadata={"asset_type": "shot_background", "background_id": background.background_id},
        )

    @staticmethod
    def _reference_version(ref: AssetRef) -> str:
        """Content-sensitive signature for a reference image used in a model call."""
        digest = hashlib.sha256()
        digest.update(str(ref.id or "").encode("utf-8"))
        path = Path(ref.path) if ref.path else None
        if path is not None and path.is_file():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        else:
            digest.update(str(ref.url or "").encode("utf-8"))
        return digest.hexdigest()

    def _generation_fingerprint(self, prompt: str, refs: list[AssetRef], provider: Any) -> str:
        binding = getattr(provider, "model_binding", None)
        params = getattr(binding, "params", {}) if binding is not None else {}
        payload = {
            "prompt": prompt,
            "references": [self._reference_version(ref) for ref in refs],
            "provider": str(getattr(provider, "name", "")),
            "model": str(getattr(provider, "model", "")),
            "params": params if isinstance(params, dict) else {},
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _invalidate_background_dependents(
        self,
        project_dir: Path,
        episode_key: str,
        shot_ids: set[str],
    ) -> None:
        """Drop stale downstream records when a background plate is regenerated."""
        if not shot_ids:
            return
        prompt_path = self.layout.node_episode_output_path(project_dir, "shot_keyframe_prompt", episode_key)
        if prompt_path.exists():
            output = ShotKeyframePromptOutput.model_validate_json(prompt_path.read_text(encoding="utf-8"))
            self.repo.write_json(
                prompt_path,
                ShotKeyframePromptOutput(prompts=[item for item in output.prompts if item.shot_id not in shot_ids]),
            )
        image_path = self.layout.node_episode_output_path(project_dir, "shot_keyframe_image_generation", episode_key)
        if image_path.exists():
            output = ShotKeyframeImageGenerationOutput.model_validate_json(image_path.read_text(encoding="utf-8"))
            self.repo.write_json(
                image_path,
                ShotKeyframeImageGenerationOutput(
                    generated_keyframes=[item for item in output.generated_keyframes if item.shot_id not in shot_ids]
                ),
            )
        manifest_path = self.layout.shot_path(project_dir, episode_key)
        if manifest_path.exists():
            try:
                output = ShotManifestEpisodeOutput.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.logger.warning(
                    "ignored incompatible existing shot manifest while invalidating backgrounds %s: %s",
                    manifest_path,
                    exc,
                )
                return
            output.shots = [item for item in output.shots if item.shot_id not in shot_ids]
            self.repo.write_json(manifest_path, output)

    @classmethod
    def _validate_model_output(cls, output: ClipToShotsModelOutput, assets: dict[str, dict[str, Any]]) -> list[Any]:
        if not output.root:
            raise ValueError("clip_to_shots returned no shots")
        ordered: list[tuple[int, Any]] = []
        for key, item in output.root.items():
            match = cls._LOCAL_SHOT_KEY.fullmatch(str(key))
            if not match:
                raise ValueError(f"clip_to_shots has invalid local shot key: {key}")
            ordered.append((int(match.group(1)), item))
        ordered.sort(key=lambda value: value[0])
        if [index for index, _ in ordered] != list(range(1, len(ordered) + 1)):
            raise ValueError("clip_to_shots local shot keys must be continuously numbered from 1")
        valid_role_ids = {
            role.id
            for asset in assets.values()
            if asset["kind"] == "roleboard"
            for role in [asset["object"][0]]
        }
        valid_appearance_ids = {
            appearance.id
            for asset in assets.values()
            if asset["kind"] == "roleboard"
            for appearance in [asset["object"][1]]
        }
        appearance_role_ids = {
            appearance.id: role.id
            for asset in assets.values()
            if asset["kind"] == "roleboard"
            for role, appearance in [asset["object"]]
        }
        valid_prop_ids = {
            prop.id
            for asset in assets.values()
            if asset["kind"] == "prop"
            for prop in [asset["object"][0]]
        }
        for _, item in ordered:
            item.shot_description = cls._clean(item.shot_description)
            item.narrative_angle = cls._clean(item.narrative_angle)
            item.opening_state = cls._clean(item.opening_state)
            item.video_prompt = cls._clean(item.video_prompt)
            if not all((item.shot_description, item.narrative_angle, item.opening_state, item.video_prompt)):
                raise ValueError("clip_to_shots requires shot_description, narrative_angle, opening_state and video_prompt")
            visible_text = "\n".join((item.shot_description, item.narrative_angle, item.opening_state, item.video_prompt)).lower()
            if any(token in visible_text for token in cls._FORBIDDEN):
                raise ValueError("clip_to_shots contains forbidden workflow/path terminology")
            unknown = [ref_id for ref_id in item.ref_ids if ref_id not in assets]
            if unknown:
                raise ValueError(f"clip_to_shots references unavailable assets: {', '.join(unknown)}")
            layouts = [ref_id for ref_id in item.ref_ids if assets[ref_id]["kind"] == "layout"]
            if len(layouts) != 1:
                raise ValueError("each shot must reference exactly one layout")
            referenced_role_ids = {
                assets[ref_id]["object"][0].id
                for ref_id in item.ref_ids
                if assets[ref_id]["kind"] == "roleboard"
            }
            if [line.line_index for line in item.dialogue_lines] != list(
                range(1, len(item.dialogue_lines) + 1)
            ):
                raise ValueError("dialogue line_index values must be continuously numbered from 1")
            for line in item.dialogue_lines:
                line.text = cls._clean(line.text)
                if not line.text:
                    raise ValueError("dialogue text must contain spoken content")
                if line.speaker_role_id is not None and line.speaker_role_id not in valid_role_ids:
                    raise ValueError(f"dialogue references unavailable role: {line.speaker_role_id}")
            unknown_allowed_props = sorted(set(item.allowed_props).difference(valid_prop_ids))
            if unknown_allowed_props:
                raise ValueError(
                    f"allowed_props references unavailable props: {', '.join(unknown_allowed_props)}"
                )
            entity_ids: set[str] = set()
            for entity_state in item.entity_states:
                if entity_state.entity_id not in valid_role_ids:
                    raise ValueError(f"entity state references unavailable role: {entity_state.entity_id}")
                if entity_state.entity_id not in referenced_role_ids:
                    raise ValueError(
                        f"entity state role must have a visual reference: {entity_state.entity_id}"
                    )
                if entity_state.entity_id in entity_ids:
                    raise ValueError(f"duplicate entity state for role: {entity_state.entity_id}")
                entity_ids.add(entity_state.entity_id)
                if (
                    entity_state.appearance_id is not None
                    and entity_state.appearance_id not in valid_appearance_ids
                ):
                    raise ValueError(
                        f"entity state references unavailable appearance: {entity_state.appearance_id}"
                    )
                if (
                    entity_state.appearance_id is not None
                    and appearance_role_ids[entity_state.appearance_id] != entity_state.entity_id
                ):
                    raise ValueError(
                        f"appearance {entity_state.appearance_id} does not belong to "
                        f"{entity_state.entity_id}"
                    )
                unknown_held_props = sorted(
                    set(entity_state.held_props).difference(item.allowed_props)
                )
                if unknown_held_props:
                    raise ValueError(
                        f"held_props must be declared in allowed_props: {', '.join(unknown_held_props)}"
                    )
            for line in item.dialogue_lines:
                if (
                    line.delivery_mode == "on_screen"
                    and line.speaker_role_id is not None
                    and line.speaker_role_id not in entity_ids
                ):
                    raise ValueError(
                        f"on-screen dialogue speaker must have an entity state: {line.speaker_role_id}"
                    )
            overlay = item.overlay_text_spec
            if overlay is not None:
                if overlay.start_seconds is not None and overlay.start_seconds > item.duration_seconds:
                    raise ValueError("overlay start_seconds exceeds shot duration")
                if overlay.end_seconds is not None and overlay.end_seconds > item.duration_seconds:
                    raise ValueError("overlay end_seconds exceeds shot duration")
        return [item for _, item in ordered]

    @staticmethod
    def _visual_quality(state: ProjectState) -> str:
        raw_spec = state.metadata.get("visual_style_spec")
        if raw_spec is None:
            raise ValueError("project metadata is missing required visual_style_spec")
        return render_visual_style_brief(VisualStyleSpec.model_validate(raw_spec))


class ClipToShotsNode(ShotAssetNodeBase):
    name = "clip_to_shots"

    def _reference_budget(self) -> int:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        try:
            return max(1, int(params.get("reference_budget", 4)))
        except (TypeError, ValueError):
            return 4

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        episodes: list[ClipToShotsEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            segment = self._load_episode_output(project_dir, "clip_segment", episode_key, ClipSegmentOutput)
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ClipToShotsEpisodeOutput(episode_key=episode_key, clips=[])
            if path.exists() and not self._force(self.workflow):
                existing = ClipToShotsEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))
            existing_by_clip = {item.clip_id: item for item in existing.clips}
            plans: list[ClipShotPlan] = []
            source_clips = list(segment.root.items())
            target_duration = int(state.metadata.get("episode_duration_seconds") or 30)
            clip_budgets = [
                float(clip.allocated_seconds)
                if clip.allocated_seconds is not None
                else target_duration / max(1, len(source_clips))
                for _, clip in source_clips
            ]
            budget_total = sum(clip_budgets)
            if budget_total <= 0:
                raise ValueError(f"clip_to_shots has no positive duration budget for {episode_key}")
            clip_budgets = [target_duration * value / budget_total for value in clip_budgets]
            reference_budget = self._reference_budget()
            for clip_index, (_, clip) in enumerate(source_clips, start=1):
                clip_id = f"{episode_key}_clip_{clip_index:03d}"
                if self._active_clip_selectors() and not clip_matches_selectors(episode_key, clip_id, clip_index, self._active_clip_selectors()):
                    if clip_id in existing_by_clip:
                        plans.append(existing_by_clip[clip_id])
                    continue
                asset_index, assets = self._asset_index(project_dir, state, clip=clip)
                previous = self._clean(source_clips[clip_index - 2][1].text)[-400:] if clip_index > 1 else ""
                following = self._clean(source_clips[clip_index][1].text)[:400] if clip_index < len(source_clips) else ""
                raw = await self.workflow.director_service.clip_to_shots(
                    state,
                    provider,
                    audit_asset_name=clip_id,
                    clip_text=clip.text,
                    asset_index=asset_index,
                    previous_context=previous,
                    next_context=following,
                    available_seconds=clip_budgets[clip_index - 1],
                    reference_budget=reference_budget,
                )
                rows = self._validate_model_output(raw, assets)
                for row in rows:
                    if len(row.ref_ids) > reference_budget:
                        raise ValueError(
                            f"{clip_id} uses {len(row.ref_ids)} references; provider budget is {reference_budget}"
                        )
                plans.append(
                    ClipShotPlan(
                        clip_id=clip_id,
                        clip_index=clip_index,
                        shots=[
                            ShotPlanItem(
                                shot_id=f"{clip_id}_shot_{shot_index:03d}",
                                clip_id=clip_id,
                                clip_index=clip_index,
                                shot_index_in_clip=shot_index,
                                episode_shot_index=0,
                                shot_description=item.shot_description,
                                narrative_angle=item.narrative_angle,
                                opening_state=item.opening_state,
                                ref_ids=item.ref_ids,
                                video_prompt=item.video_prompt,
                                duration_seconds=item.duration_seconds,
                                entity_states=item.entity_states,
                                dialogue_lines=item.dialogue_lines,
                                overlay_text_spec=item.overlay_text_spec,
                                allowed_props=item.allowed_props,
                                duration_budget=clip_budgets[clip_index - 1],
                                reference_budget=reference_budget,
                            )
                            for shot_index, item in enumerate(rows, start=1)
                        ],
                    )
                )
                state.budget.used_text_calls += 1
            counter = 0
            all_shots = [shot for plan in plans for shot in plan.shots]
            normalized = normalize_shot_durations(
                [shot.duration_seconds for shot in all_shots],
                target_duration,
            )
            for shot, duration in zip(all_shots, normalized):
                shot.duration_seconds = duration
            for plan in plans:
                for shot in plan.shots:
                    counter += 1
                    shot.episode_shot_index = counter
            total_duration = float(sum(shot.duration_seconds for shot in all_shots))
            assert_duration_gate(total_duration, float(target_duration))
            output = ClipToShotsEpisodeOutput(
                episode_key=episode_key,
                clips=plans,
                target_duration_seconds=float(target_duration),
                total_duration_seconds=total_duration,
                duration_gate_status="accepted",
            )
            self.repo.write_json(path, output)
            selection = configured_episode_output_selection(
                self.repo,
                project_dir,
                output,
            )
            state.metadata["expected_output_selection_path"] = self.layout.project_relative(
                project_dir,
                self.layout.expected_output_selection_path(project_dir),
            )
            selection_summaries = state.metadata.get("expected_output_selection")
            if not isinstance(selection_summaries, dict):
                selection_summaries = {}
                state.metadata["expected_output_selection"] = selection_summaries
            selection_summaries[episode_key] = {
                "planned_output_seconds": selection.planned_output_seconds,
                "selected_clip_count": selection.selected_clip_count,
                "selected_shot_count": selection.selected_shot_count,
                "target_reached": selection.target_reached,
            }
            episodes.append(output)
        self.repo.save_node_output(project_dir, self.name, ClipToShotsOutput(episodes=episodes))
        return state


class LayoutToBackgroundPromptNode(ShotAssetNodeBase):
    name = "layout_to_background_prompt"

    def _existing(self, project_dir: Path, episode_key: str) -> LayoutToBackgroundPromptEpisodeOutput:
        path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
        if not path.exists():
            return LayoutToBackgroundPromptEpisodeOutput(episode_key=episode_key)
        return LayoutToBackgroundPromptEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def _validate_background_plan(
        self,
        output: LayoutBackgroundPromptModelOutput,
        *,
        expected_indices: set[int],
    ) -> list[tuple[int, Any]]:
        if not output.root:
            raise ValueError("layout_to_background_prompt returned no backgrounds")
        parsed: list[tuple[int, Any]] = []
        assigned: list[int] = []
        for key, item in output.root.items():
            match = self._LOCAL_BACKGROUND_KEY.fullmatch(str(key))
            if not match:
                raise ValueError(f"Invalid local background ID: {key}")
            item.prompt_content = self._clean(item.prompt_content)
            item.description = self._clean(item.description)
            if not item.prompt_content or not item.description:
                raise ValueError(f"Background {key} has empty prompt_content or description")
            if any(index not in expected_indices for index in item.shot_indices):
                raise ValueError(f"Background {key} maps an unknown local shot index")
            assigned.extend(item.shot_indices)
            parsed.append((int(match.group(1)), item))
        if set(assigned) != expected_indices or len(assigned) != len(set(assigned)):
            raise ValueError("Each target shot must map to exactly one background")
        return sorted(parsed, key=lambda pair: pair[0])

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        outputs: list[LayoutToBackgroundPromptEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput)
            targets = self._selected_shots(project_dir, plan)
            assets = self._all_assets(project_dir, state)
            existing = self._existing(project_dir, episode_key)
            retained = [
                item
                for item in existing.backgrounds
                if not self._force(self.workflow) and not set(item.shot_ids).intersection({shot.shot_id for shot in targets})
            ]
            backgrounds: list[ShotBackgroundPromptItem] = list(retained)
            by_layout: dict[str, list[ShotPlanItem]] = {}
            for shot in targets:
                layout_id, _ = self._shot_layout(shot, assets)
                by_layout.setdefault(layout_id, []).append(shot)
            for layout_id, layout_shots in by_layout.items():
                layout_item = assets[layout_id]
                layout = layout_item["object"]
                layout_ref = self._asset_ref(project_dir, layout_item)
                if not (layout_ref.path or layout_ref.url):
                    raise ValueError(f"layout {layout_id} has no usable reference image")
                background_prefix = f"{layout_id}_background_"
                retained_indices = [
                    int(item.background_id[len(background_prefix) :])
                    for item in backgrounds
                    if item.layout_id == layout_id
                    and item.background_id.startswith(background_prefix)
                    and item.background_id[len(background_prefix) :].isdigit()
                ]
                background_index_offset = max(retained_indices, default=0)
                shot_rows = [
                    {
                        "index": index,
                        "shot_description": shot.shot_description,
                        "narrative_angle": shot.narrative_angle,
                        "opening_state": shot.opening_state,
                        "fixed_props": [
                            assets[ref_id]["label"]
                            for ref_id in shot.ref_ids
                            if ref_id in assets and assets[ref_id]["kind"] == "prop"
                        ],
                    }
                    for index, shot in enumerate(layout_shots, start=1)
                ]
                request_prompt = self.workflow.prompts.render(
                    self.name,
                    layout_description=self._clean(layout.desc or layout.name),
                    shots=json.dumps(shot_rows, ensure_ascii=False, indent=2),
                )
                response = await provider.generate_json(
                    request_prompt,
                    LayoutBackgroundPromptModelOutput,
                    temperature=0.25,
                    refs=[layout_ref],
                    metadata={
                        "node_name": self.name,
                        "prompt_asset_type": self.name,
                        "prompt_asset_name": layout.id,
                        "layout_id": layout.id,
                    },
                )
                state.budget.used_text_calls += 1
                validated = self._validate_background_plan(response, expected_indices=set(range(1, len(layout_shots) + 1)))
                for background_index, item in validated:
                    assigned_shots = [layout_shots[index - 1] for index in item.shot_indices]
                    final_prompt = self.workflow.prompts.render_final(
                        self.name,
                        item.prompt_content,
                        {"visual_quality": self._visual_quality(state)},
                    )
                    backgrounds.append(
                        ShotBackgroundPromptItem(
                            background_id=(
                                f"{layout_id}_background_"
                                f"{background_index_offset + background_index:03d}"
                            ),
                            layout_id=layout_id,
                            shot_ids=[shot.shot_id for shot in assigned_shots],
                            description=item.description,
                            prompt=final_prompt,
                        )
                    )
            seen_shots = [shot_id for item in backgrounds for shot_id in item.shot_ids]
            if len(seen_shots) != len(set(seen_shots)):
                raise ValueError(f"layout_to_background_prompt assigned a shot to multiple backgrounds in {episode_key}")
            output = LayoutToBackgroundPromptEpisodeOutput(episode_key=episode_key, backgrounds=backgrounds)
            self.repo.write_json(self.layout.node_episode_output_path(project_dir, self.name, episode_key), output)
            outputs.append(output)
        self.repo.save_node_output(project_dir, self.name, LayoutToBackgroundPromptOutput(episodes=outputs))
        return state


class ShotBackgroundImageGenerationNode(ShotAssetNodeBase):
    name = "shot_background_image_generation"

    def _concurrency(self) -> int:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        try:
            return max(1, min(5, int(params.get("concurrency", 2))))
        except (TypeError, ValueError):
            return 2

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("shot", node_name=self.name)
        if not bool(getattr(provider, "supports_reference_images", True)):
            raise ValueError("shot_background_image_generation requires a reference-image provider")
        all_outputs: list[ShotBackgroundImageGenerationEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput)
            prompt_output = self._load_episode_output(project_dir, "layout_to_background_prompt", episode_key, LayoutToBackgroundPromptEpisodeOutput)
            selected_ids = {
                shot.shot_id for shot in self._selected_shots(project_dir, plan)
            }
            assets = self._all_assets(project_dir, state)
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotBackgroundImageGenerationEpisodeOutput(episode_key=episode_key)
            if path.exists():
                existing = ShotBackgroundImageGenerationEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))
            by_id = {item.background_id: item for item in existing.generated_backgrounds}
            targets = [item for item in prompt_output.backgrounds if selected_ids.intersection(item.shot_ids)]
            semaphore = asyncio.Semaphore(self._concurrency())

            async def generate(item: ShotBackgroundPromptItem) -> ShotBackgroundImageGenerationItem:
                layout_item = assets.get(item.layout_id)
                if layout_item is None or layout_item["kind"] != "layout":
                    raise ValueError(f"Background {item.background_id} references unavailable layout {item.layout_id}")
                layout_ref = self._asset_ref(project_dir, layout_item)
                fingerprint = self._generation_fingerprint(item.prompt, [layout_ref], provider)
                old = by_id.get(item.background_id)
                if (
                    old
                    and not self._force(self.workflow)
                    and old.fingerprint == fingerprint
                    and self.layout.existing_project_file(project_dir, old.asset_path)
                ):
                    return old
                async with semaphore:
                    result, final_prompt, _ = await self._generate_image_with_safety_prompt_rewrites(
                        provider=provider,
                        state=state,
                        node_name=self.name,
                        asset_id=item.background_id,
                        prompt=item.prompt,
                        refs=[layout_ref],
                        metadata={
                            "node_name": self.name,
                            "prompt_asset_type": "shot_background",
                            "prompt_asset_name": item.background_id,
                            "background_id": item.background_id,
                        },
                        context={"description": item.description},
                    )
                    asset_path = await self.media_store.write_first_generated_image(
                        project_dir,
                        self.layout.image_asset_path(project_dir, "shot_backgrounds", item.background_id),
                        result,
                    )
                return ShotBackgroundImageGenerationItem(
                    background_id=item.background_id,
                    layout_id=item.layout_id,
                    shot_ids=item.shot_ids,
                    description=item.description,
                    prompt=final_prompt,
                    fingerprint=fingerprint,
                    asset_path=asset_path,
                    asset_url=self.first_image_url(result),
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )

            generated = await asyncio.gather(*(generate(item) for item in targets))
            regenerated_shot_ids: set[str] = set()
            for item in targets:
                layout_item = assets[item.layout_id]
                expected_fingerprint = self._generation_fingerprint(
                    item.prompt,
                    [self._asset_ref(project_dir, layout_item)],
                    provider,
                )
                old = by_id.get(item.background_id)
                reusable = bool(
                    old
                    and old.fingerprint == expected_fingerprint
                    and self.layout.existing_project_file(project_dir, old.asset_path)
                )
                if self._force(self.workflow) or not reusable:
                    regenerated_shot_ids.update(item.shot_ids)
            for item in generated:
                by_id[item.background_id] = item
            required_ids = {item.background_id for item in prompt_output.backgrounds}
            missing = required_ids.difference(by_id)
            if missing:
                raise ValueError(f"shot_background_image_generation missing backgrounds: {', '.join(sorted(missing))}")
            output = ShotBackgroundImageGenerationEpisodeOutput(
                episode_key=episode_key,
                generated_backgrounds=[by_id[item.background_id] for item in prompt_output.backgrounds],
            )
            self.repo.write_json(path, output)
            self._invalidate_background_dependents(project_dir, episode_key, regenerated_shot_ids)
            all_outputs.append(output)
        self.repo.save_node_output(project_dir, self.name, ShotBackgroundImageGenerationOutput(episodes=all_outputs))
        return state


class ShotKeyframePromptNode(ShotAssetNodeBase):
    name = "shot_keyframe_prompt"

    def _negative_prompt(self) -> str:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        return self._clean(params.get("negative_prompt"))

    @staticmethod
    def _requires_clean_plate(shot: ShotPlanItem) -> bool:
        return bool(
            shot.overlay_text_spec is not None
            and shot.overlay_text_spec.render_mode == "postproduction"
        )

    @classmethod
    def _text_rendering_instruction(cls, shot: ShotPlanItem) -> str:
        if cls._requires_clean_plate(shot):
            return "生成无任何可读文字的 clean plate，并为后期叠字保留指定位置。"
        if shot.overlay_text_spec is not None:
            return f"场景内准确呈现文字：{shot.overlay_text_spec.text}"
        return "本镜头没有精确文字渲染要求。"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        negative = self._negative_prompt()
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput)
            rows = [shot for clip in plan.clips for shot in clip.shots]
            backgrounds = self._load_episode_output(project_dir, "shot_background_image_generation", episode_key, ShotBackgroundImageGenerationEpisodeOutput)
            targets = self._selected_shots(project_dir, plan)
            assets = self._all_assets(project_dir, state)
            style_spec = VisualStyleSpec.model_validate(state.metadata.get("visual_style_spec"))
            background_by_shot: dict[str, ShotBackgroundImageGenerationItem] = {}
            for background in backgrounds.generated_backgrounds:
                for shot_id in background.shot_ids:
                    if shot_id in background_by_shot:
                        raise ValueError(f"{shot_id} has multiple generated backgrounds")
                    background_by_shot[shot_id] = background
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotKeyframePromptOutput(prompts=[])
            if path.exists():
                existing = ShotKeyframePromptOutput.model_validate_json(path.read_text(encoding="utf-8"))
            by_id = {item.shot_id: item for item in existing.prompts}
            for shot in targets:
                background = background_by_shot.get(shot.shot_id)
                if background is None:
                    raise ValueError(f"shot_keyframe_prompt has no background for {shot.shot_id}")
                background_ref = self._background_asset_ref(project_dir, background)
                role_refs: list[tuple[str, AssetRef]] = []
                prop_refs: list[tuple[str, AssetRef]] = []
                for ref_id in shot.ref_ids:
                    item = assets.get(ref_id)
                    if item is None:
                        raise ValueError(f"{shot.shot_id} has unresolved ref_id {ref_id}")
                    if item["kind"] == "roleboard":
                        role_refs.append((item["label"], self._asset_ref(project_dir, item)))
                    elif item["kind"] == "prop":
                        prop_refs.append((item["label"], self._asset_ref(project_dir, item)))
                foreground = [*role_refs, *prop_refs]
                refs = [background_ref, *[ref for _, ref in foreground]]
                guide = ["图1：当前镜头背景与机位锚点"]
                guide.extend(f"图{index}：{label}" for index, (label, _) in enumerate(foreground, start=2))
                request_prompt = self.workflow.prompts.render(
                    self.name,
                    shot_description=shot.shot_description,
                    narrative_angle=shot.narrative_angle,
                    opening_state=shot.opening_state,
                    reference_guide="\n".join(guide),
                    visual_quality=self._visual_quality(state),
                    text_rendering_instruction=self._text_rendering_instruction(shot),
                )
                response = await provider.generate_json(
                    request_prompt,
                    ShotKeyframePromptModelOutput,
                    temperature=0.3,
                    refs=refs,
                    metadata={
                        "node_name": self.name,
                        "prompt_asset_type": self.name,
                        "prompt_asset_name": shot.shot_id,
                        "shot_id": shot.shot_id,
                    },
                )
                state.budget.used_text_calls += 1
                final_prompt = self.workflow.prompts.render_final(
                    self.name,
                    self._clean(response.prompt_content),
                    {"reference_guide": "\n".join(guide), "negative_prompt": negative},
                )
                clean_plate = self._requires_clean_plate(shot)
                if clean_plate:
                    final_prompt += "\n画面必须是无可读文字的 clean plate，精确文字将在后期叠加。"
                by_id[shot.shot_id] = ShotKeyframePromptItem(
                    episode_key=episode_key,
                    clip_id=shot.clip_id,
                    shot_id=shot.shot_id,
                    background_id=background.background_id,
                    background_asset_path=background.asset_path,
                    background_asset_url=background.asset_url,
                    ref_ids=shot.ref_ids,
                    prompt=final_prompt,
                    negative_prompt=negative or None,
                    style_spec_version=style_spec.version,
                    included_fields=["identity_invariants", "current_shot_state", "allowed_props", "camera", "visual_style"],
                    excluded_state_fields=["legacy_desc", "future_events", "future_prop_states", "exact_text"],
                    prompt_provenance={
                        "appearance_ids": [
                            ref.metadata.get("appearance_id") for _, ref in role_refs if ref.metadata.get("appearance_id")
                        ],
                        "prop_ref_ids": [ref.id for _, ref in prop_refs],
                        "background_id": background.background_id,
                    },
                    clean_plate=clean_plate,
                    overlay_text_spec=shot.overlay_text_spec,
                )
            output = ShotKeyframePromptOutput(prompts=[by_id[shot.shot_id] for shot in rows if shot.shot_id in by_id])
            self.repo.write_json(path, output)
        return state


class ShotKeyframeImageGenerationNode(ShotAssetNodeBase):
    name = "shot_keyframe_image_generation"

    def _concurrency(self) -> int:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        try:
            return max(1, min(5, int(params.get("concurrency", 2))))
        except (TypeError, ValueError):
            return 2

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("shot", node_name=self.name)
        if not bool(getattr(provider, "supports_reference_images", True)):
            raise ValueError("shot_keyframe_image_generation requires a reference-image provider")
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput)
            prompt_output = self._load_episode_output(project_dir, "shot_keyframe_prompt", episode_key, ShotKeyframePromptOutput)
            backgrounds = self._load_episode_output(project_dir, "shot_background_image_generation", episode_key, ShotBackgroundImageGenerationEpisodeOutput)
            rows = [shot for clip in plan.clips for shot in clip.shots]
            targets = self._selected_shots(project_dir, plan)
            assets = self._all_assets(project_dir, state)
            prompts_by_id = {item.shot_id: item for item in prompt_output.prompts}
            backgrounds_by_id = {item.background_id: item for item in backgrounds.generated_backgrounds}
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotKeyframeImageGenerationOutput(generated_keyframes=[])
            if path.exists():
                existing = ShotKeyframeImageGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
            by_id = {item.shot_id: item for item in existing.generated_keyframes}
            semaphore = asyncio.Semaphore(self._concurrency())

            async def generate(shot: ShotPlanItem) -> ShotKeyframeImageGenerationItem:
                prompt = prompts_by_id.get(shot.shot_id)
                if prompt is None:
                    raise ValueError(f"missing keyframe prompt for {shot.shot_id}")
                background = backgrounds_by_id.get(prompt.background_id)
                if background is None:
                    raise ValueError(f"missing background {prompt.background_id} for {shot.shot_id}")
                role_refs: list[AssetRef] = []
                prop_refs: list[AssetRef] = []
                for ref_id in shot.ref_ids:
                    item = assets.get(ref_id)
                    if item is None:
                        raise ValueError(f"{shot.shot_id} has unresolved ref_id {ref_id}")
                    if item["kind"] == "roleboard":
                        role_refs.append(self._asset_ref(project_dir, item))
                    elif item["kind"] == "prop":
                        prop_refs.append(self._asset_ref(project_dir, item))
                refs = [self._background_asset_ref(project_dir, background), *role_refs, *prop_refs]
                fingerprint = self._generation_fingerprint(prompt.prompt, refs, provider)
                old = by_id.get(shot.shot_id)
                if (
                    old
                    and not self._force(self.workflow)
                    and old.fingerprint == fingerprint
                    and self.layout.existing_project_file(project_dir, old.keyframe_asset_path)
                ):
                    return old
                limit = int(getattr(provider, "max_reference_images", 99) or 99)
                if len(refs) > limit:
                    raise ValueError(f"{shot.shot_id} needs {len(refs)} references but provider limit is {limit}; split the shot")
                async with semaphore:
                    result, final_prompt, _ = await self._generate_image_with_safety_prompt_rewrites(
                        provider=provider,
                        state=state,
                        node_name=self.name,
                        asset_id=f"{shot.shot_id}_keyframe",
                        prompt=prompt.prompt,
                        refs=refs,
                        metadata={
                            "node_name": self.name,
                            "prompt_asset_type": "shot_keyframe",
                            "prompt_asset_name": shot.shot_id,
                            "shot_id": shot.shot_id,
                        },
                        context={"shot_description": shot.shot_description},
                    )
                    asset_path = await self.media_store.write_first_generated_image(
                        project_dir,
                        self.layout.image_asset_path(project_dir, "shot_keyframes", shot.shot_id),
                        result,
                    )
                return ShotKeyframeImageGenerationItem(
                    episode_key=episode_key,
                    clip_id=shot.clip_id,
                    shot_id=shot.shot_id,
                    background_id=background.background_id,
                    ref_ids=shot.ref_ids,
                    keyframe_asset_id=f"{shot.shot_id}_keyframe",
                    keyframe_asset_path=asset_path,
                    keyframe_asset_url=self.first_image_url(result),
                    prompt=final_prompt,
                    fingerprint=fingerprint,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                    input_fingerprint=fingerprint,
                    audit_status="generated",
                    resume_provenance={"reused": False, "fingerprint_verified": True},
                )

            generated = await asyncio.gather(*(generate(shot) for shot in targets))
            for item in generated:
                by_id[item.shot_id] = item
            missing = {shot.shot_id for shot in targets}.difference(by_id)
            if missing:
                raise ValueError(f"shot_keyframe_image_generation missing keyframes: {', '.join(sorted(missing))}")
            self.repo.write_json(
                path,
                ShotKeyframeImageGenerationOutput(
                    generated_keyframes=[by_id[shot.shot_id] for shot in rows if shot.shot_id in by_id],
                ),
            )
        return state


class ShotManifestGenerationNode(ShotAssetNodeBase):
    name = "shot_manifest_generation"

    def _accepted_assets(self, project_dir: Path, node_name: str) -> set[str]:
        path = self.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            return set()
        output = ImageAssetAuditOutput.model_validate_json(path.read_text(encoding="utf-8"))
        return {item.asset_id for item in output.audited_assets if item.approved}

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        generated: list[ShotManifestGenerationEpisodeItem] = []
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput)
            prompts = self._load_episode_output(project_dir, "shot_keyframe_prompt", episode_key, ShotKeyframePromptOutput)
            images = self._load_episode_output(project_dir, "shot_keyframe_image_generation", episode_key, ShotKeyframeImageGenerationOutput)
            backgrounds = self._load_episode_output(project_dir, "shot_background_image_generation", episode_key, ShotBackgroundImageGenerationEpisodeOutput)
            rows = [shot for clip in plan.clips for shot in clip.shots]
            prompt_by_id = {item.shot_id: item for item in prompts.prompts}
            image_by_id = {item.shot_id: item for item in images.generated_keyframes}
            background_by_id = {item.background_id: item for item in backgrounds.generated_backgrounds}
            accepted_keyframes = self._accepted_assets(project_dir, "shot_keyframe_image_audit")
            accepted_backgrounds = self._accepted_assets(project_dir, "shot_background_image_audit")
            accepted_roleboards = self._accepted_assets(project_dir, "roleboard_image_audit")
            assets = self._all_assets(project_dir, state)
            existing_by_id: dict[str, ShotManifestItem] = {}
            output_path = self.layout.shot_path(project_dir, episode_key)
            if output_path.exists():
                try:
                    existing = ShotManifestEpisodeOutput.model_validate_json(output_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    self.logger.warning("ignored incompatible existing shot manifest %s: %s", output_path, exc)
                else:
                    existing_by_id = {shot.shot_id: shot for shot in existing.shots}
            selected_ids = {
                shot.shot_id for shot in self._selected_shots(project_dir, plan)
            }
            shots: list[ShotManifestItem] = []
            for row in rows:
                if row.shot_id not in selected_ids and row.shot_id in existing_by_id:
                    shots.append(existing_by_id[row.shot_id])
                    continue
                if row.shot_id not in selected_ids:
                    continue
                prompt = prompt_by_id.get(row.shot_id)
                image = image_by_id.get(row.shot_id)
                if prompt is None or image is None:
                    raise ValueError(f"shot_manifest_generation missing prompt or keyframe for {row.shot_id}")
                background = background_by_id.get(image.background_id)
                if background is None:
                    raise ValueError(f"shot_manifest_generation missing background {image.background_id}")
                if not self.layout.existing_project_file(project_dir, background.asset_path):
                    raise FileNotFoundError(f"shot background asset is missing: {background.asset_path}")
                if not self.layout.existing_project_file(project_dir, image.keyframe_asset_path):
                    raise FileNotFoundError(f"shot keyframe asset is missing: {image.keyframe_asset_path}")
                gate_results = [
                    GateResult(
                        name="duration",
                        status="accepted" if plan.duration_gate_status == "accepted" else "rejected",
                        details=f"episode total={plan.total_duration_seconds}s target={plan.target_duration_seconds}s",
                    ),
                    GateResult(
                        name="background_audit",
                        status="accepted" if background.background_id in accepted_backgrounds else "rejected",
                        details=background.background_id,
                    ),
                    GateResult(
                        name="keyframe_audit",
                        status=(
                            "accepted"
                            if image.keyframe_asset_id in accepted_keyframes and image.audit_status == "accepted"
                            else "rejected"
                        ),
                        details=image.keyframe_asset_id,
                    ),
                ]
                layout_id, _ = self._shot_layout(row, assets)
                role_ids: list[str] = []
                appearance_ids: list[str] = []
                prop_ids: list[str] = []
                for ref_id in row.ref_ids:
                    item = assets.get(ref_id)
                    if item is None:
                        raise ValueError(f"manifest has unresolved ref_id {ref_id}")
                    if item["kind"] == "roleboard":
                        role_ids.append(item["object"][0].id)
                        appearance_ids.append(item["object"][1].id)
                    elif item["kind"] == "prop":
                        prop_ids.append(item["object"][0].id)
                prop_ids.extend(row.allowed_props)
                if not row.narrative_angle or not row.video_prompt:
                    raise ValueError(f"shot manifest requires narrative_angle and video_prompt for {row.shot_id}")
                video_inputs = [
                    ShotVideoInput(
                        slot="image_1",
                        asset_type="shot_keyframe",
                        asset_id=image.keyframe_asset_id,
                        asset_path=image.keyframe_asset_path,
                        asset_url=image.keyframe_asset_url,
                        source_node="shot_keyframe_image_generation",
                        required=True,
                        order=0,
                        metadata={
                            "reference_role": "shot_keyframe_anchor",
                            "kling_content_id": "shot_keyframe",
                        },
                    )
                ]
                seen_role_ids: set[str] = set()
                for ref_id in row.ref_ids:
                    item = assets.get(ref_id)
                    if item is None or item["kind"] != "roleboard":
                        continue
                    role, appearance = item["object"]
                    if role.id in seen_role_ids:
                        continue
                    roleboard_path = appearance.asset_path or appearance.design_image_asset_path
                    roleboard_url = appearance.asset_url or appearance.design_image_asset_url
                    if not (roleboard_path or roleboard_url):
                        raise ValueError(
                            f"shot_manifest_generation missing roleboard for "
                            f"{row.shot_id}/{role.name}/{appearance.name}"
                        )
                    if roleboard_path and not self.layout.existing_project_file(project_dir, roleboard_path):
                        raise FileNotFoundError(f"shot roleboard asset is missing: {roleboard_path}")
                    seen_role_ids.add(role.id)
                    role_index = len(seen_role_ids)
                    video_inputs.append(
                        ShotVideoInput(
                            slot=f"image_{len(video_inputs) + 1}",
                            asset_type="roleboard",
                            asset_id=(
                                appearance.asset_id
                                or appearance.design_image_asset_id
                                or f"{appearance.id}_roleboard"
                            ),
                            asset_path=roleboard_path,
                            asset_url=roleboard_url,
                            source_node="roleboard_image_generation",
                            label=f"{role.name}/{appearance.name}",
                            role_id=role.id,
                            role_name=role.name,
                            appearance_id=appearance.id,
                            appearance_name=appearance.name,
                            required=True,
                            order=len(video_inputs),
                            metadata={
                                "reference_role": "character_turnaround_identity",
                                "kling_content_id": f"role_{role_index}",
                            },
                        )
                    )
                    roleboard_asset_id = (
                        appearance.asset_id
                        or appearance.design_image_asset_id
                        or f"{appearance.id}_roleboard"
                    )
                    gate_results.append(
                        GateResult(
                            name=f"roleboard_audit:{role.id}",
                            status="accepted" if roleboard_asset_id in accepted_roleboards else "rejected",
                            details=roleboard_asset_id,
                        )
                    )
                required_visual_role_ids = {
                    state.entity_id for state in row.entity_states
                }
                missing_role_ids = sorted(required_visual_role_ids.difference(seen_role_ids))
                if missing_role_ids:
                    raise ValueError(
                        f"shot_manifest_generation missing involved character roleboards for "
                        f"{row.shot_id}: {', '.join(missing_role_ids)}"
                    )
                reference_budget = row.reference_budget or 4
                gate_results.append(
                    GateResult(
                        name="reference_budget",
                        status="accepted" if len(row.ref_ids) <= reference_budget else "rejected",
                        details=f"{len(row.ref_ids)}/{reference_budget}",
                    )
                )
                ready_for_video = all(
                    gate.status == "accepted" for gate in gate_results if gate.required
                )
                video_prompt = row.video_prompt
                if prompt.clean_plate:
                    video_prompt += "；不得生成可读文字、字幕、标识或人声，保持 clean plate。"
                shots.append(
                    ShotManifestItem(
                        shot_id=row.shot_id,
                        index=row.episode_shot_index,
                        clip_id=row.clip_id,
                        clip_index=row.clip_index,
                        shot_index_in_clip=row.shot_index_in_clip,
                        ref_ids=row.ref_ids,
                        layout_id=layout_id,
                        layout_ids=[layout_id],
                        title=row.shot_description,
                        content=row.shot_description,
                        shot_description=row.shot_description,
                        narrative_angle=row.narrative_angle,
                        opening_state=row.opening_state,
                        duration_seconds=float(row.duration_seconds),
                        dialogue_lines=row.dialogue_lines,
                        role_ids=list(dict.fromkeys(role_ids)),
                        role_appearance_ids=list(dict.fromkeys(appearance_ids)),
                        prop_ids=list(dict.fromkeys(prop_ids)),
                        video_prompt=video_prompt,
                        final_video_prompt=video_prompt,
                        background_asset_id=background.background_id,
                        background_asset_path=background.asset_path,
                        background_asset_url=background.asset_url,
                        video_inputs=video_inputs,
                        gate_results=gate_results,
                        entity_state_snapshot=row.entity_states,
                        reference_budget=reference_budget,
                        text_overlay_spec=row.overlay_text_spec,
                        input_fingerprints={
                            "keyframe": image.fingerprint,
                            "background": background.fingerprint,
                        },
                        ready_for_video=ready_for_video,
                    )
                )
            target_duration = float(plan.target_duration_seconds or state.metadata.get("episode_duration_seconds") or 30)
            total_duration = sum(shot.duration_seconds for shot in shots)
            duration_ok = plan.duration_gate_status == "accepted"
            episode_gates = [
                GateResult(
                    name="duration",
                    status="accepted" if duration_ok else "rejected",
                    details=f"{total_duration:.3f}/{target_duration:.3f}",
                ),
                GateResult(
                    name="all_shots_ready",
                    status="accepted" if shots and all(shot.ready_for_video for shot in shots) else "rejected",
                    details=f"{sum(shot.ready_for_video for shot in shots)}/{len(shots)}",
                ),
            ]
            episode_ready = all(gate.status == "accepted" for gate in episode_gates if gate.required)
            episode = ShotManifestEpisodeOutput(
                episode_key=episode_key,
                shots=shots,
                gate_results=episode_gates,
                ready_for_video=episode_ready,
            )
            self.workflow._save_shot_manifest(project_dir, episode)
            item = ShotManifestGenerationEpisodeItem(
                episode_key=episode_key,
                shot_count=len(shots),
                shot_path=self.layout.project_relative(project_dir, output_path),
                warnings=[] if episode_ready else ["required manifest gates rejected; video generation is blocked"],
            )
            self.repo.write_json(self.layout.node_episode_output_path(project_dir, self.name, episode_key), ShotManifestGenerationOutput(episodes=[item]))
            generated.append(item)
        self.repo.save_node_output(project_dir, self.name, ShotManifestGenerationOutput(episodes=generated))
        return state


def build_shot_asset_nodes(workflow: Any) -> list[WorkflowNode]:
    deps = {
        "workflow": workflow,
        "repo": workflow.repo,
        "layout": workflow.layout,
        "router": workflow.router,
        "script_service": workflow.script_service,
        "asset_service": workflow.asset_service,
        "script_contents": workflow.script_contents,
        "prop_designs": workflow.prop_designs,
        "media_store": workflow.media_store,
        "logger": getattr(workflow, "logger", None) or get_logger(),
    }
    return [
        WorkflowNode(name=cls.name, run=cls(**deps).run)
        for cls in (
            ClipToShotsNode,
            LayoutToBackgroundPromptNode,
            ShotBackgroundImageGenerationNode,
            ShotKeyframePromptNode,
            ShotKeyframeImageGenerationNode,
            ShotManifestGenerationNode,
        )
    ]


__all__ = ["SHOT_ASSET_NODE_NAMES", "build_shot_asset_nodes"]
