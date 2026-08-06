from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    BudgetState,
    ImageAuditDimensionAssessment,
    ImageAuditRegion,
    ProjectState,
    StaticAssetGenerationItem,
)
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.image_audit_rubrics import (  # noqa: E402
    aggregate_dimension_scores,
    load_production_rubric_bundle,
    render_production_rubric,
)
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.image_audit_nodes import (  # noqa: E402
    KeyVisionAuditDecision,
    KeyVisionImageAuditNode,
)


OUTPUT = ROOT / ".tmp" / "key-vision-audit-rubric-smoke.json"


class FakeProvider:
    async def generate_json(self, _prompt: str, schema: type, **_kwargs: object) -> object:
        bundle = load_production_rubric_bundle(include_xuanhuan_style=True)
        return schema(
            approved=True,
            issues=[],
            revised_prompt="",
            rationale="全部适用维度均有完整可见证据。",
            assessments=[
                ImageAuditDimensionAssessment(
                    dimension_id=dimension.id,
                    category=dimension.category,
                    weight=dimension.weight,
                    applicable=True,
                    score=10,
                    severity="none",
                    evidence="该维度的可见证据完整且无缺陷。",
                )
                for dimension in bundle.dimensions
            ],
        )


class SmokeKeyVisionAuditNode(KeyVisionImageAuditNode):
    def __init__(self) -> None:
        self.repo = SimpleNamespace(settings=SimpleNamespace(nodes={}))
        self.prompts = PromptStore(SRC / "autodrama" / "prompts")

    def _asset_ref(self, _project_dir: Path, item: StaticAssetGenerationItem) -> AssetRef:
        return AssetRef(id=item.asset_id, type="image", url="https://example.invalid/key-vision.png")


def build_assessments(*, critical: bool) -> list[ImageAuditDimensionAssessment]:
    bundle = load_production_rubric_bundle(include_xuanhuan_style=True)
    rows: list[ImageAuditDimensionAssessment] = []
    for dimension in bundle.dimensions:
        failed = dimension.id == "prop_rigidity_topology"
        rows.append(
            ImageAuditDimensionAssessment(
                dimension_id=dimension.id,
                category=dimension.category,
                weight=dimension.weight,
                applicable=True,
                score=0 if failed else 10,
                severity="critical" if failed and critical else ("major" if failed else "none"),
                evidence="刚性道具在画面中心出现可见分叉。" if failed else "该维度的可见证据完整且无缺陷。",
                defect="一件刚性道具错误分叉。" if failed else "",
                regions=[ImageAuditRegion(label="道具分叉", x1=0.4, y1=0.3, x2=0.7, y2=0.8)] if failed else [],
            )
        )
    return rows


def main() -> int:
    bundle = load_production_rubric_bundle(include_xuanhuan_style=True)
    assert bundle.policy.score_caps is False
    assert "general_rubrics-v2" in bundle.rubric_revisions
    assert "xuanhuan-v2-rubrics" in bundle.rubric_revisions

    scores = {
        row.dimension_id: row.score
        for row in build_assessments(critical=True)
    }
    weighted_score, category_scores = aggregate_dimension_scores(bundle, scores)
    assert weighted_score > 6.9, weighted_score

    state = ProjectState.model_construct(
        project_id="rubric-smoke",
        metadata={
            "visual_style_name": "xuanhuan-v1",
            "visual_style_prompt": "Eastern xuanhuan stylized 3D CG animation.",
            "key_vision_prompt": {
                "shot_contract": "One rigid staff remains continuous between both hands.",
                "scene_style_contract": "Dry stone and matte cloth under motivated daylight.",
                "prompt": "Exactly one rigid staff with a continuous axis.",
            },
        },
        budget=BudgetState(),
    )
    item = StaticAssetGenerationItem(
        asset_id="key_vision_original",
        asset_type="key_vision",
        owner_id="project",
        name="主视觉原图",
        prompt="Exactly one rigid staff with a continuous axis.",
        provider="fake",
        model="fake",
    )
    node = SmokeKeyVisionAuditNode()
    decision = KeyVisionAuditDecision(
        approved=True,
        issues=["刚性道具分叉"],
        revised_prompt="保留原画面，只把刚性道具修复为一条连续轴线。",
        rationale="存在严重道具拓扑错误。",
        assessments=build_assessments(critical=True),
    )
    normalized = node._normalize_decision(decision, state=state, item=item)
    assert isinstance(normalized, KeyVisionAuditDecision)
    assert normalized.weighted_score == weighted_score
    assert normalized.weighted_score > 6.9
    assert normalized.approved is False
    assert "failed_gates=1" in normalized.rationale

    prompt_store = PromptStore(SRC / "autodrama" / "prompts")
    prompt = prompt_store.render(
        "key_vision_image_audit",
        asset_name="主视觉原图",
        expectation="东方玄幻国漫三维动画主视觉。",
        shot_contract="一根刚性长棍保持连续轴线。",
        scene_style_contract="干燥石材和柔哑织物服从自然日光。",
        current_prompt=item.prompt,
        rubric=render_production_rubric(bundle),
        approval_threshold=f"{bundle.policy.approval_threshold:g}",
    )
    assert "general_rubrics-v2" in prompt
    assert "xuanhuan-v2-rubrics" in prompt
    assert "一根刚性长棍保持连续轴线" in prompt
    assert "干燥石材和柔哑织物" in prompt
    assert "不得因为 gate" in prompt

    accepted = asyncio.run(
        SmokeKeyVisionAuditNode()._audit_one(
            provider=FakeProvider(),
            project_dir=ROOT,
            state=state,
            initial_item=item,
        )
    )
    assert accepted.approved is True
    assert accepted.weighted_score == 10
    assert len(accepted.dimension_assessments) == len(bundle.dimensions)
    assert accepted.rubric_name == bundle.policy.name
    assert state.budget.used_text_calls == 1

    result = {
        "protocol": bundle.policy.name,
        "revision": bundle.revision_label,
        "dimension_count": len(bundle.dimensions),
        "weighted_score": weighted_score,
        "category_scores": category_scores,
        "approved_with_critical": normalized.approved,
        "accepted_score": accepted.weighted_score,
        "score_caps": bundle.policy.score_caps,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
