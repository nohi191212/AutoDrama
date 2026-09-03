"""Visual acceptance and precise repair for independently generated images."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autodrama.core.schemas import (
    ImageAuditDimensionAssessment,
    ImageAssetAuditItem,
    ImageAssetAuditOutput,
    KeyVisionPromptOutput,
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
from autodrama.image_audit_rubrics import (
    ProductionRubricBundle,
    aggregate_dimension_scores,
    load_key_vision_audit_rubric_bundle,
    render_production_rubric,
)
from autodrama.logging import get_logger
from autodrama.core.visual_contract import identity_brief
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


class KeyVisionAuditDecision(ImageAuditDecision):
    assessments: list[ImageAuditDimensionAssessment] = Field(min_length=1)
    weighted_score: float | None = Field(default=None, ge=0, le=10)
    category_scores: dict[str, float] = Field(default_factory=dict)


class ImageAuditNodeBase:
    """Audit generated images and repair only the rejected asset in place."""

    REPAIR_FEEDBACK_MARKER = "【本轮图像审计修复约束】"
    MAX_REPAIR_ATTEMPTS = 4
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
                    self.MAX_REPAIR_ATTEMPTS,
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

    def _audit_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
    ) -> list[AssetRef]:
        del state
        return [self._asset_ref(project_dir, item)]

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        raise NotImplementedError

    @staticmethod
    def _style_brief(state: ProjectState) -> str:
        value = str(state.metadata.get("visual_style_prompt") or "").strip()
        if not value:
            raise ValueError("project metadata has no global visual style prompt")
        return value

    def _save_revised_prompt(self, project_dir: Path, state: ProjectState, asset_id: str, prompt: str) -> None:
        raise NotImplementedError

    def _on_rejection(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
        attempt: int,
    ) -> None:
        del project_dir, state, item, decision, attempt

    def _prepare_repair(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
    ) -> None:
        self._save_revised_prompt(project_dir, state, item.asset_id, decision.revised_prompt)

    @classmethod
    def _without_previous_repair_feedback(cls, prompt: str) -> str:
        text = str(prompt or "").strip()
        marker_index = text.find(cls.REPAIR_FEEDBACK_MARKER)
        if marker_index >= 0:
            text = text[:marker_index].rstrip()
        return text

    @classmethod
    def _compose_repair_prompt(
        cls,
        *,
        current_prompt: str,
        decision: ImageAuditDecision,
    ) -> str:
        prompt = cls._without_previous_repair_feedback(decision.revised_prompt)
        if not prompt:
            prompt = cls._without_previous_repair_feedback(current_prompt)
        reasons: list[str] = []
        raw_reasons = decision.issues or [decision.rationale]
        for raw_reason in raw_reasons:
            reason = str(raw_reason or "").strip()
            if reason and reason not in reasons:
                reasons.append(reason)
        if not reasons:
            reasons.append("修复所有阻止当前图片直接交付的可见问题")
        feedback = "\n".join(f"- {reason}" for reason in reasons)
        return (
            f"{prompt.rstrip()}\n\n{cls.REPAIR_FEEDBACK_MARKER}\n"
            "下一轮生成必须修复以下本轮可见问题；保留已通过的主体身份、构图、视图数量、"
            "比例、材质和整体视觉风格，不要引入新的主体、道具、文字或水印：\n"
            f"{feedback}"
        ).strip()

    def _requires_revised_prompt(self) -> bool:
        return True

    def _force_accept_rejection(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
        attempt: int,
    ) -> ImageAssetAuditItem | None:
        del project_dir, state, item, decision, attempt
        return None

    def _record_rejection(
        self,
        project_dir: Path,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
        attempt: int,
    ) -> None:
        self.repo.append_audit_rejection(
            project_dir,
            node_name=self.name,
            asset_id=item.asset_id,
            attempt=attempt,
            issues=decision.issues,
            rationale=decision.rationale,
            current_prompt=item.prompt,
            revised_prompt=decision.revised_prompt,
        )

    def _render_audit_request(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        return self.prompts.render(
            "image_asset_audit",
            asset_type=self.asset_type,
            asset_name=item.name,
            expectation=self._asset_expectation(state, item),
            current_prompt=item.prompt,
        )

    def _decision_model(self) -> type[ImageAuditDecision]:
        return ImageAuditDecision

    def _audit_metadata(self, state: ProjectState, item: StaticAssetGenerationItem) -> dict[str, Any]:
        return {
            "node_name": self.name,
            "project_id": state.project_id,
            "asset_id": item.asset_id,
            "prompt_asset_type": "image_audit",
            "prompt_asset_name": item.asset_id,
            "reasoning_effort": "high",
        }

    def _normalize_decision(
        self,
        decision: ImageAuditDecision,
        *,
        state: ProjectState,
        item: StaticAssetGenerationItem,
    ) -> ImageAuditDecision:
        del state, item
        decision.issues = [str(issue).strip() for issue in decision.issues if str(issue).strip()]
        decision.revised_prompt = str(decision.revised_prompt or "").strip()
        decision.rationale = str(decision.rationale or "").strip()
        return decision

    def _audit_output_item(
        self,
        *,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
        attempt: int,
    ) -> ImageAssetAuditItem:
        return ImageAssetAuditItem(
            asset_id=item.asset_id,
            asset_type=self.asset_type,
            approved=True,
            issues=decision.issues,
            rationale=decision.rationale,
            attempts=attempt,
        )

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
            request = self._render_audit_request(state, item)
            decision = await provider.generate_json(
                request,
                self._decision_model(),
                temperature=0.1,
                refs=self._audit_refs(project_dir, state, item),
                metadata=self._audit_metadata(state, item),
            )
            state.budget.used_text_calls += 1
            if not isinstance(decision, ImageAuditDecision):
                raise TypeError(f"{self.name} returned an invalid audit decision")
            decision = self._normalize_decision(decision, state=state, item=item)
            if decision.approved:
                return self._audit_output_item(item=item, decision=decision, attempt=attempt)
            decision.revised_prompt = self._compose_repair_prompt(
                current_prompt=item.prompt,
                decision=decision,
            )
            self._record_rejection(project_dir, item, decision, attempt)
            self._on_rejection(project_dir, state, item, decision, attempt)
            forced_acceptance = self._force_accept_rejection(
                project_dir,
                state,
                item,
                decision,
                attempt,
            )
            if forced_acceptance is not None:
                return forced_acceptance
            if attempt > max_repairs:
                raise ValueError(
                    f"{self.name} could not obtain an accepted image for {initial_item.asset_id} "
                    f"after {max_repairs} repair attempt(s)"
                )
            if self._requires_revised_prompt() and not decision.revised_prompt:
                raise ValueError(f"{self.name} rejected {item.asset_id} without a revised_prompt")
            self.logger.warning(
                "%s rejected asset=%s attempt=%d/%d issues=%s; repairing only this asset",
                self.name,
                item.asset_id,
                attempt,
                max_repairs,
                "; ".join(decision.issues) or "unspecified visual issue",
            )
            self._prepare_repair(project_dir, state, item, decision)
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
    MAX_REPAIR_ATTEMPTS = 2

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

    def _rubric_bundle(self, state: ProjectState) -> ProductionRubricBundle:
        del state
        return load_key_vision_audit_rubric_bundle()

    def _max_attempts(self) -> int:
        """Allow at most three prompt-generation-audit repair cycles."""
        try:
            configured = self._params().get(
                "max_attempts",
                self._params().get("max_iterations", 3),
            )
            return max(1, min(3, int(configured)))
        except (TypeError, ValueError):
            return 3

    def _approval_threshold(self, bundle: ProductionRubricBundle) -> float:
        raw = self._params().get("approval_threshold", bundle.policy.approval_threshold)
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid key vision audit approval_threshold: {raw!r}") from exc
        if value < 0 or value > 10:
            raise ValueError("key vision audit approval_threshold must be between 0 and 10")
        return value

    def _decision_model(self) -> type[ImageAuditDecision]:
        return KeyVisionAuditDecision

    def _audit_metadata(self, state: ProjectState, item: StaticAssetGenerationItem) -> dict[str, Any]:
        metadata = super()._audit_metadata(state, item)
        raw = self._params().get("max_output_tokens", 16384)
        try:
            max_output_tokens = int(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid key vision audit max_output_tokens: {raw!r}") from exc
        if max_output_tokens <= 0:
            raise ValueError("key vision audit max_output_tokens must be positive")
        metadata["max_output_tokens"] = max_output_tokens
        return metadata

    def _render_audit_request(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        bundle = self._rubric_bundle(state)
        approval_threshold = self._approval_threshold(bundle)
        prompt_output = KeyVisionPromptOutput.model_validate(state.metadata.get("key_vision_prompt"))
        return self.prompts.render(
            "key_vision_image_audit",
            asset_name=item.name,
            expectation=self._asset_expectation(state, item),
            shot_contract=prompt_output.shot_contract,
            scene_style_contract=prompt_output.scene_style_contract,
            current_prompt=item.prompt,
            rubric=render_production_rubric(bundle),
            approval_threshold=f"{approval_threshold:g}",
        )

    def _normalize_decision(
        self,
        decision: ImageAuditDecision,
        *,
        state: ProjectState,
        item: StaticAssetGenerationItem,
    ) -> ImageAuditDecision:
        decision = super()._normalize_decision(decision, state=state, item=item)
        if not isinstance(decision, KeyVisionAuditDecision):
            raise TypeError("key_vision_image_audit requires KeyVisionAuditDecision")

        bundle = self._rubric_bundle(state)
        dimension_map = bundle.dimension_map
        assessments = {row.dimension_id: row for row in decision.assessments}
        if len(assessments) != len(decision.assessments):
            raise ValueError("key_vision_image_audit returned duplicate rubric dimensions")
        missing = [item.id for item in bundle.dimensions if item.id not in assessments]
        unknown = sorted(set(assessments).difference(dimension_map))
        if missing or unknown:
            raise ValueError(
                "key_vision_image_audit rubric coverage mismatch: "
                f"missing={missing or 'none'} unknown={unknown or 'none'}"
            )

        normalized_assessments: list[ImageAuditDimensionAssessment] = []
        for spec in bundle.dimensions:
            assessment = assessments[spec.id].model_copy(
                update={"category": spec.category, "weight": spec.weight, "gate": spec.gate}
            )
            normalized_assessments.append(assessment)
        decision.assessments = normalized_assessments

        weighted_score, category_scores = aggregate_dimension_scores(
            bundle,
            {
                row.dimension_id: row.score if row.applicable else None
                for row in decision.assessments
            },
        )
        expected_categories = {item.category for item in bundle.dimensions}
        missing_categories = sorted(expected_categories.difference(category_scores))
        if missing_categories:
            raise ValueError(
                f"key_vision_image_audit has no applicable score for categories: {missing_categories}"
            )

        reject_severities = set(bundle.policy.reject_severities)
        approval_threshold = self._approval_threshold(bundle)
        critical = [
            row
            for row in decision.assessments
            if row.applicable and row.severity in reject_severities
        ]
        failed_gates = [
            row
            for row in decision.assessments
            if bundle.policy.reject_gate_below_threshold
            and row.applicable
            and dimension_map[row.dimension_id].gate
            and row.score is not None
            and row.score < bundle.policy.gate_threshold
        ]
        decision.weighted_score = weighted_score
        decision.category_scores = dict(category_scores)
        decision.approved = (
            weighted_score >= approval_threshold
            and not critical
            and not failed_gates
        )
        if decision.approved:
            decision.revised_prompt = ""
        elif not decision.issues:
            decision.issues = [
                f"{row.dimension_id}: {row.defect or row.evidence}"
                for row in decision.assessments
                if row.applicable and (
                    row.severity in {"major", "critical"}
                    or row in failed_gates
                    or (row.score is not None and row.score < approval_threshold)
                )
            ]
        decision.rationale = (
            f"未封顶加权分 {weighted_score:.4f}，通过线 {approval_threshold:g}；"
            f"critical={len(critical)}，failed_gates={len(failed_gates)}。{decision.rationale}"
        ).strip()
        return decision

    def _audit_output_item(
        self,
        *,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
        attempt: int,
    ) -> ImageAssetAuditItem:
        if not isinstance(decision, KeyVisionAuditDecision) or decision.weighted_score is None:
            raise TypeError("key_vision_image_audit cannot persist an incomplete rubric decision")
        bundle = load_key_vision_audit_rubric_bundle()
        return ImageAssetAuditItem(
            asset_id=item.asset_id,
            asset_type=self.asset_type,
            approved=True,
            issues=decision.issues,
            rationale=decision.rationale,
            attempts=attempt,
            rubric_name=bundle.policy.name,
            rubric_revision=bundle.revision_label,
            weighted_score=decision.weighted_score,
            category_scores=decision.category_scores,
            dimension_assessments=decision.assessments,
        )

    def _asset_expectation(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        return (
            "一张由剧本类型定义、可作为全片世界观与空间尺度锚点的主视觉。只检查人物与建筑的相对比例、"
            "人物整体头身比例、空间透视、画面构图、遮挡层级、世界空间逻辑，以及是否存在会污染主视觉交付的"
            "生成模型风格高频噪点和局部伪影；不以媒介风格偏好、一般材质/色彩/灯光偏好、文字、水印、手脚细节"
            "或步态作为拒绝理由。真实材质纹理、合理雾气和合法电影颗粒不应被误判为噪点。"
        )

    def _requires_revised_prompt(self) -> bool:
        return False

    def _force_accept_rejection(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
        attempt: int,
    ) -> ImageAssetAuditItem | None:
        if attempt <= self._max_attempts():
            return None
        decision.approved = True
        decision.rationale = (
            f"已完成最多 {self._max_attempts()} 次主视觉重试；本轮仍有审计意见，按策略强制接收。"
            f"{decision.rationale}"
        ).strip()
        state.metadata["key_vision_audit_forced_acceptance"] = {
            "attempt": attempt,
            "reason": "达到主视觉审计重试上限后强制接收",
        }
        self.repo.save_state(project_dir, state)
        self.logger.warning(
            "%s force-accepted asset after retry limit attempt=%d",
            self.name,
            attempt,
        )
        return self._audit_output_item(item=item, decision=decision, attempt=attempt)

    def _on_rejection(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
        attempt: int,
    ) -> None:
        del item
        feedback = state.metadata.get("key_vision_audit_feedback")
        if not isinstance(feedback, list):
            feedback = []
        feedback = list(feedback)
        feedback.append(
            {
                "attempt": attempt,
                "issues": list(decision.issues),
                "rationale": decision.rationale,
            }
        )
        state.metadata["key_vision_audit_feedback"] = feedback
        state.metadata["key_vision_audit_rejection_log_path"] = self.layout.project_relative(
            project_dir,
            self.layout.audit_rejection_log_path(project_dir),
        )
        # Persist feedback before invoking the next prompt node so a failed
        # repair still becomes input to the next manually resumed cycle.
        self.repo.save_state(project_dir, state)

    def _prepare_repair(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
        decision: ImageAuditDecision,
    ) -> None:
        del project_dir, state, item, decision

    async def _regenerate_one(self, project_dir: Path, state: ProjectState, asset_id: str) -> None:
        del asset_id
        runners = build_director_node_runners(self.workflow)
        await runners["key_vision_prompt"].run(project_dir, state)
        self.repo.save_state(project_dir, state)
        await runners["key_vision_image_generation"].run(project_dir, state)
        self.repo.save_state(project_dir, state)


class RoleboardImageAuditNode(ImageAuditNodeBase):
    name = "roleboard_image_audit"
    source_node = "roleboard_image_generation"
    asset_type = "roleboard"
    MAX_REPAIR_ATTEMPTS = 2

    def _render_audit_request(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        role, appearance = self._find_appearance(state, item.asset_id)
        return self.prompts.render(
            "roleboard_image_audit",
            asset_name=item.name,
            expectation=self._asset_expectation(state, item),
            current_prompt=item.prompt,
            reference_context=self._reference_context(role, appearance),
        )

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
        stage = "；".join(
            value
            for value in (
                str(appearance.age_band or "").strip(),
                str(appearance.time_period or "").strip(),
            )
            if value
        )
        stage_expectation = f"目标年龄与时间阶段：{stage}。" if stage else ""
        return (
            f"单角色身份板“{role.name} / {appearance.name}”。{stage_expectation}{details}。"
            "必须严格只呈现同一主体的正面、侧面、背面三个等尺度完整全身视图，身份一致且结构自然；"
            "人物的脸型骨相、眉眼鼻唇、年龄感、发型轮廓、体型与服装结构应具体可辨，生物的头部、"
            "躯干、肢体、表面纹理与识别特征应具体可辨；不得以标准美型、通用英雄脸或无依据的繁复装饰"
            f"代替角色设计。服从以下视觉契约：{self._style_brief(state)}。无其他角色、文字、logo、水印或畸形肢体。"
        )

    @staticmethod
    def _reference_appearance(role: Role, appearance: RoleAppearance) -> RoleAppearance | None:
        if appearance.asset_role == "base":
            return None
        reference_name = str(appearance.reference_asset_name or "").strip()
        if not reference_name:
            raise ValueError(f"role variant {role.name}/{appearance.name} requires reference_asset_name")
        reference = role.appearances.get(reference_name)
        if reference is None:
            raise ValueError(
                f"role variant {role.name}/{appearance.name} references missing base appearance {reference_name}"
            )
        if reference.asset_role != "base":
            raise ValueError(f"role variant {role.name}/{appearance.name} must reference a base appearance")
        return reference

    @classmethod
    def _reference_context(cls, role: Role, appearance: RoleAppearance) -> str:
        reference = cls._reference_appearance(role, appearance)
        if reference is None:
            return "本次只提供图片1作为待审查身份板，没有同角色基础造型对照图。"
        target_stage = "；".join(
            value
            for value in (
                str(appearance.age_band or "").strip(),
                str(appearance.time_period or "").strip(),
            )
            if value
        ) or "当前变化造型规定的阶段"
        source_stage = "；".join(
            value
            for value in (
                str(reference.age_band or "").strip(),
                str(reference.time_period or "").strip(),
            )
            if value
        ) or "基础造型阶段"
        return (
            f"图片2是同一角色“{role.name}”的基础造型“{reference.name}”，其阶段为“{source_stage}”，"
            f"只用于核对跨阶段的身份血缘连续性。图片1的目标阶段是“{target_stage}”；"
            "应保留能够跨年龄成立的脸部骨相、五官关系和身体血缘特征，同时必须真实呈现目标年龄与时间阶段，"
            "不得照搬图片2只属于原阶段的皱纹、发色发量、体态、服装、妆造或其他阶段性特征。"
        )

    def _same_role_identity_ref(
        self,
        project_dir: Path,
        role: Role,
        appearance: RoleAppearance,
    ) -> AssetRef | None:
        reference = self._reference_appearance(role, appearance)
        if reference is None:
            return None
        ref_path = reference.asset_path or reference.design_image_asset_path
        ref_url = reference.asset_url or reference.design_image_asset_url
        path: str | None = None
        if ref_path:
            existing = self.layout.existing_project_file(project_dir, ref_path)
            if existing is not None:
                path = str(project_dir / existing)
        if not path and not ref_url:
            raise ValueError(
                f"roleboard audit for {role.name}/{appearance.name} requires generated base image {reference.name}"
            )
        return AssetRef(
            id=reference.asset_id or reference.design_image_asset_id or reference.id,
            type="image",
            path=path,
            url=ref_url,
            metadata={
                "asset_type": "roleboard_identity_reference",
                "source_node": self.source_node,
                "reference_role": "same_role_identity",
                "reference_index": 2,
                "role_id": role.id,
                "role_name": role.name,
                "appearance_id": reference.id,
                "appearance_name": reference.name,
                "reference_for": appearance.id,
                "identity_transfer_allowed": True,
            },
        )

    def _audit_refs(
        self,
        project_dir: Path,
        state: ProjectState,
        item: StaticAssetGenerationItem,
    ) -> list[AssetRef]:
        role, appearance = self._find_appearance(state, item.asset_id)
        refs = [self._asset_ref(project_dir, item)]
        identity_ref = self._same_role_identity_ref(project_dir, role, appearance)
        if identity_ref is not None:
            refs.append(identity_ref)
        return refs

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
    MAX_REPAIR_ATTEMPTS = 2

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
            f"A single 2:3 spatial-anchor image for the scene {layout.name}. {layout.desc}. "
            f"Fixed spatial anchors: {space}. It must show the same empty location as one coherent, "
            "production-ready single view from a readable natural camera height and angle — a finished set "
            "reference, not a technical diagram. Keep the structure readable at a glance: the main entrance "
            "and its approach, the fixed architecture and largest set pieces, and a clear foreground / "
            "midground / background depth. Do NOT split the scene into paired, stacked, or two-view panels, "
            "and do not add a floor-plan inset, north arrow, compass, crop marks, or border. "
            "No people, camera or character overlays, "
            f"readable text, logo, or watermark. Visual contract: {self._style_brief(state)}."
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
            self.repo.append_audit_rejection(
                project_dir,
                node_name=self.name,
                asset_id=item.asset_id,
                attempt=attempt,
                issues=decision.issues,
                rationale=str(decision.rationale or "").strip(),
                current_prompt=item.prompt,
                revised_prompt=decision.revised_prompt,
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
        if set(getattr(self.workflow, "_active_shot_selectors", set()) or set()):
            path = self.layout.node_output_path(project_dir, self.name)
            existing_by_id: dict[str, ImageAssetAuditItem] = {}
            if path.exists():
                try:
                    existing = ImageAssetAuditOutput.model_validate_json(
                        path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    self.logger.warning(
                        "%s ignored incompatible existing audit output during partial rerun: %s",
                        self.name,
                        exc,
                    )
                else:
                    existing_by_id = {
                        item.asset_id: item for item in existing.audited_assets
                    }
            for item in audited:
                existing_by_id[item.asset_id] = item
            audited = list(existing_by_id.values())
        self.repo.save_node_output(
            project_dir,
            self.name,
            ImageAssetAuditOutput(source_node=self.source_node, audited_assets=audited),
        )
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
                if row.shot_id not in selected_shots:
                    continue
                candidates.append(
                    ShotImageAuditCandidate(
                        episode_key=episode_key,
                        selector=row.shot_id,
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
            "One empty cinematic background plate for exactly one shot. It must preserve the bound scene's "
            "topology and the scene multiview master's camera position, target, field of view, height, pitch, "
            f"perspective, and reserved staging areas. Visual contract: {self._style_brief(state)}. "
            "No people, diagram overlays, readable text, logo, watermark, split view, or malformed architecture."
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
