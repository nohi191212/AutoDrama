"""Visual acceptance and precise repair for independently generated images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autodrama.core.schemas import (
    ImageAssetAuditItem,
    ImageAssetAuditOutput,
    Layout,
    ProjectState,
    Prop,
    PropAsset,
    Role,
    RoleAppearance,
    RoleSubjectFrontalImageGenerationOutput,
    ClipToShotsEpisodeOutput,
    LayoutToBackgroundPromptEpisodeOutput,
    ShotBackgroundImageGenerationEpisodeOutput,
    ShotBackgroundImageGenerationItem,
    ShotKeyframeImageGenerationItem,
    ShotKeyframeImageGenerationOutput,
    ShotKeyframePromptOutput,
    StaticAssetGenerationItem,
    StaticAssetGenerationOutput,
)
from autodrama.logging import get_logger
from autodrama.core.schemas import VisualStyleSpec
from autodrama.core.visual_contract import identity_brief, render_visual_style_brief
from autodrama.providers.base import AssetRef
from autodrama.workflows.nodes.director_nodes import build_director_node_runners
from autodrama.workflows.nodes.role_subject_nodes import build_role_subject_nodes
from autodrama.workflows.nodes.shot_asset_nodes import build_shot_asset_nodes
from autodrama.workflows.nodes.static_asset_nodes import build_static_asset_node_runners
from autodrama.workflows.output_scope import configured_episode_output_selection
from autodrama.workflows.runner import WorkflowNode


IMAGE_AUDIT_NODE_NAMES = [
    "key_vision_image_audit",
    "roleboard_image_audit",
    "role_subject_frontal_image_audit",
    "prop_image_audit",
    "layout_image_audit",
    "shot_background_image_audit",
    "shot_keyframe_image_audit",
]


class ImageAuditDecision(BaseModel):
    approved: bool
    issues: list[str] = Field(default_factory=list)
    revised_prompt: str = ""
    rationale: str = ""


class ImageAuditNodeBase:
    """Audit generated images and repair only the rejected asset in place."""

    source_node: str
    asset_type: str

    def __init__(self, *, workflow: Any) -> None:
        self.workflow = workflow
        self.repo = workflow.repo
        self.layout = workflow.layout
        self.router = workflow.router
        self.prompts = workflow.prompts
        self.logger = getattr(workflow, "logger", None) or get_logger()

    def _params(self) -> dict[str, Any]:
        params = getattr(self.repo.settings.nodes.get(self.name), "params", {}) or {}
        return dict(params) if isinstance(params, dict) else {}

    def _max_attempts(self) -> int:
        try:
            return max(
                1,
                min(
                    4,
                    int(
                        self._params().get(
                            "max_attempts",
                            self._params().get("max_iterations", 2),
                        )
                    ),
                ),
            )
        except (TypeError, ValueError):
            return 2

    def _selected_asset_ids(self) -> set[str]:
        return {str(item).strip() for item in getattr(self.workflow, "_active_asset_ids", set()) if str(item).strip()}

    def _load_source_output(self, project_dir: Path) -> StaticAssetGenerationOutput:
        path = self.layout.node_output_path(project_dir, self.source_node)
        if not path.exists():
            raise FileNotFoundError(f"{self.name} requires {self.source_node} output; run {self.source_node} first")
        return StaticAssetGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def _source_items(self, project_dir: Path) -> list[StaticAssetGenerationItem]:
        items = self._load_source_output(project_dir).generated_assets
        selected = self._selected_asset_ids()
        if selected:
            items = [item for item in items if item.asset_id in selected]
            missing = selected.difference({item.asset_id for item in items})
            if missing:
                raise ValueError(f"{self.name} could not find selected generated asset(s): {', '.join(sorted(missing))}")
        return items

    def _asset_ref(self, project_dir: Path, item: StaticAssetGenerationItem) -> AssetRef:
        existing = self.layout.existing_project_file(project_dir, item.asset_path)
        if not existing and not item.asset_url:
            raise FileNotFoundError(f"{self.name} cannot audit missing image: {item.asset_path or item.asset_id}")
        return AssetRef(
            id=item.asset_id,
            type="image",
            path=str(project_dir / existing) if existing else None,
            url=item.asset_url,
            metadata={"asset_type": item.asset_type, "source_node": self.source_node, "asset_id": item.asset_id},
        )

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        raise NotImplementedError

    @staticmethod
    def _style_brief(state: ProjectState) -> str:
        raw = state.metadata.get("visual_style_spec")
        if not isinstance(raw, dict):
            raise ValueError(
                "project metadata has no visual_style_spec v2; run the visual contract migration"
            )
        spec = VisualStyleSpec.model_validate(raw)
        return render_visual_style_brief(spec)

    def _save_revised_prompt(self, project_dir: Path, state: ProjectState, asset_id: str, prompt: str) -> None:
        raise NotImplementedError

    async def _regenerate_one(self, project_dir: Path, state: ProjectState, asset_id: str) -> None:
        previous_force = getattr(self.workflow, "_force_pregen", None)
        previous_asset_ids = getattr(self.workflow, "_active_asset_ids", None)
        self.workflow._force_pregen = True
        self.workflow._active_asset_ids = {asset_id}
        try:
            await build_static_asset_node_runners(self.workflow)[self.source_node].run(project_dir, state)
        finally:
            if previous_force is None:
                delattr(self.workflow, "_force_pregen")
            else:
                self.workflow._force_pregen = previous_force
            if previous_asset_ids is None:
                delattr(self.workflow, "_active_asset_ids")
            else:
                self.workflow._active_asset_ids = previous_asset_ids

    async def _audit_one(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        initial_item: StaticAssetGenerationItem,
    ) -> ImageAssetAuditItem:
        item = initial_item
        max_repairs = self._max_attempts()
        for attempt in range(1, max_repairs + 2):
            request = self.prompts.render(
                "image_asset_audit",
                asset_type=self.asset_type,
                asset_name=item.name,
                expectation=self._asset_expectation(state, item),
                current_prompt=item.prompt,
            )
            decision = await provider.generate_json(
                request,
                ImageAuditDecision,
                temperature=0.1,
                refs=[self._asset_ref(project_dir, item)],
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "asset_id": item.asset_id,
                    "prompt_asset_type": "image_audit",
                    "prompt_asset_name": item.asset_id,
                    "reasoning_effort": "high",
                },
            )
            state.budget.used_text_calls += 1
            decision.issues = [str(issue).strip() for issue in decision.issues if str(issue).strip()]
            decision.revised_prompt = str(decision.revised_prompt or "").strip()
            if decision.approved:
                return ImageAssetAuditItem(
                    asset_id=item.asset_id,
                    asset_type=self.asset_type,
                    approved=True,
                    issues=decision.issues,
                    rationale=str(decision.rationale or "").strip(),
                    attempts=attempt,
                )
            if not decision.revised_prompt:
                raise ValueError(f"{self.name} rejected {item.asset_id} without a revised_prompt")
            if attempt > max_repairs:
                raise ValueError(
                    f"{self.name} could not obtain an accepted image for {initial_item.asset_id} "
                    f"after {max_repairs} repair attempt(s)"
                )
            self.logger.warning(
                "%s rejected asset=%s attempt=%d/%d issues=%s; repairing only this asset",
                self.name,
                item.asset_id,
                attempt,
                max_repairs,
                "; ".join(decision.issues) or "unspecified visual issue",
            )
            self._save_revised_prompt(project_dir, state, item.asset_id, decision.revised_prompt)
            await self._regenerate_one(project_dir, state, item.asset_id)
            refreshed = {row.asset_id: row for row in self._source_items(project_dir)}
            item = refreshed.get(item.asset_id)
            if item is None:
                raise ValueError(f"{self.name} repair lost source output for {initial_item.asset_id}")
        raise AssertionError("unreachable image audit loop")

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text(self.asset_type, node_name=self.name)
        self.logger.info(
            "node=%s provider=%s model=%s source_node=%s",
            self.name,
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            self.source_node,
        )
        audited = [
            await self._audit_one(provider=provider, project_dir=project_dir, state=state, initial_item=item)
            for item in self._source_items(project_dir)
        ]
        self.repo.save_node_output(project_dir, self.name, ImageAssetAuditOutput(source_node=self.source_node, audited_assets=audited))
        return state


class PropImageAuditNode(ImageAuditNodeBase):
    name = "prop_image_audit"
    source_node = "prop_image_generation"
    asset_type = "prop"

    @staticmethod
    def _find_asset(state: ProjectState, asset_id: str) -> tuple[Prop, PropAsset]:
        for prop in state.props.values():
            for asset in prop.assets.values():
                if asset.id == asset_id:
                    return prop, asset
        raise KeyError(f"Unknown prop asset for audit: {asset_id}")

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        prop, asset = self._find_asset(state, item.asset_id)
        details = "；".join(
            value
            for value in (prop.intro, asset.desc, asset.visual_features, asset.state_change)
            if value
        )
        return (
            f"单件道具“{prop.name} / {asset.name}”。{details}。必须服从以下视觉契约："
            f"{self._style_brief(state)}。不是技术多视图。"
        )

    def _save_revised_prompt(self, project_dir: Path, state: ProjectState, asset_id: str, prompt: str) -> None:
        prop, asset = self._find_asset(state, asset_id)
        build_static_asset_node_runners(self.workflow)[self.source_node].prop_designs.update_asset_prompt(project_dir, prop, asset, prompt)


class KeyVisionImageAuditNode(ImageAuditNodeBase):
    name = "key_vision_image_audit"
    source_node = "key_vision_image_generation"
    asset_type = "key_vision"

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        return (
            "一张可作为全片风格与世界观锚点的主视觉。核心叙事主体、关键灵兽或标志物和整体氛围"
            f"必须清晰统一；服从以下视觉契约：{self._style_brief(state)}。"
            "没有额外主体、畸形、可读文字、logo 或水印。"
        )

    def _save_revised_prompt(self, project_dir: Path, state: ProjectState, asset_id: str, prompt: str) -> None:
        payload = state.metadata.get("key_vision_prompt")
        if not isinstance(payload, dict):
            raise ValueError("key_vision_image_audit requires key_vision_prompt metadata")
        payload = dict(payload)
        payload["prompt"] = prompt
        state.metadata["key_vision_prompt"] = payload

    async def _regenerate_one(self, project_dir: Path, state: ProjectState, asset_id: str) -> None:
        previous_force = getattr(self.workflow, "_force_pregen", None)
        self.workflow._force_pregen = True
        try:
            await build_director_node_runners(self.workflow)[self.source_node].run(project_dir, state)
        finally:
            if previous_force is None:
                delattr(self.workflow, "_force_pregen")
            else:
                self.workflow._force_pregen = previous_force


class RoleboardImageAuditNode(ImageAuditNodeBase):
    name = "roleboard_image_audit"
    source_node = "roleboard_image_generation"
    asset_type = "roleboard"

    @staticmethod
    def _find_appearance(state: ProjectState, asset_id: str) -> tuple[Role, RoleAppearance]:
        for role in state.roles.values():
            for appearance in role.appearances.values():
                roleboard_asset_id = appearance.asset_id or appearance.design_image_asset_id or f"{appearance.id}_roleboard"
                if asset_id in {appearance.id, roleboard_asset_id, f"{appearance.id}_roleboard"}:
                    return role, appearance
        raise KeyError(f"Unknown roleboard asset for audit: {asset_id}")

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        role, appearance = self._find_appearance(state, item.asset_id)
        details = identity_brief(appearance)
        return (
            f"单角色身份板“{role.name} / {appearance.name}”。{details}。"
            "必须严格只呈现同一主体的正面、侧面、背面三个等尺度完整全身视图，身份一致且结构自然；"
            f"服从以下视觉契约：{self._style_brief(state)}。无其他角色、文字、logo、水印或畸形肢体。"
        )

    def _save_revised_prompt(self, project_dir: Path, state: ProjectState, asset_id: str, prompt: str) -> None:
        _role, appearance = self._find_appearance(state, asset_id)
        appearance.roleboard_prompt = prompt
        appearance.prompt = prompt


class RoleSubjectFrontalImageAuditNode(ImageAuditNodeBase):
    name = "role_subject_frontal_image_audit"
    source_node = "role_subject_frontal_image_generation"
    asset_type = "role_subject_frontal"

    def _source_items(self, project_dir: Path) -> list[StaticAssetGenerationItem]:
        path = self.layout.node_output_path(project_dir, self.source_node)
        if not path.exists():
            raise FileNotFoundError(f"{self.name} requires {self.source_node} output; run {self.source_node} first")
        output = RoleSubjectFrontalImageGenerationOutput.model_validate_json(path.read_text(encoding="utf-8"))
        rows = output.generated_frontal_images
        selected = self._selected_asset_ids()
        if selected:
            rows = [row for row in rows if row.asset_id in selected or row.appearance_id in selected]
            missing = selected.difference({row.asset_id for row in rows}).difference({row.appearance_id for row in rows})
            if missing:
                raise ValueError(f"{self.name} could not find selected generated asset(s): {', '.join(sorted(missing))}")
        return [
            StaticAssetGenerationItem(
                asset_id=row.asset_id,
                asset_type="roleboard",
                owner_id=row.role_id,
                name=f"{row.role_name}/{row.appearance_name}/frontal",
                prompt=row.prompt,
                asset_path=row.asset_path,
                asset_url=row.asset_url,
                provider=row.provider,
                model=row.model,
                request_id=row.request_id,
                usage=row.usage,
                raw_response=row.raw_response,
            )
            for row in rows
        ]

    @staticmethod
    def _find_appearance(state: ProjectState, asset_id: str) -> tuple[Role, RoleAppearance]:
        for role in state.roles.values():
            for appearance in role.appearances.values():
                expected = f"role_subject_frontal_{appearance.id}"
                if asset_id in {appearance.id, appearance.subject_frontal_image_asset_id, expected}:
                    return role, appearance
        raise KeyError(f"Unknown frontal role asset for audit: {asset_id}")

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        role, appearance = self._find_appearance(state, item.asset_id)
        details = identity_brief(appearance)
        return (
            f"单主体正面参考图“{role.name} / {appearance.name}”。{details}。"
            "主体必须全身正对镜头、居中完整、五官和关键结构无遮挡，严格保持角色板身份；"
            "无多视图、拼图、额外主体、文字、logo、水印或肢体错误。"
        )

    def _save_revised_prompt(self, project_dir: Path, state: ProjectState, asset_id: str, prompt: str) -> None:
        revised_prompts = state.metadata.get("role_subject_frontal_image_prompts")
        if not isinstance(revised_prompts, dict):
            revised_prompts = {}
        revised_prompts = dict(revised_prompts)
        revised_prompts[asset_id] = prompt
        state.metadata["role_subject_frontal_image_prompts"] = revised_prompts

    async def _regenerate_one(self, project_dir: Path, state: ProjectState, asset_id: str) -> None:
        previous_force = getattr(self.workflow, "_force_pregen", None)
        previous_asset_ids = getattr(self.workflow, "_active_asset_ids", None)
        self.workflow._force_pregen = True
        self.workflow._active_asset_ids = {asset_id}
        try:
            runners = {node.name: node for node in build_role_subject_nodes(self.workflow)}
            await runners[self.source_node].run(project_dir, state)
        finally:
            if previous_force is None:
                delattr(self.workflow, "_force_pregen")
            else:
                self.workflow._force_pregen = previous_force
            if previous_asset_ids is None:
                delattr(self.workflow, "_active_asset_ids")
            else:
                self.workflow._active_asset_ids = previous_asset_ids


class LayoutImageAuditNode(ImageAuditNodeBase):
    name = "layout_image_audit"
    source_node = "layout_image_generation"
    asset_type = "layout"

    @staticmethod
    def _find_layout(state: ProjectState, asset_id: str) -> Layout:
        layout = state.layouts.get(asset_id)
        if layout is None:
            raise KeyError(f"Unknown layout asset for audit: {asset_id}")
        return layout

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        layout = self._find_layout(state, item.asset_id)
        space = "；".join(layout.space_features)
        return (
            f"单幅可用于镜头调度的场景母版“{layout.name}”。{layout.desc}。空间要点：{space}。"
            f"无人、无文字、无三视图或拼图；服从以下视觉契约：{self._style_brief(state)}。"
        )

    def _save_revised_prompt(self, project_dir: Path, state: ProjectState, asset_id: str, prompt: str) -> None:
        self._find_layout(state, asset_id).prompt = prompt


@dataclass(frozen=True)
class ShotImageAuditCandidate:
    episode_key: str
    selector: str
    item: StaticAssetGenerationItem


class ShotImageAuditNodeBase(ImageAuditNodeBase):
    """Audit shot images and regenerate only the owning background or keyframe."""

    def _episode_keys(self, state: ProjectState) -> list[str]:
        active = getattr(self.workflow, "_active_episode_keys", None)
        expected = self.workflow.script_service.state_episode_keys(state)
        if not active:
            return expected
        wanted = {str(key) for key in active}
        return [episode_key for episode_key in expected if episode_key in wanted]

    def _selected_shot_ids(
        self,
        project_dir: Path,
        plan: ClipToShotsEpisodeOutput,
    ) -> set[str]:
        selectors = {
            str(value).strip().lower().replace("-", "_")
            for value in getattr(self.workflow, "_active_shot_selectors", set()) or set()
            if str(value).strip()
        }
        rows = [shot for clip in plan.clips for shot in clip.shots]
        if not selectors:
            selection = configured_episode_output_selection(
                self.repo,
                project_dir,
                plan,
            )
            return set(selection.selected_shot_ids)
        selected: set[str] = set()
        for shot in rows:
            index = shot.episode_shot_index
            keys = {
                shot.shot_id.lower().replace("-", "_"),
                str(index),
                f"{index:03d}",
                f"shot_{index}",
                f"shot_{index:03d}",
                f"{plan.episode_key}_shot_{index}",
                f"{plan.episode_key}_shot_{index:03d}",
            }
            if keys.intersection(selectors):
                selected.add(shot.shot_id)
        return selected

    def _candidates(self, project_dir: Path, state: ProjectState) -> list[ShotImageAuditCandidate]:
        raise NotImplementedError

    def _asset_expectation_for_candidate(self, state: ProjectState, candidate: ShotImageAuditCandidate) -> str:
        raise NotImplementedError

    def _save_revised_prompt_for_candidate(
        self,
        project_dir: Path,
        state: ProjectState,
        candidate: ShotImageAuditCandidate,
        prompt: str,
    ) -> None:
        raise NotImplementedError

    async def _regenerate_candidate(
        self,
        project_dir: Path,
        state: ProjectState,
        candidate: ShotImageAuditCandidate,
    ) -> None:
        previous_force = getattr(self.workflow, "_force_pregen", None)
        previous_episodes = getattr(self.workflow, "_active_episode_keys", None)
        previous_shots = getattr(self.workflow, "_active_shot_selectors", None)
        self.workflow._force_pregen = True
        self.workflow._active_episode_keys = {candidate.episode_key}
        self.workflow._active_shot_selectors = {candidate.selector}
        try:
            runners = {node.name: node for node in build_shot_asset_nodes(self.workflow)}
            await runners[self.source_node].run(project_dir, state)
        finally:
            if previous_force is None:
                delattr(self.workflow, "_force_pregen")
            else:
                self.workflow._force_pregen = previous_force
            if previous_episodes is None:
                delattr(self.workflow, "_active_episode_keys")
            else:
                self.workflow._active_episode_keys = previous_episodes
            if previous_shots is None:
                delattr(self.workflow, "_active_shot_selectors")
            else:
                self.workflow._active_shot_selectors = previous_shots

    async def _audit_candidate(
        self,
        *,
        provider: Any,
        project_dir: Path,
        state: ProjectState,
        initial: ShotImageAuditCandidate,
    ) -> ImageAssetAuditItem:
        candidate = initial
        max_repairs = self._max_attempts()
        for attempt in range(1, max_repairs + 2):
            item = candidate.item
            request = self.prompts.render(
                "image_asset_audit",
                asset_type=self.asset_type,
                asset_name=item.name,
                expectation=self._asset_expectation_for_candidate(state, candidate),
                current_prompt=item.prompt,
            )
            decision = await provider.generate_json(
                request,
                ImageAuditDecision,
                temperature=0.1,
                refs=[self._asset_ref(project_dir, item)],
                metadata={
                    "node_name": self.name,
                    "project_id": state.project_id,
                    "episode_key": candidate.episode_key,
                    "asset_id": item.asset_id,
                    "shot_id": candidate.selector,
                    "prompt_asset_type": "image_audit",
                    "prompt_asset_name": item.asset_id,
                    "reasoning_effort": "high",
                },
            )
            state.budget.used_text_calls += 1
            decision.issues = [str(issue).strip() for issue in decision.issues if str(issue).strip()]
            decision.revised_prompt = str(decision.revised_prompt or "").strip()
            if decision.approved:
                return ImageAssetAuditItem(
                    asset_id=item.asset_id,
                    asset_type=self.asset_type,
                    episode_key=candidate.episode_key,
                    approved=True,
                    issues=decision.issues,
                    rationale=str(decision.rationale or "").strip(),
                    attempts=attempt,
                )
            if not decision.revised_prompt:
                raise ValueError(f"{self.name} rejected {item.asset_id} without a revised_prompt")
            if attempt > max_repairs:
                raise ValueError(
                    f"{self.name} could not obtain an accepted image for {initial.item.asset_id} "
                    f"after {max_repairs} repair attempt(s)"
                )
            self.logger.warning(
                "%s rejected episode=%s asset=%s attempt=%d/%d issues=%s; repairing only this asset",
                self.name,
                candidate.episode_key,
                item.asset_id,
                attempt,
                max_repairs,
                "; ".join(decision.issues) or "unspecified visual issue",
            )
            self._save_revised_prompt_for_candidate(project_dir, state, candidate, decision.revised_prompt)
            await self._regenerate_candidate(project_dir, state, candidate)
            refreshed = {
                (row.episode_key, row.item.asset_id): row
                for row in self._candidates(project_dir, state)
            }
            candidate = refreshed.get((candidate.episode_key, item.asset_id))
            if candidate is None:
                raise ValueError(f"{self.name} repair lost source output for {initial.item.asset_id}")
        raise AssertionError("unreachable shot image audit loop")

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        provider = self.router.text("shot", node_name=self.name)
        self.logger.info(
            "node=%s provider=%s model=%s source_node=%s",
            self.name,
            getattr(provider, "name", "unknown"),
            getattr(provider, "model", "-"),
            self.source_node,
        )
        audited = [
            await self._audit_candidate(provider=provider, project_dir=project_dir, state=state, initial=candidate)
            for candidate in self._candidates(project_dir, state)
        ]
        self.repo.save_node_output(project_dir, self.name, ImageAssetAuditOutput(source_node=self.source_node, audited_assets=audited))
        return state


class ShotBackgroundImageAuditNode(ShotImageAuditNodeBase):
    name = "shot_background_image_audit"
    source_node = "shot_background_image_generation"
    asset_type = "shot_background"

    def _candidates(self, project_dir: Path, state: ProjectState) -> list[ShotImageAuditCandidate]:
        candidates: list[ShotImageAuditCandidate] = []
        for episode_key in self._episode_keys(state):
            plan_path = self.layout.node_episode_output_path(project_dir, "clip_to_shots", episode_key)
            output_path = self.layout.node_episode_output_path(project_dir, self.source_node, episode_key)
            if not plan_path.exists() or not output_path.exists():
                raise FileNotFoundError(f"{self.name} requires {self.source_node} and clip_to_shots output for {episode_key}")
            plan = ClipToShotsEpisodeOutput.model_validate_json(plan_path.read_text(encoding="utf-8"))
            output = ShotBackgroundImageGenerationEpisodeOutput.model_validate_json(output_path.read_text(encoding="utf-8"))
            selected_shots = self._selected_shot_ids(project_dir, plan)
            for row in output.generated_backgrounds:
                if not selected_shots.intersection(row.shot_ids):
                    continue
                candidates.append(
                    ShotImageAuditCandidate(
                        episode_key=episode_key,
                        selector=row.shot_ids[0],
                        item=StaticAssetGenerationItem(
                            asset_id=row.background_id,
                            asset_type="layout",
                            owner_id=episode_key,
                            name=f"{episode_key} / {row.background_id}",
                            prompt=row.prompt,
                            asset_path=row.asset_path,
                            asset_url=row.asset_url,
                            provider=row.provider,
                            model=row.model,
                            request_id=row.request_id,
                            usage=row.usage,
                            raw_response=row.raw_response,
                        ),
                    )
                )
        return candidates

    def _asset_expectation_for_candidate(self, state: ProjectState, candidate: ShotImageAuditCandidate) -> str:
        return (
            "单幅可直接用作镜头背景的无人场景板。保持空间结构、机位与透视可信，"
            f"服从以下视觉契约：{self._style_brief(state)}；"
            "无人物、可读文字、logo、水印、拼图、多视图或畸形建筑。"
        )

    def _save_revised_prompt_for_candidate(
        self,
        project_dir: Path,
        state: ProjectState,
        candidate: ShotImageAuditCandidate,
        prompt: str,
    ) -> None:
        path = self.layout.node_episode_output_path(project_dir, "layout_to_background_prompt", candidate.episode_key)
        output = LayoutToBackgroundPromptEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))
        found = False
        for row in output.backgrounds:
            if row.background_id == candidate.item.asset_id:
                row.prompt = prompt
                found = True
                break
        if not found:
            raise KeyError(f"Unknown shot background prompt for audit: {candidate.item.asset_id}")
        self.repo.write_json(path, output)


class ShotKeyframeImageAuditNode(ShotImageAuditNodeBase):
    name = "shot_keyframe_image_audit"
    source_node = "shot_keyframe_image_generation"
    asset_type = "shot_keyframe"

    async def run(self, project_dir: Path, state: ProjectState) -> ProjectState:
        state = await super().run(project_dir, state)
        audit_path = self.layout.node_output_path(project_dir, self.name)
        audit = ImageAssetAuditOutput.model_validate_json(audit_path.read_text(encoding="utf-8"))
        status_by_asset = {
            item.asset_id: ("accepted" if item.approved else "rejected")
            for item in audit.audited_assets
        }
        for episode_key in self._episode_keys(state):
            output_path = self.layout.node_episode_output_path(project_dir, self.source_node, episode_key)
            if not output_path.exists():
                continue
            output = ShotKeyframeImageGenerationOutput.model_validate_json(
                output_path.read_text(encoding="utf-8")
            )
            for row in output.generated_keyframes:
                if row.keyframe_asset_id in status_by_asset:
                    row.audit_status = status_by_asset[row.keyframe_asset_id]
            self.repo.write_json(output_path, output)
        return state

    def _candidates(self, project_dir: Path, state: ProjectState) -> list[ShotImageAuditCandidate]:
        candidates: list[ShotImageAuditCandidate] = []
        for episode_key in self._episode_keys(state):
            plan_path = self.layout.node_episode_output_path(project_dir, "clip_to_shots", episode_key)
            output_path = self.layout.node_episode_output_path(project_dir, self.source_node, episode_key)
            if not plan_path.exists() or not output_path.exists():
                raise FileNotFoundError(f"{self.name} requires {self.source_node} and clip_to_shots output for {episode_key}")
            plan = ClipToShotsEpisodeOutput.model_validate_json(plan_path.read_text(encoding="utf-8"))
            output = ShotKeyframeImageGenerationOutput.model_validate_json(output_path.read_text(encoding="utf-8"))
            selected_shots = self._selected_shot_ids(project_dir, plan)
            for row in output.generated_keyframes:
                if row.shot_id not in selected_shots:
                    continue
                candidates.append(
                    ShotImageAuditCandidate(
                        episode_key=episode_key,
                        selector=row.shot_id,
                        item=StaticAssetGenerationItem(
                            asset_id=row.keyframe_asset_id,
                            asset_type="roleboard",
                            owner_id=row.shot_id,
                            name=f"{episode_key} / {row.shot_id}",
                            prompt=row.prompt,
                            asset_path=row.keyframe_asset_path,
                            asset_url=row.keyframe_asset_url,
                            provider=row.provider,
                            model=row.model,
                            request_id=row.request_id,
                            usage=row.usage,
                            raw_response=row.raw_response,
                        ),
                    )
                )
        return candidates

    def _asset_expectation_for_candidate(self, state: ProjectState, candidate: ShotImageAuditCandidate) -> str:
        return (
            "单幅可作为视频起始帧的剧情关键帧。严格核对候选提示词要求的主体、道具、空间关系与动作起势，"
            "保持背景机位和视觉风格统一；未被候选提示词要求的角色、道具或动作不应作为拒绝理由。"
            "无身份漂移、重复主体或肢体错误。所有精确剧情文字均应由后期叠加，关键帧必须是无字 clean plate；"
            "拒绝可读文字、假文字、字幕、logo 或水印。"
        )

    def _save_revised_prompt_for_candidate(
        self,
        project_dir: Path,
        state: ProjectState,
        candidate: ShotImageAuditCandidate,
        prompt: str,
    ) -> None:
        path = self.layout.node_episode_output_path(project_dir, "shot_keyframe_prompt", candidate.episode_key)
        output = ShotKeyframePromptOutput.model_validate_json(path.read_text(encoding="utf-8"))
        found = False
        for row in output.prompts:
            if row.shot_id == candidate.selector:
                row.prompt = prompt
                found = True
                break
        if not found:
            raise KeyError(f"Unknown shot keyframe prompt for audit: {candidate.selector}")
        self.repo.write_json(path, output)


def build_image_audit_nodes(workflow: Any) -> list[WorkflowNode]:
    runners = {
        KeyVisionImageAuditNode.name: KeyVisionImageAuditNode(workflow=workflow),
        RoleboardImageAuditNode.name: RoleboardImageAuditNode(workflow=workflow),
        RoleSubjectFrontalImageAuditNode.name: RoleSubjectFrontalImageAuditNode(workflow=workflow),
        PropImageAuditNode.name: PropImageAuditNode(workflow=workflow),
        LayoutImageAuditNode.name: LayoutImageAuditNode(workflow=workflow),
        ShotBackgroundImageAuditNode.name: ShotBackgroundImageAuditNode(workflow=workflow),
        ShotKeyframeImageAuditNode.name: ShotKeyframeImageAuditNode(workflow=workflow),
    }
    return [WorkflowNode(name=node_name, run=runners[node_name].run) for node_name in IMAGE_AUDIT_NODE_NAMES]


__all__ = ["IMAGE_AUDIT_NODE_NAMES", "build_image_audit_nodes"]
