"""Shot-first pre-generation with explicit scene, blocking, and camera anchors."""

from __future__ import annotations

import asyncio
import colorsys
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import ValidationError

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
    SceneMultiviewImageGenerationEpisodeOutput,
    SceneMultiviewImageGenerationItem,
    SceneMultiviewImageGenerationOutput,
    SceneMultiviewGeneratedViewItem,
    SceneMultiviewPlanEpisodeOutput,
    SceneMultiviewPlanItem,
    SceneMultiviewPlanModelOutput,
    SceneMultiviewPlanOutput,
    SceneMultiviewShotAssignment,
    SceneMultiviewViewPlanItem,
    StaticAssetGenerationOutput,
    ShotBackgroundImageGenerationEpisodeOutput,
    ShotBackgroundImageGenerationItem,
    ShotBackgroundImageGenerationOutput,
    ShotBackgroundPromptItem,
    ShotBlockingBinding,
    ShotBlockingControlEpisodeOutput,
    ShotBlockingControlItem,
    ShotBlockingControlOutput,
    ShotBlockingPlanEpisodeOutput,
    ShotBlockingPlanItem,
    ShotBlockingPlanModelOutput,
    ShotBlockingPlanOutput,
    ShotKeyframeImageGenerationItem,
    ShotKeyframeImageGenerationOutput,
    ShotKeyframePromptItem,
    ShotKeyframePromptModelOutput,
    ShotKeyframePromptOutput,
    ShotKeyframeStageGenerationItem,
    ShotKeyframeStageGenerationOutput,
    ShotManifestGenerationEpisodeItem,
    ShotManifestGenerationOutput,
    ShotPlanItem,
    ShotManifestEpisodeOutput,
    ShotManifestItem,
    ShotVideoInput,
    GateResult,
    ImageAssetAuditOutput,
)
from autodrama.core.visual_contract import identity_brief
from autodrama.logging import get_logger
from autodrama.providers.base import AssetRef
from autodrama.workflows.scene_multiview import crop_scene_multiview_board
from autodrama.workflows.shot_blocking_control import (
    BlockingControlBinding,
    render_shot_blocking_control,
)
from autodrama.workflows.nodes.static_asset_nodes import StaticAssetNodeBase
from autodrama.workflows.output_scope import (
    EXPECTED_OUTPUT_GENERATION_NODES,
    EXPECTED_OUTPUT_POSTGEN_NODES,
    configured_episode_output_selection,
)
from autodrama.workflows.runner import WorkflowNode
from autodrama.workflows.selection import clip_matches_selectors, shot_matches_selectors


SHOT_ASSET_NODE_NAMES = [
    "clip_to_shots",
    "scene_multiview_plan",
    "scene_multiview_image_generation",
    "layout_to_background_prompt",
    "shot_background_image_generation",
    "shot_blocking_plan",
    "shot_blocking_control_render",
    "shot_keyframe_prompt",
    "shot_keyframe_stage_generation",
    "shot_keyframe_image_generation",
    "shot_manifest_generation",
]


class ShotAssetNodeBase(StaticAssetNodeBase):
    _LOCAL_BACKGROUND_KEY = re.compile(r"^background_(\d+)$")
    _FORBIDDEN = ("p01", "p12", "://", "\\\\", "/assets/", "model=")
    _INTERNAL_CUT_RE = re.compile(
        r"(?i)(?:\bcut\s+to\b|\bthen\s+cut\b|\bcut[- ]in\b|\breverse\s+angle\b|"
        r"\bmontage\b|\bsecond\s+viewpoint\b|\bswitch(?:es)?\s+to\b|"
        r"切到|切为|转到另一|反打|蒙太奇)"
    )
    # Confirmed provider false-positive from the saodi source. Keep this narrowly
    # semantic-equivalent and outside dialogue; it is input hygiene, not a story beat.
    _POLICY_NEUTRAL_VISUAL_REPLACEMENTS = (
        (
            "指尖拂过花瓣，柔软的触感带着细微湿意；再捏住一片叶子，叶脉清晰，边缘划过皮肤时有轻轻的痒。"
            "她猛地缩回手，又忍不住再次触碰",
            "指尖让柔软花瓣微微弯曲，一滴露水沿花瓣滚动；她再捏起一片叶子，看清叶脉，"
            "叶缘轻扫指尖带来细微痒感。她条件反射地收手，迟疑后又伸手确认一次",
        ),
    )

    def active_episode_keys(self, state: ProjectState) -> list[str]:
        return super().active_episode_keys(state) or self.expected_episode_keys(state)

    @staticmethod
    def _clean(value: object) -> str:
        return " ".join(str(value or "").split()).strip()

    @classmethod
    def _visual_planning_text(cls, text: str) -> str:
        """Apply verified provider-safe visual wording."""

        for source, replacement in cls._POLICY_NEUTRAL_VISUAL_REPLACEMENTS:
            text = text.replace(source, replacement)
        return text

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
        wanted_roles = {self._clean(name).casefold() for name in getattr(clip, "role_names", [])}
        wanted_props = {self._clean(name).casefold() for name in getattr(clip, "prop_names", [])}
        scene_id = self._clean(getattr(clip, "scene_id", ""))
        assets: dict[str, dict[str, Any]] = {}
        index_rows: list[str] = []
        if scene_id:
            scene = state.layouts.get(scene_id)
            if scene is None:
                raise ValueError(f"clip references unavailable scene_id: {scene_id}")
            scene_rows = [scene]
        else:
            scene_rows = list(state.layouts.values())
        for scene in scene_rows:
            assets[scene.id] = {"kind": "layout", "label": scene.desc or scene.name, "object": scene}
        for role in state.roles.values():
            role_names = {
                self._clean(name).casefold()
                for name in (role.name, *role.aliases)
                if self._clean(name)
            }
            if wanted_roles and not wanted_roles.intersection(role_names):
                continue
            appearances = list(role.appearances.values())
            if not appearances:
                continue
            for appearance in appearances:
                asset_id = appearance.asset_id or appearance.design_image_asset_id or f"{appearance.id}_roleboard"
                label = identity_brief(appearance)
                assets[asset_id] = {
                    "kind": "roleboard",
                    "label": label,
                    "object": (role, appearance),
                }
                index_rows.append(
                    f"{asset_id}: character reference for {role.name} ({role.id}), "
                    f"appearance {appearance.name}; {label}"
                )
        for prop in state.props.values():
            prop_names = {
                self._clean(name).casefold()
                for name in (prop.name, *prop.aliases)
                if self._clean(name)
            }
            if wanted_props and not wanted_props.intersection(prop_names):
                continue
            asset = prop.base_asset
            if asset is not None:
                assets[asset.asset_id or asset.id] = {
                    "kind": "prop",
                    "label": asset.desc or prop.intro or prop.name,
                    "object": (prop, asset),
                }
                description = self._clean(asset.desc or prop.intro or prop.name)
                index_rows.append(
                    f"{asset.asset_id or asset.id}: prop reference for {prop.name} "
                    f"({prop.id}); {description}"
                )
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

    def _shot_scene(self, shot: ShotPlanItem, assets: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
        scene = assets.get(shot.scene_id)
        if scene is None or scene["kind"] != "layout":
            raise ValueError(f"{shot.shot_id} references unavailable scene {shot.scene_id}")
        return shot.scene_id, scene

    def _background_asset_ref(self, project_dir: Path, background: ShotBackgroundImageGenerationItem) -> AssetRef:
        path = project_dir / background.asset_path
        if not path.is_file() and not background.asset_url:
            raise FileNotFoundError(f"Background image is missing for {background.background_id}: {background.asset_path}")
        return AssetRef(
            id=background.background_id,
            type="image",
            path=str(path) if path.is_file() else None,
            url=background.asset_url,
            metadata={
                "asset_type": "shot_background",
                "background_id": background.background_id,
                "shot_id": background.shot_id,
                "scene_id": background.scene_id,
            },
        )

    def _scene_multiview_asset_ref(
        self,
        project_dir: Path,
        scene: SceneMultiviewImageGenerationItem,
        view: SceneMultiviewGeneratedViewItem,
    ) -> AssetRef:
        path = project_dir / view.asset_path
        if not path.is_file():
            raise FileNotFoundError(
                f"Scene multiview image is missing for {view.view_id}: {view.asset_path}"
            )
        return AssetRef(
            id=view.view_id,
            type="image",
            path=str(path),
            url=None,
            metadata={
                "asset_type": "scene_multiview",
                "scene_id": scene.scene_id,
                "view_id": view.view_id,
                "view_index": view.view_index,
            },
        )

    def _scene_multiview_board_ref(
        self,
        project_dir: Path,
        scene: SceneMultiviewImageGenerationItem,
    ) -> AssetRef:
        path = project_dir / scene.board_asset_path
        if not path.is_file() and not scene.board_asset_url:
            raise FileNotFoundError(
                f"Scene multiview board is missing for {scene.scene_id}: {scene.board_asset_path}"
            )
        return AssetRef(
            id=scene.board_asset_id,
            type="image",
            path=str(path) if path.is_file() else None,
            url=scene.board_asset_url,
            metadata={
                "asset_type": "scene_multiview_board",
                "scene_id": scene.scene_id,
            },
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

    @staticmethod
    def _invalidate_completed_nodes(state: ProjectState, node_names: set[str]) -> None:
        if not node_names:
            return
        state.completed_nodes = [
            node_name for node_name in state.completed_nodes if node_name not in node_names
        ]
        if state.current_node in node_names:
            state.current_node = state.completed_nodes[-1] if state.completed_nodes else None

    def _invalidate_audit_assets(
        self,
        project_dir: Path,
        node_name: str,
        asset_ids: set[str],
    ) -> None:
        if not asset_ids:
            return
        path = self.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            return
        try:
            output = ImageAssetAuditOutput.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.warning(
                "ignored incompatible %s output while invalidating assets %s: %s",
                node_name,
                ", ".join(sorted(asset_ids)),
                exc,
            )
            return
        retained = [item for item in output.audited_assets if item.asset_id not in asset_ids]
        if len(retained) != len(output.audited_assets):
            self.repo.write_json(
                path,
                ImageAssetAuditOutput(source_node=output.source_node, audited_assets=retained),
            )

    def _invalidate_background_dependents(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        shot_ids: set[str],
    ) -> None:
        """Drop stale downstream records when a background plate is regenerated."""
        if not shot_ids:
            return
        blocking_plan_path = self.layout.node_episode_output_path(
            project_dir, "shot_blocking_plan", episode_key
        )
        if blocking_plan_path.exists():
            try:
                output = ShotBlockingPlanEpisodeOutput.model_validate_json(
                    blocking_plan_path.read_text(encoding="utf-8")
                )
            except Exception:
                output = ShotBlockingPlanEpisodeOutput(episode_key=episode_key)
            self.repo.write_json(
                blocking_plan_path,
                ShotBlockingPlanEpisodeOutput(
                    episode_key=episode_key,
                    shots=[item for item in output.shots if item.shot_id not in shot_ids],
                ),
            )
        blocking_control_path = self.layout.node_episode_output_path(
            project_dir, "shot_blocking_control_render", episode_key
        )
        if blocking_control_path.exists():
            try:
                output = ShotBlockingControlEpisodeOutput.model_validate_json(
                    blocking_control_path.read_text(encoding="utf-8")
                )
            except Exception:
                output = ShotBlockingControlEpisodeOutput(episode_key=episode_key)
            self.repo.write_json(
                blocking_control_path,
                ShotBlockingControlEpisodeOutput(
                    episode_key=episode_key,
                    generated_controls=[
                        item
                        for item in output.generated_controls
                        if item.shot_id not in shot_ids
                    ],
                ),
            )
        prompt_path = self.layout.node_episode_output_path(project_dir, "shot_keyframe_prompt", episode_key)
        if prompt_path.exists():
            try:
                output = ShotKeyframePromptOutput.model_validate_json(
                    prompt_path.read_text(encoding="utf-8")
                )
            except Exception:
                output = ShotKeyframePromptOutput(prompts=[])
            self.repo.write_json(
                prompt_path,
                ShotKeyframePromptOutput(prompts=[item for item in output.prompts if item.shot_id not in shot_ids]),
            )
        stage_path = self.layout.node_episode_output_path(
            project_dir, "shot_keyframe_stage_generation", episode_key
        )
        if stage_path.exists():
            try:
                output = ShotKeyframeStageGenerationOutput.model_validate_json(
                    stage_path.read_text(encoding="utf-8")
                )
            except Exception:
                output = ShotKeyframeStageGenerationOutput(generated_stages=[])
            self.repo.write_json(
                stage_path,
                ShotKeyframeStageGenerationOutput(
                    generated_stages=[
                        item for item in output.generated_stages if item.shot_id not in shot_ids
                    ]
                ),
            )
        image_path = self.layout.node_episode_output_path(project_dir, "shot_keyframe_image_generation", episode_key)
        if image_path.exists():
            try:
                output = ShotKeyframeImageGenerationOutput.model_validate_json(
                    image_path.read_text(encoding="utf-8")
                )
            except Exception:
                output = ShotKeyframeImageGenerationOutput(generated_keyframes=[])
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
        self._invalidate_audit_assets(
            project_dir,
            "shot_background_image_audit",
            {f"{shot_id}_background" for shot_id in shot_ids},
        )
        self._invalidate_audit_assets(
            project_dir,
            "shot_keyframe_image_audit",
            {f"{shot_id}_keyframe" for shot_id in shot_ids},
        )
        self._invalidate_completed_nodes(
            state,
            {
                "shot_background_image_audit",
                "shot_blocking_plan",
                "shot_blocking_control_render",
                "shot_keyframe_prompt",
                "shot_keyframe_stage_generation",
                "shot_keyframe_image_generation",
                "shot_keyframe_image_audit",
                "shot_manifest_generation",
                *EXPECTED_OUTPUT_GENERATION_NODES,
                *EXPECTED_OUTPUT_POSTGEN_NODES,
            },
        )

    def _invalidate_scene_multiview_dependents(
        self,
        project_dir: Path,
        state: ProjectState,
        episode_key: str,
        shot_ids: set[str],
    ) -> None:
        if not shot_ids:
            return
        background_path = self.layout.node_episode_output_path(
            project_dir, "shot_background_image_generation", episode_key
        )
        if background_path.exists():
            try:
                output = ShotBackgroundImageGenerationEpisodeOutput.model_validate_json(
                    background_path.read_text(encoding="utf-8")
                )
            except Exception:
                output = ShotBackgroundImageGenerationEpisodeOutput(episode_key=episode_key)
            self.repo.write_json(
                background_path,
                ShotBackgroundImageGenerationEpisodeOutput(
                    episode_key=episode_key,
                    generated_backgrounds=[
                        item for item in output.generated_backgrounds if item.shot_id not in shot_ids
                    ],
                ),
            )
        self._invalidate_completed_nodes(state, {"shot_background_image_generation"})
        self._invalidate_background_dependents(
            project_dir, state, episode_key, shot_ids
        )

    @classmethod
    def _compile_model_output(
        cls,
        output: ClipToShotsModelOutput,
        assets: dict[str, dict[str, Any]],
        *,
        clip_id: str,
        clip_index: int,
        scene_id: str,
        reference_budget: int,
    ) -> list[ShotPlanItem]:
        if not output.shots:
            raise ValueError("clip_to_shots returned no shots")

        compiled: list[ShotPlanItem] = []
        for shot_index, item in enumerate(output.shots, start=1):
            item.video_prompt = cls._clean(item.video_prompt)
            item.ref_ids = [cls._clean(value) for value in item.ref_ids]
            if not item.video_prompt:
                raise ValueError("clip_to_shots requires a non-empty video_prompt")
            if cls._INTERNAL_CUT_RE.search(item.video_prompt):
                raise ValueError("clip_to_shots placed an internal cut or second setup inside one shot")
            if any(token in item.video_prompt.lower() for token in cls._FORBIDDEN):
                raise ValueError("clip_to_shots contains forbidden workflow/path terminology")
            if any(not value for value in item.ref_ids):
                raise ValueError("clip_to_shots ref_ids cannot contain empty IDs")
            if len(item.ref_ids) != len(set(item.ref_ids)):
                raise ValueError("clip_to_shots ref_ids must be unique within one shot")
            invalid_refs = sorted(
                ref_id
                for ref_id in item.ref_ids
                if ref_id not in assets or assets[ref_id]["kind"] not in {"roleboard", "prop"}
            )
            if invalid_refs:
                raise ValueError(
                    "clip_to_shots references unavailable foreground assets: "
                    + ", ".join(invalid_refs)
                )
            referenced_role_ids = [
                assets[ref_id]["object"][0].id
                for ref_id in item.ref_ids
                if assets[ref_id]["kind"] == "roleboard"
            ]
            if len(referenced_role_ids) != len(set(referenced_role_ids)):
                raise ValueError(
                    "clip_to_shots must select at most one appearance reference per visible character"
                )
            total_references = 1 + len(item.ref_ids)
            if total_references > reference_budget:
                raise ValueError(
                    f"{clip_id} uses {total_references} total references; "
                    f"provider budget is {reference_budget}"
                )

            compiled.append(
                ShotPlanItem(
                    shot_id=f"{clip_id}_shot_{shot_index:03d}",
                    clip_id=clip_id,
                    scene_id=scene_id,
                    clip_index=clip_index,
                    shot_index_in_clip=shot_index,
                    episode_shot_index=0,
                    ref_ids=item.ref_ids,
                    video_prompt=item.video_prompt,
                    duration_seconds=item.duration_seconds,
                )
            )
        return compiled

    def _reference_budget(self) -> int:
        params = getattr(self.repo.settings.nodes.get("clip_to_shots"), "params", {}) or {}
        try:
            return max(1, int(params.get("reference_budget", 4)))
        except (TypeError, ValueError):
            return 4

    @staticmethod
    def _visual_quality(state: ProjectState) -> str:
        value = str(state.metadata.get("visual_style_prompt") or "").strip()
        if not value:
            raise ValueError("project metadata is missing global visual style prompt")
        return value


class ClipToShotsNode(ShotAssetNodeBase):
    name = "clip_to_shots"

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
            reference_budget = self._reference_budget()
            for clip_index, (_, clip) in enumerate(source_clips, start=1):
                clip_id = f"{episode_key}_clip_{clip_index:03d}"
                if self._active_clip_selectors() and not clip_matches_selectors(episode_key, clip_id, clip_index, self._active_clip_selectors()):
                    if clip_id in existing_by_clip:
                        plans.append(existing_by_clip[clip_id])
                    continue
                entity_index, assets = self._asset_index(project_dir, state, clip=clip)
                scene_item = assets.get(clip.scene_id)
                if scene_item is None or scene_item["kind"] != "layout":
                    raise ValueError(f"{clip_id} references unavailable scene {clip.scene_id}")
                scene = scene_item["object"]
                scene_ref = self._asset_ref(project_dir, scene_item)
                if not (scene_ref.path or scene_ref.url):
                    raise ValueError(
                        f"{clip_id} scene {clip.scene_id} has no generated spatial-anchor image"
                    )
                scene_description = self._clean(
                    "; ".join(
                        value
                        for value in (
                            scene.desc or scene.name,
                            "fixed anchors: " + ", ".join(scene.space_features)
                            if scene.space_features
                            else "",
                            "visible state: " + scene.state_delta if scene.state_delta else "",
                        )
                        if value
                    )
                )
                previous = self._clean(source_clips[clip_index - 2][1].text)[-400:] if clip_index > 1 else ""
                following = self._clean(source_clips[clip_index][1].text)[:400] if clip_index < len(source_clips) else ""
                raw = await self.workflow.director_service.clip_to_shots(
                    state,
                    provider,
                    audit_asset_name=clip_id,
                    clip_text=self._visual_planning_text(clip.text),
                    scene_description=scene_description,
                    scene_ref=scene_ref,
                    entity_index=entity_index,
                    previous_context=previous,
                    next_context=following,
                    foreground_reference_budget=max(0, reference_budget - 1),
                )
                shots = self._compile_model_output(
                    raw,
                    assets,
                    clip_id=clip_id,
                    clip_index=clip_index,
                    scene_id=clip.scene_id,
                    reference_budget=reference_budget,
                )
                plans.append(
                    ClipShotPlan(
                        clip_id=clip_id,
                        clip_index=clip_index,
                        scene_id=clip.scene_id,
                        shots=shots,
                    )
                )
                state.budget.used_text_calls += 1
            counter = 0
            all_shots = [shot for plan in plans for shot in plan.shots]
            for plan in plans:
                for shot in plan.shots:
                    counter += 1
                    shot.episode_shot_index = counter
            output = ClipToShotsEpisodeOutput(
                episode_key=episode_key,
                clips=plans,
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
        self._invalidate_completed_nodes(
            state,
            {
                *SHOT_ASSET_NODE_NAMES[1:],
                "shot_background_image_audit",
                "shot_keyframe_image_audit",
                *EXPECTED_OUTPUT_GENERATION_NODES,
                *EXPECTED_OUTPUT_POSTGEN_NODES,
            },
        )
        return state


class SceneMultiviewPlanNode(ShotAssetNodeBase):
    name = "scene_multiview_plan"

    @staticmethod
    def _compile_plan(
        response: SceneMultiviewPlanModelOutput,
        *,
        episode_key: str,
        scene_id: str,
        shots: list[ShotPlanItem],
        fingerprint: str,
    ) -> SceneMultiviewPlanItem:
        if len(response.views) != 4:
            raise ValueError("scene_multiview_plan must return exactly four complementary views")
        view_indices = [item.view_index for item in response.views]
        if sorted(view_indices) != [1, 2, 3, 4] or len(set(view_indices)) != 4:
            raise ValueError("scene_multiview_plan view_index values must be exactly 1, 2, 3, 4")
        expected_shot_indices = set(range(1, len(shots) + 1))
        assigned_shot_indices = [item.shot_index for item in response.assignments]
        if (
            set(assigned_shot_indices) != expected_shot_indices
            or len(assigned_shot_indices) != len(set(assigned_shot_indices))
        ):
            raise ValueError("scene_multiview_plan must assign every scene shot exactly once")
        view_id_by_index = {
            index: f"{scene_id}_view_{index:02d}" for index in view_indices
        }
        shot_ids_by_view: dict[int, list[str]] = {index: [] for index in view_indices}
        assignments: list[SceneMultiviewShotAssignment] = []
        for assignment in sorted(response.assignments, key=lambda item: item.shot_index):
            if assignment.primary_view_index not in view_id_by_index:
                raise ValueError("scene_multiview_plan assignment references an unknown primary view")
            invalid_secondary = [
                index
                for index in assignment.secondary_view_indices
                if index not in view_id_by_index or index == assignment.primary_view_index
            ]
            if invalid_secondary or len(assignment.secondary_view_indices) != len(
                set(assignment.secondary_view_indices)
            ):
                raise ValueError("scene_multiview_plan has an invalid secondary view assignment")
            shot = shots[assignment.shot_index - 1]
            shot_ids_by_view[assignment.primary_view_index].append(shot.shot_id)
            assignments.append(
                SceneMultiviewShotAssignment(
                    shot_id=shot.shot_id,
                    primary_view_id=view_id_by_index[assignment.primary_view_index],
                    secondary_view_ids=[
                        view_id_by_index[index]
                        for index in assignment.secondary_view_indices
                    ],
                )
            )
        views = [
            SceneMultiviewViewPlanItem(
                view_id=view_id_by_index[item.view_index],
                view_index=item.view_index,
                camera_description=" ".join(item.camera_description.split()),
                visible_anchors=[" ".join(value.split()) for value in item.visible_anchors if value.strip()],
                shot_ids=shot_ids_by_view[item.view_index],
            )
            for item in sorted(response.views, key=lambda item: item.view_index)
        ]
        if any(not view.camera_description for view in views):
            raise ValueError("scene_multiview_plan returned an empty camera description")
        return SceneMultiviewPlanItem(
            episode_key=episode_key,
            scene_id=scene_id,
            layout_id=scene_id,
            views=views,
            assignments=assignments,
            fingerprint=fingerprint,
        )

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        all_outputs: list[SceneMultiviewPlanEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            shot_plan = self._load_episode_output(
                project_dir,
                "clip_to_shots",
                episode_key,
                ClipToShotsEpisodeOutput,
            )
            rows = [shot for clip in shot_plan.clips for shot in clip.shots]
            selected_scene_ids = {shot.scene_id for shot in self._selected_shots(project_dir, shot_plan)}
            shots_by_scene: dict[str, list[ShotPlanItem]] = {}
            for shot in rows:
                shots_by_scene.setdefault(shot.scene_id, []).append(shot)
            assets = self._all_assets(project_dir, state)
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = SceneMultiviewPlanEpisodeOutput(episode_key=episode_key)
            if path.exists():
                try:
                    existing = SceneMultiviewPlanEpisodeOutput.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except ValidationError:
                    existing = SceneMultiviewPlanEpisodeOutput(episode_key=episode_key)
            by_scene = {item.scene_id: item for item in existing.scenes}
            regenerated_scene_ids: set[str] = set()
            for scene_id, scene_shots in shots_by_scene.items():
                if scene_id not in selected_scene_ids:
                    continue
                _scene_id, scene_item = self._shot_scene(scene_shots[0], assets)
                scene = scene_item["object"]
                scene_ref = self._asset_ref(project_dir, scene_item)
                if not (scene_ref.path or scene_ref.url):
                    raise ValueError(f"scene {scene_id} has no usable spatial-anchor image")
                request_prompt = self.workflow.prompts.render(
                    self.name,
                    scene_description=self._clean(scene.desc or scene.name),
                    fixed_anchors=json.dumps(list(scene.space_features), ensure_ascii=False),
                    shots=json.dumps(
                        [
                            {"shot_index": index, "video_prompt": shot.video_prompt}
                            for index, shot in enumerate(scene_shots, start=1)
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
                fingerprint = self._generation_fingerprint(request_prompt, [scene_ref], provider)
                old = by_scene.get(scene_id)
                if old and not self._force(self.workflow) and old.fingerprint == fingerprint:
                    continue
                response = await provider.generate_json(
                    request_prompt,
                    SceneMultiviewPlanModelOutput,
                    temperature=0.2,
                    refs=[scene_ref],
                    metadata={
                        "node_name": self.name,
                        "prompt_asset_type": self.name,
                        "prompt_asset_name": scene_id,
                        "scene_id": scene_id,
                    },
                )
                state.budget.used_text_calls += 1
                by_scene[scene_id] = self._compile_plan(
                    response,
                    episode_key=episode_key,
                    scene_id=scene_id,
                    shots=scene_shots,
                    fingerprint=fingerprint,
                )
                regenerated_scene_ids.add(scene_id)
            output = SceneMultiviewPlanEpisodeOutput(
                episode_key=episode_key,
                scenes=[by_scene[scene_id] for scene_id in shots_by_scene if scene_id in by_scene],
            )
            self.repo.write_json(path, output)
            if regenerated_scene_ids:
                affected_shot_ids = {
                    shot.shot_id
                    for clip in shot_plan.clips
                    for shot in clip.shots
                    if shot.scene_id in regenerated_scene_ids
                }
                self._invalidate_scene_multiview_dependents(
                    project_dir,
                    state,
                    episode_key,
                    affected_shot_ids,
                )
                self._invalidate_completed_nodes(
                    state,
                    {
                        "scene_multiview_image_generation",
                        "layout_to_background_prompt",
                        "shot_background_image_generation",
                        "shot_blocking_plan",
                        "shot_blocking_control_render",
                        "shot_keyframe_prompt",
                        "shot_keyframe_stage_generation",
                        "shot_keyframe_image_generation",
                        "shot_manifest_generation",
                        *EXPECTED_OUTPUT_GENERATION_NODES,
                        *EXPECTED_OUTPUT_POSTGEN_NODES,
                    },
                )
            all_outputs.append(output)
        self.repo.save_node_output(
            project_dir,
            self.name,
            SceneMultiviewPlanOutput(episodes=all_outputs),
        )
        return state


class SceneMultiviewImageGenerationNode(ShotAssetNodeBase):
    name = "scene_multiview_image_generation"

    def _scene_item_reusable(
        self,
        project_dir: Path,
        item: SceneMultiviewImageGenerationItem | None,
        plan_item: SceneMultiviewPlanItem,
        fingerprint: str,
    ) -> bool:
        expected_views = {
            (view.view_index, view.view_id)
            for view in plan_item.views
        }
        return bool(
            item
            and item.fingerprint == fingerprint
            and item.episode_key == plan_item.episode_key
            and item.scene_id == plan_item.scene_id
            and item.layout_id == plan_item.layout_id
            and item.plan_fingerprint == plan_item.fingerprint
            and {
                (view.view_index, view.view_id)
                for view in item.views
            }
            == expected_views
            and self.layout.existing_project_file(project_dir, item.board_asset_path)
            and all(
                self.layout.existing_project_file(project_dir, view.asset_path)
                for view in item.views
            )
        )

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("shot", node_name=self.name)
        if not bool(getattr(provider, "supports_reference_images", True)):
            raise ValueError("scene_multiview_image_generation requires a reference-image provider")
        all_outputs: list[SceneMultiviewImageGenerationEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            shot_plan = self._load_episode_output(
                project_dir,
                "clip_to_shots",
                episode_key,
                ClipToShotsEpisodeOutput,
            )
            multiview_plan = self._load_episode_output(
                project_dir,
                "scene_multiview_plan",
                episode_key,
                SceneMultiviewPlanEpisodeOutput,
            )
            selected_scene_ids = {shot.scene_id for shot in self._selected_shots(project_dir, shot_plan)}
            assets = self._all_assets(project_dir, state)
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = SceneMultiviewImageGenerationEpisodeOutput(episode_key=episode_key)
            if path.exists():
                try:
                    existing = SceneMultiviewImageGenerationEpisodeOutput.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except ValidationError:
                    existing = SceneMultiviewImageGenerationEpisodeOutput(episode_key=episode_key)
            by_scene = {item.scene_id: item for item in existing.generated_scenes}
            regenerated_scene_ids: set[str] = set()
            for plan_item in multiview_plan.scenes:
                if plan_item.scene_id not in selected_scene_ids:
                    continue
                scene_item = assets.get(plan_item.scene_id)
                if scene_item is None or scene_item["kind"] != "layout":
                    raise ValueError(f"unknown scene layout {plan_item.scene_id}")
                layout_ref = self._asset_ref(project_dir, scene_item)
                view_plan = [
                    {
                        "panel": view.view_index,
                        "camera": view.camera_description,
                        "visible_anchors": view.visible_anchors,
                    }
                    for view in plan_item.views
                ]
                prompt = self.workflow.prompts.render(
                    self.name,
                    scene_description=self._clean(
                        scene_item["object"].desc or scene_item["object"].name
                    ),
                    visual_quality=self._visual_quality(state),
                    view_plan=json.dumps(view_plan, ensure_ascii=False, indent=2),
                )
                fingerprint = self._generation_fingerprint(prompt, [layout_ref], provider)
                old = by_scene.get(plan_item.scene_id)
                if (
                    not self._force(self.workflow)
                    and self._scene_item_reusable(project_dir, old, plan_item, fingerprint)
                ):
                    continue
                result, final_prompt, _ = await self._generate_image_with_safety_prompt_rewrites(
                    provider=provider,
                    state=state,
                    node_name=self.name,
                    asset_id=f"{plan_item.scene_id}_multiview_board",
                    prompt=prompt,
                    refs=[layout_ref],
                    metadata={
                        "node_name": self.name,
                        "prompt_asset_type": "scene_multiview_board",
                        "prompt_asset_name": plan_item.scene_id,
                        "scene_id": plan_item.scene_id,
                        "aspectRatio": "16:9",
                        "imageSize": "4K",
                    },
                    context={"scene_description": self._clean(scene_item["object"].desc)},
                )
                board_path = await self.media_store.write_first_generated_image(
                    project_dir,
                    self.layout.image_asset_path(
                        project_dir,
                        "scene_multiview_boards",
                        f"{plan_item.scene_id}_multiview_board",
                    ),
                    result,
                )
                crops = crop_scene_multiview_board(
                    project_dir / board_path,
                    project_dir / "assets" / "images" / "scene_multiview_views",
                    output_stem=plan_item.scene_id,
                )
                plan_view_by_index = {view.view_index: view for view in plan_item.views}
                generated_views = [
                    SceneMultiviewGeneratedViewItem(
                        view_id=plan_view_by_index[crop.view_index].view_id,
                        view_index=crop.view_index,
                        camera_description=plan_view_by_index[crop.view_index].camera_description,
                        asset_path=self.layout.project_relative(project_dir, crop.asset_path),
                        fingerprint=crop.content_fingerprint,
                    )
                    for crop in crops.views
                ]
                by_scene[plan_item.scene_id] = SceneMultiviewImageGenerationItem(
                    episode_key=episode_key,
                    scene_id=plan_item.scene_id,
                    layout_id=plan_item.layout_id,
                    plan_fingerprint=plan_item.fingerprint,
                    prompt=final_prompt,
                    fingerprint=fingerprint,
                    board_asset_id=f"{plan_item.scene_id}_multiview_board",
                    board_asset_path=board_path,
                    board_asset_url=self.first_image_url(result),
                    views=generated_views,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                )
                regenerated_scene_ids.add(plan_item.scene_id)
                partial = SceneMultiviewImageGenerationEpisodeOutput(
                    episode_key=episode_key,
                    generated_scenes=[
                        by_scene[item.scene_id]
                        for item in multiview_plan.scenes
                        if item.scene_id in by_scene
                    ],
                )
                self.repo.write_json(path, partial)
            missing = selected_scene_ids.difference(by_scene)
            if missing:
                raise ValueError(
                    "scene_multiview_image_generation missing scenes: "
                    + ", ".join(sorted(missing))
                )
            output = SceneMultiviewImageGenerationEpisodeOutput(
                episode_key=episode_key,
                generated_scenes=[
                    by_scene[item.scene_id]
                    for item in multiview_plan.scenes
                    if item.scene_id in by_scene
                ],
            )
            self.repo.write_json(path, output)
            if regenerated_scene_ids:
                affected_shot_ids = {
                    shot.shot_id
                    for clip in shot_plan.clips
                    for shot in clip.shots
                    if shot.scene_id in regenerated_scene_ids
                }
                self._invalidate_scene_multiview_dependents(
                    project_dir,
                    state,
                    episode_key,
                    affected_shot_ids,
                )
                self._invalidate_completed_nodes(
                    state,
                    {
                        "layout_to_background_prompt",
                        "shot_background_image_generation",
                        "shot_blocking_plan",
                        "shot_blocking_control_render",
                        "shot_keyframe_prompt",
                        "shot_keyframe_stage_generation",
                        "shot_keyframe_image_generation",
                        "shot_manifest_generation",
                        *EXPECTED_OUTPUT_GENERATION_NODES,
                        *EXPECTED_OUTPUT_POSTGEN_NODES,
                    },
                )
            all_outputs.append(output)
        self.repo.save_node_output(
            project_dir,
            self.name,
            SceneMultiviewImageGenerationOutput(episodes=all_outputs),
        )
        return state


class LayoutToBackgroundPromptNode(ShotAssetNodeBase):
    name = "layout_to_background_prompt"

    def _existing(self, project_dir: Path, episode_key: str) -> LayoutToBackgroundPromptEpisodeOutput:
        path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
        if not path.exists():
            return LayoutToBackgroundPromptEpisodeOutput(episode_key=episode_key)
        try:
            return LayoutToBackgroundPromptEpisodeOutput.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except Exception:
            return LayoutToBackgroundPromptEpisodeOutput(episode_key=episode_key)

    def _validate_background_plan(
        self,
        output: LayoutBackgroundPromptModelOutput,
        *,
        expected_indices: set[int],
    ) -> list[tuple[int, Any]]:
        if not output.root:
            raise ValueError("layout_to_background_prompt returned no backgrounds")
        parsed: list[tuple[int, Any]] = []
        local_keys: list[int] = []
        assigned: list[int] = []
        for key, item in output.root.items():
            match = self._LOCAL_BACKGROUND_KEY.fullmatch(str(key))
            if not match:
                raise ValueError(f"Invalid local background ID: {key}")
            local_keys.append(int(match.group(1)))
            item.prompt_content = self._clean(item.prompt_content)
            item.description = self._clean(item.description)
            if not item.prompt_content or not item.description:
                raise ValueError(f"Background {key} has empty prompt_content or description")
            if item.shot_index not in expected_indices:
                raise ValueError(f"Background {key} maps an unknown local shot index")
            assigned.append(item.shot_index)
            parsed.append((item.shot_index, item))
        if sorted(local_keys) != list(range(1, len(local_keys) + 1)):
            raise ValueError("Background keys must be continuously numbered from 1")
        if set(assigned) != expected_indices or len(assigned) != len(set(assigned)):
            raise ValueError("Each target shot must map to exactly one background")
        return sorted(parsed, key=lambda pair: pair[0])

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        outputs: list[LayoutToBackgroundPromptEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput)
            multiview_plan = self._load_episode_output(
                project_dir,
                "scene_multiview_plan",
                episode_key,
                SceneMultiviewPlanEpisodeOutput,
            )
            view_assignment_by_shot = {
                assignment.shot_id: assignment
                for scene in multiview_plan.scenes
                for assignment in scene.assignments
            }
            targets = self._selected_shots(project_dir, plan)
            assets = self._all_assets(project_dir, state)
            existing = self._existing(project_dir, episode_key)
            target_ids = {shot.shot_id for shot in targets}
            retained = [
                item
                for item in existing.backgrounds
                if item.shot_id not in target_ids
            ]
            by_shot: dict[str, ShotBackgroundPromptItem] = {
                item.shot_id: item for item in retained
            }
            by_scene: dict[str, list[ShotPlanItem]] = {}
            for shot in targets:
                scene_id, _ = self._shot_scene(shot, assets)
                by_scene.setdefault(scene_id, []).append(shot)
            for scene_id, scene_shots in by_scene.items():
                scene_item = assets[scene_id]
                scene = scene_item["object"]
                scene_ref = self._asset_ref(project_dir, scene_item)
                if not (scene_ref.path or scene_ref.url):
                    raise ValueError(f"scene {scene_id} has no usable spatial-anchor image")
                shot_rows = [
                    {
                        "index": index,
                        "video_prompt": shot.video_prompt,
                    }
                    for index, shot in enumerate(scene_shots, start=1)
                ]
                request_prompt = self.workflow.prompts.render(
                    self.name,
                    scene_description=self._clean(scene.desc or scene.name),
                    visual_quality=self._visual_quality(state),
                    shots=json.dumps(shot_rows, ensure_ascii=False, indent=2),
                )
                response = await provider.generate_json(
                    request_prompt,
                    LayoutBackgroundPromptModelOutput,
                    temperature=0.25,
                    refs=[scene_ref],
                    metadata={
                        "node_name": self.name,
                        "prompt_asset_type": self.name,
                        "prompt_asset_name": scene.id,
                        "scene_id": scene.id,
                    },
                )
                state.budget.used_text_calls += 1
                validated = self._validate_background_plan(
                    response,
                    expected_indices=set(range(1, len(scene_shots) + 1)),
                )
                for shot_index, item in validated:
                    assigned_shot = scene_shots[shot_index - 1]
                    view_assignment = view_assignment_by_shot.get(assigned_shot.shot_id)
                    if view_assignment is None:
                        raise ValueError(
                            f"layout_to_background_prompt has no scene view assignment for "
                            f"{assigned_shot.shot_id}"
                        )
                    reference_guide = (
                        "Image 1 = 与目标机位最接近的场景母版视角；"
                        "Image 2 = 场景空间布局与拓扑锚点。"
                    )
                    if view_assignment.secondary_view_ids:
                        reference_guide += " Image 3 = 与目标机位高重叠的辅助母版视角。"
                    final_prompt = self.workflow.prompts.render_final(
                        self.name,
                        item.prompt_content,
                        {
                            "visual_quality": self._visual_quality(state),
                            "reference_guide": reference_guide,
                        },
                    )
                    by_shot[assigned_shot.shot_id] = ShotBackgroundPromptItem(
                        background_id=f"{assigned_shot.shot_id}_background",
                        scene_id=scene_id,
                        shot_id=assigned_shot.shot_id,
                        primary_view_id=view_assignment.primary_view_id,
                        secondary_view_ids=view_assignment.secondary_view_ids,
                        description=item.description,
                        prompt=final_prompt,
                    )
            missing_targets = target_ids.difference(by_shot)
            if missing_targets:
                raise ValueError(
                    "layout_to_background_prompt missed shots: "
                    + ", ".join(sorted(missing_targets))
                )
            all_shots = [shot for clip in plan.clips for shot in clip.shots]
            backgrounds = [by_shot[shot.shot_id] for shot in all_shots if shot.shot_id in by_shot]
            output = LayoutToBackgroundPromptEpisodeOutput(episode_key=episode_key, backgrounds=backgrounds)
            self.repo.write_json(self.layout.node_episode_output_path(project_dir, self.name, episode_key), output)
            outputs.append(output)
        self.repo.save_node_output(project_dir, self.name, LayoutToBackgroundPromptOutput(episodes=outputs))
        self._invalidate_completed_nodes(
            state,
            {
                "shot_background_image_generation",
                "shot_blocking_plan",
                "shot_blocking_control_render",
                "shot_keyframe_prompt",
                "shot_keyframe_stage_generation",
                "shot_keyframe_image_generation",
                "shot_manifest_generation",
                *EXPECTED_OUTPUT_GENERATION_NODES,
                *EXPECTED_OUTPUT_POSTGEN_NODES,
            },
        )
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
            prompt_output = self._load_episode_output(
                project_dir,
                "layout_to_background_prompt",
                episode_key,
                LayoutToBackgroundPromptEpisodeOutput,
            )
            multiview_output = self._load_episode_output(
                project_dir,
                "scene_multiview_image_generation",
                episode_key,
                SceneMultiviewImageGenerationEpisodeOutput,
            )
            selected_ids = {shot.shot_id for shot in self._selected_shots(project_dir, plan)}
            shots_by_id = {shot.shot_id: shot for clip in plan.clips for shot in clip.shots}
            multiview_by_scene = {
                item.scene_id: item for item in multiview_output.generated_scenes
            }
            assets = self._all_assets(project_dir, state)
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotBackgroundImageGenerationEpisodeOutput(episode_key=episode_key)
            if path.exists():
                try:
                    existing = ShotBackgroundImageGenerationEpisodeOutput.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except Exception:
                    existing = ShotBackgroundImageGenerationEpisodeOutput(episode_key=episode_key)
            by_id = {item.background_id: item for item in existing.generated_backgrounds}
            targets = [item for item in prompt_output.backgrounds if item.shot_id in selected_ids]
            semaphore = asyncio.Semaphore(self._concurrency())

            def generation_refs(item: ShotBackgroundPromptItem) -> list[AssetRef]:
                shot = shots_by_id.get(item.shot_id)
                if shot is None:
                    raise ValueError(f"Background prompt references unknown shot {item.shot_id}")
                if shot.scene_id != item.scene_id:
                    raise ValueError(
                        f"Background {item.background_id} scene mismatch: {item.scene_id} != {shot.scene_id}"
                    )
                _scene_id, scene_item = self._shot_scene(shot, assets)
                scene_multiview = multiview_by_scene.get(shot.scene_id)
                if scene_multiview is None:
                    raise ValueError(f"Missing scene multiview package for {shot.scene_id}")
                views_by_id = {view.view_id: view for view in scene_multiview.views}
                primary = views_by_id.get(item.primary_view_id)
                if primary is None:
                    raise ValueError(
                        f"Background {item.background_id} references missing primary view "
                        f"{item.primary_view_id}"
                    )
                secondary = []
                for view_id in item.secondary_view_ids:
                    view = views_by_id.get(view_id)
                    if view is None:
                        raise ValueError(
                            f"Background {item.background_id} references missing secondary view {view_id}"
                        )
                    secondary.append(self._scene_multiview_asset_ref(project_dir, scene_multiview, view))
                return [
                    self._scene_multiview_asset_ref(project_dir, scene_multiview, primary),
                    self._asset_ref(project_dir, scene_item),
                    *secondary,
                ]

            async def generate(
                item: ShotBackgroundPromptItem,
            ) -> tuple[ShotBackgroundImageGenerationItem, bool]:
                shot = shots_by_id[item.shot_id]
                scene_multiview = multiview_by_scene[shot.scene_id]
                refs = generation_refs(item)
                fingerprint = self._generation_fingerprint(item.prompt, refs, provider)
                old = by_id.get(item.background_id)
                if (
                    old
                    and not self._force(self.workflow)
                    and old.fingerprint == fingerprint
                    and self.layout.existing_project_file(project_dir, old.asset_path)
                ):
                    return old, False
                async with semaphore:
                    result, final_prompt, _ = await self._generate_image_with_safety_prompt_rewrites(
                        provider=provider,
                        state=state,
                        node_name=self.name,
                        asset_id=item.background_id,
                        prompt=item.prompt,
                        refs=refs,
                        metadata={
                            "node_name": self.name,
                            "prompt_asset_type": "shot_background",
                            "prompt_asset_name": item.background_id,
                            "background_id": item.background_id,
                            "shot_id": item.shot_id,
                            "scene_id": item.scene_id,
                            "aspectRatio": "16:9",
                            "imageSize": "4K",
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
                    episode_key=episode_key,
                    clip_id=shot.clip_id,
                    shot_id=shot.shot_id,
                    scene_id=shot.scene_id,
                    primary_view_id=item.primary_view_id,
                    view_ids=[item.primary_view_id, *item.secondary_view_ids],
                    scene_multiview_fingerprint=scene_multiview.fingerprint,
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
                ), True

            regenerated_shot_ids: set[str] = set()
            tasks = [asyncio.create_task(generate(item)) for item in targets]
            for completed in asyncio.as_completed(tasks):
                generated_item, regenerated = await completed
                by_id[generated_item.background_id] = generated_item
                if regenerated:
                    regenerated_shot_ids.add(generated_item.shot_id)
                self.repo.write_json(
                    path,
                    ShotBackgroundImageGenerationEpisodeOutput(
                        episode_key=episode_key,
                        generated_backgrounds=[
                            by_id[item.background_id]
                            for item in prompt_output.backgrounds
                            if item.background_id in by_id
                        ],
                    ),
                )
            required_ids = {item.background_id for item in prompt_output.backgrounds}
            missing = required_ids.difference(by_id)
            if missing:
                raise ValueError(
                    "shot_background_image_generation missing backgrounds: "
                    + ", ".join(sorted(missing))
                )
            output = ShotBackgroundImageGenerationEpisodeOutput(
                episode_key=episode_key,
                generated_backgrounds=[by_id[item.background_id] for item in prompt_output.backgrounds],
            )
            self.repo.write_json(path, output)
            self._invalidate_background_dependents(
                project_dir,
                state,
                episode_key,
                regenerated_shot_ids,
            )
            all_outputs.append(output)
        self.repo.save_node_output(
            project_dir,
            self.name,
            ShotBackgroundImageGenerationOutput(episodes=all_outputs),
        )
        return state


class ShotBlockingPlanNode(ShotAssetNodeBase):
    name = "shot_blocking_plan"

    @staticmethod
    def _scene_bindings(
        scene_shots: list[ShotPlanItem],
        assets: dict[str, dict[str, Any]],
    ) -> tuple[list[ShotBlockingBinding], dict[str, str]]:
        ordered_ref_ids = list(
            dict.fromkeys(ref_id for shot in scene_shots for ref_id in shot.ref_ids)
        )
        role_index = 0
        prop_index = 0
        bindings: list[ShotBlockingBinding] = []
        binding_by_ref_id: dict[str, str] = {}
        for ref_id in ordered_ref_ids:
            item = assets.get(ref_id)
            if item is None or item["kind"] not in {"roleboard", "prop"}:
                raise ValueError(f"shot_blocking_plan has unresolved foreground ref_id {ref_id}")
            if item["kind"] == "roleboard":
                role_index += 1
                binding_id = f"S{role_index}"
            else:
                prop_index += 1
                binding_id = f"P{prop_index}"
            bindings.append(
                ShotBlockingBinding(
                    binding_id=binding_id,
                    ref_id=ref_id,
                    asset_kind=item["kind"],
                    label=str(item["label"]),
                )
            )
            binding_by_ref_id[ref_id] = binding_id
        return bindings, binding_by_ref_id

    @staticmethod
    def _shot_fingerprint(
        *,
        scene_fingerprint: str,
        shot: ShotPlanItem,
        background: ShotBackgroundImageGenerationItem,
        bindings: list[ShotBlockingBinding],
        model_item: Any,
    ) -> str:
        payload = {
            "scene_fingerprint": scene_fingerprint,
            "shot_id": shot.shot_id,
            "background_fingerprint": background.fingerprint,
            "bindings": [binding.model_dump(mode="json") for binding in bindings],
            "composition_intent": model_item.composition_intent,
            "placements": [placement.model_dump(mode="json") for placement in model_item.placements],
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        ).hexdigest()

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        all_outputs: list[ShotBlockingPlanEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(
                project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput
            )
            backgrounds = self._load_episode_output(
                project_dir,
                "shot_background_image_generation",
                episode_key,
                ShotBackgroundImageGenerationEpisodeOutput,
            )
            multiview = self._load_episode_output(
                project_dir,
                "scene_multiview_image_generation",
                episode_key,
                SceneMultiviewImageGenerationEpisodeOutput,
            )
            rows = [shot for clip in plan.clips for shot in clip.shots]
            selected_scene_ids = {shot.scene_id for shot in self._selected_shots(project_dir, plan)}
            shots_by_scene: dict[str, list[ShotPlanItem]] = {}
            for shot in rows:
                shots_by_scene.setdefault(shot.scene_id, []).append(shot)
            background_by_shot = {
                item.shot_id: item for item in backgrounds.generated_backgrounds
            }
            multiview_by_scene = {item.scene_id: item for item in multiview.generated_scenes}
            assets = self._all_assets(project_dir, state)
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotBlockingPlanEpisodeOutput(episode_key=episode_key)
            if path.exists():
                existing = ShotBlockingPlanEpisodeOutput.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            by_shot = {item.shot_id: item for item in existing.shots}
            regenerated_shot_ids: set[str] = set()
            for scene_id, scene_shots in shots_by_scene.items():
                if scene_id not in selected_scene_ids:
                    continue
                scene_multiview = multiview_by_scene.get(scene_id)
                if scene_multiview is None:
                    raise ValueError(f"shot_blocking_plan missing multiview package for {scene_id}")
                bindings, binding_by_ref_id = self._scene_bindings(scene_shots, assets)
                bindings_by_id = {item.binding_id: item for item in bindings}
                scene_rows: list[dict[str, Any]] = []
                for index, shot in enumerate(scene_shots, start=1):
                    background = background_by_shot.get(shot.shot_id)
                    if background is None:
                        raise ValueError(
                            f"shot_blocking_plan missing generated background for {shot.shot_id}"
                        )
                    scene_rows.append(
                        {
                            "shot_index": index,
                            "video_prompt": shot.video_prompt,
                            "primary_scene_view": background.primary_view_id,
                            "active_bindings": [
                                {
                                    "binding_id": binding_by_ref_id[ref_id],
                                    "asset_kind": assets[ref_id]["kind"],
                                    "identity": str(assets[ref_id]["label"]),
                                }
                                for ref_id in shot.ref_ids
                            ],
                        }
                    )
                request_prompt = self.workflow.prompts.render(
                    self.name,
                    binding_catalog=json.dumps(
                        [item.model_dump(mode="json") for item in bindings],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    shots=json.dumps(scene_rows, ensure_ascii=False, indent=2),
                )
                board_ref = self._scene_multiview_board_ref(project_dir, scene_multiview)
                scene_fingerprint = self._generation_fingerprint(
                    request_prompt,
                    [
                        board_ref,
                        *[
                            self._background_asset_ref(project_dir, background_by_shot[shot.shot_id])
                            for shot in scene_shots
                        ],
                    ],
                    provider,
                )
                reusable = all(
                    (old := by_shot.get(shot.shot_id)) is not None
                    and old.input_fingerprint
                    == hashlib.sha256(
                        f"{scene_fingerprint}:{shot.shot_id}:{background_by_shot[shot.shot_id].fingerprint}".encode(
                            "utf-8"
                        )
                    ).hexdigest()
                    for shot in scene_shots
                )
                if reusable and not self._force(self.workflow):
                    continue
                response = await provider.generate_json(
                    request_prompt,
                    ShotBlockingPlanModelOutput,
                    temperature=0.25,
                    refs=[board_ref],
                    metadata={
                        "node_name": self.name,
                        "prompt_asset_type": self.name,
                        "prompt_asset_name": scene_id,
                        "scene_id": scene_id,
                    },
                )
                state.budget.used_text_calls += 1
                expected_indices = set(range(1, len(scene_shots) + 1))
                actual_indices = [item.shot_index for item in response.shots]
                if (
                    set(actual_indices) != expected_indices
                    or len(actual_indices) != len(set(actual_indices))
                ):
                    raise ValueError("shot_blocking_plan must return every scene shot exactly once")
                response_by_index = {item.shot_index: item for item in response.shots}
                for index, shot in enumerate(scene_shots, start=1):
                    item = response_by_index[index]
                    active_role_bindings = {
                        binding_by_ref_id[ref_id]
                        for ref_id in shot.ref_ids
                        if assets[ref_id]["kind"] == "roleboard"
                    }
                    placement_ids = [placement.binding_id for placement in item.placements]
                    if (
                        set(placement_ids) != active_role_bindings
                        or len(placement_ids) != len(set(placement_ids))
                    ):
                        raise ValueError(
                            f"shot_blocking_plan placements must match visible role bindings for {shot.shot_id}"
                        )
                    for placement in item.placements:
                        if placement.binding_id not in bindings_by_id:
                            raise ValueError(
                                f"shot_blocking_plan references unknown binding {placement.binding_id}"
                            )
                        if abs(placement.facing_x) + abs(placement.facing_y) <= 1e-6:
                            raise ValueError(
                                f"shot_blocking_plan returned zero facing vector for {shot.shot_id}"
                            )
                        left = placement.center_x - placement.width / 2
                        top = placement.ground_y - placement.height
                        if left < 0 or top < 0 or left + placement.width > 1 or placement.ground_y > 1:
                            raise ValueError(
                                f"shot_blocking_plan placement is outside the normalized frame for {shot.shot_id}"
                            )
                        invalid_occlusions = set(placement.occludes_binding_ids).difference(
                            active_role_bindings
                        )
                        if invalid_occlusions:
                            raise ValueError(
                                f"shot_blocking_plan has invalid occlusion bindings for {shot.shot_id}"
                            )
                    background = background_by_shot[shot.shot_id]
                    shot_bindings = [
                        binding
                        for binding in bindings
                        if binding.ref_id in shot.ref_ids
                    ]
                    exact_fingerprint = self._shot_fingerprint(
                        scene_fingerprint=scene_fingerprint,
                        shot=shot,
                        background=background,
                        bindings=shot_bindings,
                        model_item=item,
                    )
                    # The stable seed ties the item to scene/background inputs before model content;
                    # the exact content remains in prompt provenance and downstream fingerprints.
                    stable_input_fingerprint = hashlib.sha256(
                        f"{scene_fingerprint}:{shot.shot_id}:{background.fingerprint}".encode("utf-8")
                    ).hexdigest()
                    by_shot[shot.shot_id] = ShotBlockingPlanItem(
                        episode_key=episode_key,
                        clip_id=shot.clip_id,
                        shot_id=shot.shot_id,
                        scene_id=shot.scene_id,
                        background_id=background.background_id,
                        bindings=shot_bindings,
                        placements=item.placements,
                        composition_intent=item.composition_intent,
                        input_fingerprint=stable_input_fingerprint,
                        fingerprint=exact_fingerprint,
                    )
                    regenerated_shot_ids.add(shot.shot_id)
            output = ShotBlockingPlanEpisodeOutput(
                episode_key=episode_key,
                shots=[by_shot[shot.shot_id] for shot in rows if shot.shot_id in by_shot],
            )
            self.repo.write_json(path, output)
            if regenerated_shot_ids:
                self._invalidate_completed_nodes(
                    state,
                    {
                        "shot_blocking_control_render",
                        "shot_keyframe_prompt",
                        "shot_keyframe_stage_generation",
                        "shot_keyframe_image_generation",
                        "shot_manifest_generation",
                        *EXPECTED_OUTPUT_GENERATION_NODES,
                        *EXPECTED_OUTPUT_POSTGEN_NODES,
                    },
                )
            all_outputs.append(output)
        self.repo.save_node_output(
            project_dir, self.name, ShotBlockingPlanOutput(episodes=all_outputs)
        )
        return state


class ShotBlockingControlRenderNode(ShotAssetNodeBase):
    name = "shot_blocking_control_render"
    RENDERER_VERSION = "shot-blocking-control-v1"

    @staticmethod
    def _binding_color(binding_id: str) -> tuple[int, int, int, int]:
        digest = hashlib.sha256(binding_id.encode("utf-8")).digest()
        hue = int.from_bytes(digest[:2], "big") / 65535.0
        red, green, blue = colorsys.hsv_to_rgb(hue, 0.72, 1.0)
        return int(red * 255), int(green * 255), int(blue * 255), 255

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        all_outputs: list[ShotBlockingControlEpisodeOutput] = []
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(
                project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput
            )
            blocking = self._load_episode_output(
                project_dir,
                "shot_blocking_plan",
                episode_key,
                ShotBlockingPlanEpisodeOutput,
            )
            backgrounds = self._load_episode_output(
                project_dir,
                "shot_background_image_generation",
                episode_key,
                ShotBackgroundImageGenerationEpisodeOutput,
            )
            rows = [shot for clip in plan.clips for shot in clip.shots]
            selected_scene_ids = {shot.scene_id for shot in self._selected_shots(project_dir, plan)}
            target_shot_ids = {shot.shot_id for shot in rows if shot.scene_id in selected_scene_ids}
            blocking_by_shot = {item.shot_id: item for item in blocking.shots}
            background_by_id = {item.background_id: item for item in backgrounds.generated_backgrounds}
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotBlockingControlEpisodeOutput(episode_key=episode_key)
            if path.exists():
                existing = ShotBlockingControlEpisodeOutput.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            by_shot = {item.shot_id: item for item in existing.generated_controls}
            regenerated_shot_ids: set[str] = set()
            for shot in rows:
                if shot.shot_id not in target_shot_ids:
                    continue
                plan_item = blocking_by_shot.get(shot.shot_id)
                if plan_item is None:
                    raise ValueError(f"shot_blocking_control_render missing plan for {shot.shot_id}")
                background = background_by_id.get(plan_item.background_id)
                if background is None:
                    raise ValueError(
                        f"shot_blocking_control_render missing background {plan_item.background_id}"
                    )
                output_path = self.layout.image_asset_path(
                    project_dir, "shot_blocking_controls", shot.shot_id
                )
                old = by_shot.get(shot.shot_id)
                if (
                    old
                    and not self._force(self.workflow)
                    and old.plan_fingerprint == plan_item.fingerprint
                    and old.renderer_version == self.RENDERER_VERSION
                    and self.layout.existing_project_file(project_dir, old.asset_path)
                ):
                    continue
                background_path = project_dir / background.asset_path
                if not background_path.is_file():
                    raise FileNotFoundError(
                        f"shot_blocking_control_render background is missing: {background.asset_path}"
                    )
                with Image.open(background_path) as opened:
                    background_size = opened.size
                controls = [
                    BlockingControlBinding(
                        binding_id=placement.binding_id,
                        color=self._binding_color(placement.binding_id),
                        bbox_norm=(
                            placement.center_x - placement.width / 2,
                            placement.ground_y - placement.height,
                            placement.width,
                            placement.height,
                        ),
                        footpoint_norm=(placement.center_x, placement.ground_y),
                        facing_vector=(placement.facing_x, placement.facing_y),
                        gaze_target_norm=(
                            placement.gaze_target_x,
                            placement.gaze_target_y,
                        ),
                        depth=placement.depth_rank,
                    )
                    for placement in plan_item.placements
                ]
                rendered = render_shot_blocking_control(background_size, controls)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = output_path.with_suffix(".tmp.png")
                rendered.save(temporary_path, format="PNG")
                temporary_path.replace(output_path)
                control_ref = AssetRef(
                    id=f"{shot.shot_id}_blocking_control",
                    type="image",
                    path=str(output_path),
                    metadata={"asset_type": "shot_blocking_control", "shot_id": shot.shot_id},
                )
                fingerprint = self._reference_version(control_ref)
                by_shot[shot.shot_id] = ShotBlockingControlItem(
                    episode_key=episode_key,
                    clip_id=shot.clip_id,
                    shot_id=shot.shot_id,
                    scene_id=shot.scene_id,
                    background_id=background.background_id,
                    plan_fingerprint=plan_item.fingerprint,
                    renderer_version=self.RENDERER_VERSION,
                    asset_path=self.layout.project_relative(project_dir, output_path),
                    fingerprint=fingerprint,
                )
                regenerated_shot_ids.add(shot.shot_id)
                self.repo.write_json(
                    path,
                    ShotBlockingControlEpisodeOutput(
                        episode_key=episode_key,
                        generated_controls=[
                            by_shot[item.shot_id]
                            for item in rows
                            if item.shot_id in by_shot
                        ],
                    ),
                )
            missing = target_shot_ids.difference(by_shot)
            if missing:
                raise ValueError(
                    "shot_blocking_control_render missing shots: " + ", ".join(sorted(missing))
                )
            output = ShotBlockingControlEpisodeOutput(
                episode_key=episode_key,
                generated_controls=[
                    by_shot[shot.shot_id] for shot in rows if shot.shot_id in by_shot
                ],
            )
            self.repo.write_json(path, output)
            if regenerated_shot_ids:
                self._invalidate_completed_nodes(
                    state,
                    {
                        "shot_keyframe_prompt",
                        "shot_keyframe_stage_generation",
                        "shot_keyframe_image_generation",
                        "shot_manifest_generation",
                        *EXPECTED_OUTPUT_GENERATION_NODES,
                        *EXPECTED_OUTPUT_POSTGEN_NODES,
                    },
                )
            all_outputs.append(output)
        self.repo.save_node_output(
            project_dir, self.name, ShotBlockingControlOutput(episodes=all_outputs)
        )
        return state


class ShotKeyframePromptNode(ShotAssetNodeBase):
    name = "shot_keyframe_prompt"

    def _negative_prompt(self) -> str:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        return self._clean(params.get("negative_prompt"))

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        negative = self._negative_prompt()
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(
                project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput
            )
            rows = [shot for clip in plan.clips for shot in clip.shots]
            backgrounds = self._load_episode_output(
                project_dir,
                "shot_background_image_generation",
                episode_key,
                ShotBackgroundImageGenerationEpisodeOutput,
            )
            blocking = self._load_episode_output(
                project_dir,
                "shot_blocking_plan",
                episode_key,
                ShotBlockingPlanEpisodeOutput,
            )
            controls = self._load_episode_output(
                project_dir,
                "shot_blocking_control_render",
                episode_key,
                ShotBlockingControlEpisodeOutput,
            )
            targets = self._selected_shots(project_dir, plan)
            assets = self._all_assets(project_dir, state)
            background_by_shot = {
                item.shot_id: item for item in backgrounds.generated_backgrounds
            }
            blocking_by_shot = {item.shot_id: item for item in blocking.shots}
            control_by_shot = {item.shot_id: item for item in controls.generated_controls}
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotKeyframePromptOutput(prompts=[])
            if path.exists():
                try:
                    existing = ShotKeyframePromptOutput.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except Exception:
                    existing = ShotKeyframePromptOutput(prompts=[])
            by_id = {item.shot_id: item for item in existing.prompts}
            regenerated_shot_ids: set[str] = set()
            for shot in targets:
                background = background_by_shot.get(shot.shot_id)
                if background is None:
                    raise ValueError(f"shot_keyframe_prompt has no background for {shot.shot_id}")
                blocking_item = blocking_by_shot.get(shot.shot_id)
                control = control_by_shot.get(shot.shot_id)
                if blocking_item is None or control is None:
                    raise ValueError(
                        f"shot_keyframe_prompt has no blocking plan/control for {shot.shot_id}"
                    )
                if blocking_item.fingerprint != control.plan_fingerprint:
                    raise ValueError(f"shot_keyframe_prompt has stale control for {shot.shot_id}")
                background_ref = self._background_asset_ref(project_dir, background)
                control_path = project_dir / control.asset_path
                if not control_path.is_file():
                    raise FileNotFoundError(
                        f"shot_keyframe_prompt control image is missing: {control.asset_path}"
                    )
                control_ref = AssetRef(
                    id=f"{shot.shot_id}_blocking_control",
                    type="image",
                    path=str(control_path),
                    metadata={"asset_type": "shot_blocking_control", "shot_id": shot.shot_id},
                )
                foreground: list[tuple[ShotBlockingBinding, str, AssetRef]] = []
                for binding in blocking_item.bindings:
                    item = assets.get(binding.ref_id)
                    if item is None:
                        raise ValueError(
                            f"{shot.shot_id} has unresolved binding ref_id {binding.ref_id}"
                        )
                    if item["kind"] == "roleboard":
                        role, appearance = item["object"]
                        label = f"{role.name}／{appearance.name}"
                    else:
                        prop, _asset = item["object"]
                        label = prop.name
                    foreground.append((binding, label, self._asset_ref(project_dir, item)))
                refs = [background_ref, control_ref, *[ref for _, _, ref in foreground]]
                deterministic_bindings = [
                    f"Image {index} = {binding.binding_id}，身份固定为{label}"
                    for index, (binding, label, _ref) in enumerate(foreground, start=3)
                ]
                reference_guide = "\n".join(
                    [
                        "Image 1 = 必须锁定的干净背景、机位、透视和固定陈设",
                        "Image 2 = 只表达匿名主体位置、尺度、朝向、视线和遮挡的技术控制图，不提供外观",
                        *deterministic_bindings,
                    ]
                )
                request_prompt = self.workflow.prompts.render(
                    self.name,
                    video_prompt=shot.video_prompt,
                    blocking_plan=json.dumps(
                        {
                            "composition_intent": blocking_item.composition_intent,
                            "placements": [
                                item.model_dump(mode="json")
                                for item in blocking_item.placements
                            ],
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    visual_quality=self._visual_quality(state),
                )
                renderer_fingerprint = self.workflow.prompts.renderer_fingerprint(self.name) or ""
                input_fingerprint = self._generation_fingerprint(
                    f"{request_prompt}\n\nrenderer_sha256={renderer_fingerprint}",
                    refs,
                    provider,
                )
                old = by_id.get(shot.shot_id)
                if (
                    old
                    and not self._force(self.workflow)
                    and old.input_fingerprint == input_fingerprint
                ):
                    continue
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
                opening_frame = self._clean(response.prompt_content)
                stage_prompt = self.workflow.prompts.render_final(
                    self.name,
                    opening_frame,
                    {
                        "phase": "stage",
                        "reference_guide": reference_guide,
                        "negative_prompt": negative,
                    },
                )
                placements_by_binding = {
                    placement.binding_id: placement for placement in blocking_item.placements
                }
                final_binding_lines: list[str] = []
                for index, (binding, label, _ref) in enumerate(foreground, start=3):
                    line = f"Image {index} = {binding.binding_id}，身份固定为{label}"
                    if binding.asset_kind == "roleboard":
                        placement = placements_by_binding.get(binding.binding_id)
                        if placement is None:
                            raise ValueError(
                                f"{shot.shot_id} role binding {binding.binding_id} has no blocking placement"
                            )
                        slot = {
                            "center_x": round(placement.center_x, 4),
                            "ground_y": round(placement.ground_y, 4),
                            "width": round(placement.width, 4),
                            "height": round(placement.height, 4),
                            "depth_rank": placement.depth_rank,
                            "facing": placement.facing,
                        }
                        line += (
                            "；仅允许原位精修 Image 1 中槽位 "
                            f"{json.dumps(slot, ensure_ascii=False, separators=(',', ':'))} 的既有主体，"
                            "不得作用于其他主体"
                        )
                    else:
                        line += (
                            "；仅允许原位精修 Image 1 中已经存在的同一道具，"
                            "不得把该道具新增、移动或附着到其他主体"
                        )
                    final_binding_lines.append(line)
                final_reference_guide = "\n".join(
                    [
                        "Image 1 = 粗排关键帧，锁定构图、每个 binding 的身份槽位、主体位置、姿态、朝向与遮挡",
                        "Image 2 = 原始干净背景，用于恢复全部非人物区域、固定建筑、陈设、透视与光线",
                        *final_binding_lines,
                    ]
                )
                final_prompt = self.workflow.prompts.render_final(
                    self.name,
                    opening_frame,
                    {
                        "phase": "final",
                        "reference_guide": final_reference_guide,
                        "negative_prompt": negative,
                    },
                )
                by_id[shot.shot_id] = ShotKeyframePromptItem(
                    episode_key=episode_key,
                    clip_id=shot.clip_id,
                    shot_id=shot.shot_id,
                    background_id=background.background_id,
                    background_asset_path=background.asset_path,
                    background_asset_url=background.asset_url,
                    blocking_control_asset_path=control.asset_path,
                    blocking_fingerprint=control.fingerprint,
                    ref_ids=shot.ref_ids,
                    bindings=blocking_item.bindings,
                    stage_prompt=stage_prompt,
                    prompt=final_prompt,
                    input_fingerprint=input_fingerprint,
                    negative_prompt=negative or None,
                    included_fields=["video_prompt", "references", "visual_style"],
                    excluded_state_fields=["future_events", "future_prop_states"],
                    prompt_provenance={
                        "appearance_ids": [
                            ref.metadata.get("appearance_id")
                            for binding, _label, ref in foreground
                            if binding.asset_kind == "roleboard"
                            and ref.metadata.get("appearance_id")
                        ],
                        "prop_ref_ids": [
                            ref.id
                            for binding, _label, ref in foreground
                            if binding.asset_kind == "prop"
                        ],
                        "background_id": background.background_id,
                        "scene_id": shot.scene_id,
                        "blocking_plan_fingerprint": blocking_item.fingerprint,
                        "binding_map": {
                            binding.binding_id: binding.ref_id
                            for binding in blocking_item.bindings
                        },
                    },
                    clean_plate=False,
                    overlay_text_spec=None,
                )
                regenerated_shot_ids.add(shot.shot_id)
            output = ShotKeyframePromptOutput(
                prompts=[by_id[shot.shot_id] for shot in rows if shot.shot_id in by_id]
            )
            self.repo.write_json(path, output)
            if regenerated_shot_ids:
                self._invalidate_completed_nodes(
                    state,
                    {
                        "shot_keyframe_stage_generation",
                        "shot_keyframe_image_generation",
                        "shot_manifest_generation",
                        *EXPECTED_OUTPUT_GENERATION_NODES,
                        *EXPECTED_OUTPUT_POSTGEN_NODES,
                    },
                )
        return state


class ShotKeyframeStageGenerationNode(ShotAssetNodeBase):
    name = "shot_keyframe_stage_generation"

    def _concurrency(self) -> int:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        try:
            return max(1, min(5, int(params.get("concurrency", 2))))
        except (TypeError, ValueError):
            return 2

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.image("shot", node_name=self.name)
        if not bool(getattr(provider, "supports_reference_images", True)):
            raise ValueError("shot_keyframe_stage_generation requires a reference-image provider")
        for episode_key in self.active_episode_keys(state):
            plan = self._load_episode_output(
                project_dir, "clip_to_shots", episode_key, ClipToShotsEpisodeOutput
            )
            prompt_output = self._load_episode_output(
                project_dir, "shot_keyframe_prompt", episode_key, ShotKeyframePromptOutput
            )
            backgrounds = self._load_episode_output(
                project_dir,
                "shot_background_image_generation",
                episode_key,
                ShotBackgroundImageGenerationEpisodeOutput,
            )
            rows = [shot for clip in plan.clips for shot in clip.shots]
            targets = self._selected_shots(project_dir, plan)
            assets = self._all_assets(project_dir, state)
            prompts_by_id = {item.shot_id: item for item in prompt_output.prompts}
            backgrounds_by_id = {
                item.background_id: item for item in backgrounds.generated_backgrounds
            }
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotKeyframeStageGenerationOutput(generated_stages=[])
            if path.exists():
                existing = ShotKeyframeStageGenerationOutput.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            by_id = {item.shot_id: item for item in existing.generated_stages}
            semaphore = asyncio.Semaphore(self._concurrency())

            async def generate(
                shot: ShotPlanItem,
            ) -> tuple[ShotKeyframeStageGenerationItem, bool]:
                prompt = prompts_by_id.get(shot.shot_id)
                if prompt is None:
                    raise ValueError(f"missing keyframe prompt for {shot.shot_id}")
                background = backgrounds_by_id.get(prompt.background_id)
                if background is None:
                    raise ValueError(f"missing background {prompt.background_id} for {shot.shot_id}")
                control_path = project_dir / prompt.blocking_control_asset_path
                if not control_path.is_file():
                    raise FileNotFoundError(
                        f"missing blocking control for {shot.shot_id}: "
                        f"{prompt.blocking_control_asset_path}"
                    )
                control_ref = AssetRef(
                    id=f"{shot.shot_id}_blocking_control",
                    type="image",
                    path=str(control_path),
                    metadata={"asset_type": "shot_blocking_control", "shot_id": shot.shot_id},
                )
                foreground_refs: list[AssetRef] = []
                for binding in prompt.bindings:
                    item = assets.get(binding.ref_id)
                    if item is None or item["kind"] != binding.asset_kind:
                        raise ValueError(
                            f"stale keyframe binding {binding.binding_id}/{binding.ref_id} for {shot.shot_id}"
                        )
                    foreground_refs.append(self._asset_ref(project_dir, item))
                refs = [
                    self._background_asset_ref(project_dir, background),
                    control_ref,
                    *foreground_refs,
                ]
                limit = int(getattr(provider, "max_reference_images", 99) or 99)
                if len(refs) > limit:
                    raise ValueError(
                        f"{shot.shot_id} needs {len(refs)} stage references but provider limit is {limit}"
                    )
                fingerprint = self._generation_fingerprint(prompt.stage_prompt, refs, provider)
                old = by_id.get(shot.shot_id)
                if (
                    old
                    and not self._force(self.workflow)
                    and old.fingerprint == fingerprint
                    and self.layout.existing_project_file(project_dir, old.stage_asset_path)
                ):
                    return old, False
                async with semaphore:
                    result, final_prompt, _ = await self._generate_image_with_safety_prompt_rewrites(
                        provider=provider,
                        state=state,
                        node_name=self.name,
                        asset_id=f"{shot.shot_id}_keyframe_stage",
                        prompt=prompt.stage_prompt,
                        refs=refs,
                        metadata={
                            "node_name": self.name,
                            "prompt_asset_type": "shot_keyframe_stage",
                            "prompt_asset_name": shot.shot_id,
                            "shot_id": shot.shot_id,
                            "aspectRatio": "16:9",
                            "imageSize": "4K",
                        },
                        context={"video_prompt": shot.video_prompt},
                    )
                    asset_path = await self.media_store.write_first_generated_image(
                        project_dir,
                        self.layout.image_asset_path(
                            project_dir, "shot_keyframe_stages", shot.shot_id
                        ),
                        result,
                    )
                return ShotKeyframeStageGenerationItem(
                    episode_key=episode_key,
                    clip_id=shot.clip_id,
                    shot_id=shot.shot_id,
                    background_id=background.background_id,
                    ref_ids=shot.ref_ids,
                    stage_asset_id=f"{shot.shot_id}_keyframe_stage",
                    stage_asset_path=asset_path,
                    stage_asset_url=self.first_image_url(result),
                    prompt=final_prompt,
                    fingerprint=fingerprint,
                    blocking_fingerprint=prompt.blocking_fingerprint,
                    provider=result.provider,
                    model=result.model,
                    request_id=result.request_id,
                    usage=result.usage,
                    raw_response=result.raw_response,
                ), True

            regenerated_shot_ids: set[str] = set()
            tasks = [asyncio.create_task(generate(shot)) for shot in targets]
            for completed in asyncio.as_completed(tasks):
                item, regenerated = await completed
                by_id[item.shot_id] = item
                if regenerated:
                    regenerated_shot_ids.add(item.shot_id)
                self.repo.write_json(
                    path,
                    ShotKeyframeStageGenerationOutput(
                        generated_stages=[
                            by_id[shot.shot_id] for shot in rows if shot.shot_id in by_id
                        ]
                    ),
                )
            missing = {shot.shot_id for shot in targets}.difference(by_id)
            if missing:
                raise ValueError(
                    f"shot_keyframe_stage_generation missing stages: {', '.join(sorted(missing))}"
                )
            self.repo.write_json(
                path,
                ShotKeyframeStageGenerationOutput(
                    generated_stages=[
                        by_id[shot.shot_id] for shot in rows if shot.shot_id in by_id
                    ]
                ),
            )
            if regenerated_shot_ids:
                self._invalidate_completed_nodes(
                    state,
                    {
                        "shot_keyframe_image_generation",
                        "shot_manifest_generation",
                        *EXPECTED_OUTPUT_GENERATION_NODES,
                        *EXPECTED_OUTPUT_POSTGEN_NODES,
                    },
                )
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
            stages = self._load_episode_output(
                project_dir,
                "shot_keyframe_stage_generation",
                episode_key,
                ShotKeyframeStageGenerationOutput,
            )
            rows = [shot for clip in plan.clips for shot in clip.shots]
            targets = self._selected_shots(project_dir, plan)
            assets = self._all_assets(project_dir, state)
            prompts_by_id = {item.shot_id: item for item in prompt_output.prompts}
            backgrounds_by_id = {item.background_id: item for item in backgrounds.generated_backgrounds}
            stages_by_id = {item.shot_id: item for item in stages.generated_stages}
            path = self.layout.node_episode_output_path(project_dir, self.name, episode_key)
            existing = ShotKeyframeImageGenerationOutput(generated_keyframes=[])
            if path.exists():
                try:
                    existing = ShotKeyframeImageGenerationOutput.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except Exception:
                    existing = ShotKeyframeImageGenerationOutput(generated_keyframes=[])
            by_id = {item.shot_id: item for item in existing.generated_keyframes}
            semaphore = asyncio.Semaphore(self._concurrency())

            async def generate(
                shot: ShotPlanItem,
            ) -> tuple[ShotKeyframeImageGenerationItem, bool]:
                prompt = prompts_by_id.get(shot.shot_id)
                if prompt is None:
                    raise ValueError(f"missing keyframe prompt for {shot.shot_id}")
                background = backgrounds_by_id.get(prompt.background_id)
                if background is None:
                    raise ValueError(f"missing background {prompt.background_id} for {shot.shot_id}")
                stage = stages_by_id.get(shot.shot_id)
                if stage is None:
                    raise ValueError(f"missing keyframe stage for {shot.shot_id}")
                if stage.background_id != background.background_id:
                    raise ValueError(f"stale keyframe stage background for {shot.shot_id}")
                stage_path = project_dir / stage.stage_asset_path
                if not stage_path.is_file() and not stage.stage_asset_url:
                    raise FileNotFoundError(
                        f"keyframe stage is missing for {shot.shot_id}: {stage.stage_asset_path}"
                    )
                stage_ref = AssetRef(
                    id=stage.stage_asset_id,
                    type="image",
                    path=str(stage_path) if stage_path.is_file() else None,
                    url=stage.stage_asset_url,
                    metadata={"asset_type": "shot_keyframe_stage", "shot_id": shot.shot_id},
                )
                foreground_refs: list[AssetRef] = []
                for binding in prompt.bindings:
                    item = assets.get(binding.ref_id)
                    if item is None or item["kind"] != binding.asset_kind:
                        raise ValueError(
                            f"stale final keyframe binding {binding.binding_id}/{binding.ref_id} "
                            f"for {shot.shot_id}"
                        )
                    foreground_refs.append(self._asset_ref(project_dir, item))
                refs = [
                    stage_ref,
                    self._background_asset_ref(project_dir, background),
                    *foreground_refs,
                ]
                fingerprint = self._generation_fingerprint(prompt.prompt, refs, provider)
                old = by_id.get(shot.shot_id)
                if (
                    old
                    and not self._force(self.workflow)
                    and old.fingerprint == fingerprint
                    and self.layout.existing_project_file(project_dir, old.keyframe_asset_path)
                ):
                    return old, False
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
                        context={"video_prompt": shot.video_prompt},
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
                    stage_asset_id=stage.stage_asset_id,
                    stage_fingerprint=stage.fingerprint,
                    blocking_fingerprint=prompt.blocking_fingerprint,
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
                ), True

            tasks = [asyncio.create_task(generate(shot)) for shot in targets]
            for completed in asyncio.as_completed(tasks):
                item, _regenerated = await completed
                by_id[item.shot_id] = item
                self.repo.write_json(
                    path,
                    ShotKeyframeImageGenerationOutput(
                        generated_keyframes=[
                            by_id[shot.shot_id] for shot in rows if shot.shot_id in by_id
                        ],
                    ),
                )
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

    def _audits_enabled(self) -> bool:
        try:
            return bool(getattr(self.repo.settings.app, "enable_image_audit", True))
        except Exception:
            return True

    def _accepted_assets(self, project_dir: Path, node_name: str, *, fallback_all: set[str] | None = None) -> set[str]:
        # 审计关闭时不再强依赖审计产物；给定 fallback_all（待审资产全量 id）则视为全部接受，
        # 使 manifest 的 background_audit/keyframe_audit/roleboard_audit gate 全部 accepted，
        # 保证 ready_for_video=true，由外部人工监督质量。
        if not self._audits_enabled():
            return set(fallback_all) if fallback_all else set()
        path = self.layout.node_output_path(project_dir, node_name)
        if not path.exists():
            return set(fallback_all) if fallback_all else set()
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
            assets = self._all_assets(project_dir, state)
            reference_budget = self._reference_budget()
            prompt_by_id = {item.shot_id: item for item in prompts.prompts}
            image_by_id = {item.shot_id: item for item in images.generated_keyframes}
            background_by_id = {item.background_id: item for item in backgrounds.generated_backgrounds}
            accepted_keyframes = self._accepted_assets(
                project_dir, "shot_keyframe_image_audit",
                fallback_all={item.keyframe_asset_id for item in images.generated_keyframes},
            )
            accepted_backgrounds = self._accepted_assets(
                project_dir, "shot_background_image_audit",
                fallback_all={item.background_id for item in backgrounds.generated_backgrounds},
            )
            accepted_roleboards = self._accepted_assets(
                project_dir, "roleboard_image_audit",
                fallback_all={
                    (appearance.asset_id or appearance.design_image_asset_id or f"{appearance.id}_roleboard")
                    for item in assets.values()
                    if item["kind"] == "roleboard"
                    for _role, appearance in [item["object"]]
                },
            )
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
                        name="background_audit",
                        status="accepted" if background.background_id in accepted_backgrounds else "rejected",
                        details=background.background_id,
                    ),
                    GateResult(
                        name="keyframe_audit",
                        status=(
                            "accepted"
                            if (not self._audits_enabled())
                            or (image.keyframe_asset_id in accepted_keyframes and image.audit_status == "accepted")
                            else "rejected"
                        ),
                        details=image.keyframe_asset_id,
                    ),
                ]
                scene_id, _ = self._shot_scene(row, assets)
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
                if not row.video_prompt:
                    raise ValueError(f"shot manifest requires video_prompt for {row.shot_id}")
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
                required_reference_role_ids = {
                    assets[ref_id]["object"][0].id
                    for ref_id in row.ref_ids
                    if assets[ref_id]["kind"] == "roleboard"
                }
                missing_role_ids = sorted(required_reference_role_ids.difference(seen_role_ids))
                if missing_role_ids:
                    raise ValueError(
                        f"shot_manifest_generation missing selected character roleboards for "
                        f"{row.shot_id}: {', '.join(missing_role_ids)}"
                    )
                total_image_references = 1 + len(row.ref_ids)
                gate_results.append(
                    GateResult(
                        name="reference_budget",
                        status="accepted" if total_image_references <= reference_budget else "rejected",
                        details=f"{total_image_references}/{reference_budget}",
                    )
                )
                ready_for_video = all(
                    gate.status == "accepted" for gate in gate_results if gate.required
                )
                video_prompt = row.video_prompt
                if prompt.clean_plate:
                    video_prompt += (
                        " Keep the shot as a clean plate with no readable text, subtitles, "
                        "signage, or human voice."
                    )
                shots.append(
                    ShotManifestItem(
                        shot_id=row.shot_id,
                        index=row.episode_shot_index,
                        clip_id=row.clip_id,
                        clip_index=row.clip_index,
                        shot_index_in_clip=row.shot_index_in_clip,
                        ref_ids=row.ref_ids,
                        layout_id=scene_id,
                        layout_ids=[scene_id],
                        title=row.video_prompt,
                        content=row.video_prompt,
                        duration_seconds=float(row.duration_seconds),
                        transition="cut",
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
                        reference_budget=reference_budget,
                        input_fingerprints={
                            "keyframe": image.fingerprint,
                            "background": background.fingerprint,
                            "scene_multiview": background.scene_multiview_fingerprint,
                            "blocking": image.blocking_fingerprint,
                            "keyframe_stage": image.stage_fingerprint,
                        },
                        ready_for_video=ready_for_video,
                    )
                )
            episode_gates = [
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
            SceneMultiviewPlanNode,
            SceneMultiviewImageGenerationNode,
            LayoutToBackgroundPromptNode,
            ShotBackgroundImageGenerationNode,
            ShotBlockingPlanNode,
            ShotBlockingControlRenderNode,
            ShotKeyframePromptNode,
            ShotKeyframeStageGenerationNode,
            ShotKeyframeImageGenerationNode,
            ShotManifestGenerationNode,
        )
    ]


__all__ = ["SHOT_ASSET_NODE_NAMES", "build_shot_asset_nodes"]
