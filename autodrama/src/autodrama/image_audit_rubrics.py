"""Production image-audit rubric loading and uncapped score aggregation."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, Field, model_validator


AuditCategory = Literal["physical", "contract", "cinematic", "style"]


class ProductionAuditPolicy(BaseModel):
    name: str
    revision: str
    rubric_files: list[str] = Field(min_length=1)
    category_weights: dict[AuditCategory, float]
    approval_threshold: float = Field(ge=0, le=10)
    gate_threshold: float = Field(default=6.0, ge=0, le=10)
    reject_gate_below_threshold: bool = True
    reject_severities: list[str] = Field(default_factory=lambda: ["critical"])
    score_caps: bool = False
    score_formula: str
    scale_anchors: dict[str, str]
    evidence_rules: list[str]

    @model_validator(mode="after")
    def validate_uncapped_policy(self) -> "ProductionAuditPolicy":
        if self.score_caps:
            raise ValueError("production image audit must not enable score caps")
        if abs(sum(self.category_weights.values()) - 1.0) > 1e-6:
            raise ValueError("production audit category weights must sum to 1")
        return self


class ProductionRubricDimension(BaseModel):
    id: str
    name: str
    category: AuditCategory
    weight: float = Field(gt=0)
    gate: bool = False
    applies_to: list[str] = Field(default_factory=lambda: ["all"])
    audit_question: str = ""


class ProductionRubricBundle(BaseModel):
    policy: ProductionAuditPolicy
    rubric_revisions: dict[str, str]
    dimensions: list[ProductionRubricDimension]

    @property
    def dimension_map(self) -> dict[str, ProductionRubricDimension]:
        return {item.id: item for item in self.dimensions}

    @property
    def revision_label(self) -> str:
        parts = [f"{name}@{revision}" for name, revision in self.rubric_revisions.items()]
        return f"{self.policy.revision};" + ";".join(parts)


def default_rubric_dir() -> Path:
    return Path(__file__).resolve().parent / "prompts" / "image_rubrics"


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"rubric JSON must contain an object: {path}")
    return payload


def load_production_rubric_bundle(
    rubric_dir: str | Path | None = None,
    *,
    include_xuanhuan_style: bool = True,
) -> ProductionRubricBundle:
    root = Path(rubric_dir) if rubric_dir is not None else default_rubric_dir()
    policy = ProductionAuditPolicy.model_validate(_read_json(root / "production_protocol-v1.json"))
    dimensions: list[ProductionRubricDimension] = []
    revisions: dict[str, str] = {}
    seen: set[str] = set()

    for file_name in policy.rubric_files:
        if file_name == "xuanhuan-v2-rubrics.json" and not include_xuanhuan_style:
            continue
        payload = _read_json(root / file_name)
        rubric_name = str(payload.get("name") or Path(file_name).stem)
        revisions[rubric_name] = str(payload.get("revision") or "unknown")
        default_category = str(payload.get("category") or "").strip()
        raw_dimensions = payload.get("dimensions")
        if not isinstance(raw_dimensions, list):
            raise ValueError(f"rubric has no dimension list: {root / file_name}")
        for raw in raw_dimensions:
            if not isinstance(raw, dict):
                raise ValueError(f"invalid rubric dimension in {root / file_name}")
            value = dict(raw)
            value["category"] = str(value.get("category") or default_category or "style")
            value.setdefault("audit_question", f"画面是否达到“{value.get('name', value.get('id', '该维度'))}”？")
            dimension = ProductionRubricDimension.model_validate(value)
            if dimension.id in seen:
                raise ValueError(f"duplicate production rubric dimension: {dimension.id}")
            seen.add(dimension.id)
            dimensions.append(dimension)

    if not dimensions:
        raise ValueError("production image audit rubric bundle is empty")
    return ProductionRubricBundle(
        policy=policy,
        rubric_revisions=revisions,
        dimensions=dimensions,
    )


def render_production_rubric(bundle: ProductionRubricBundle) -> str:
    anchors = "\n".join(
        f"- {score}: {description}" for score, description in bundle.policy.scale_anchors.items()
    )
    evidence_rules = "\n".join(f"- {rule}" for rule in bundle.policy.evidence_rules)
    dimensions = "\n".join(
        (
            f"- `{item.id}` | category={item.category} | weight={item.weight:g} | gate={str(item.gate).lower()} | "
            f"applies_to={','.join(item.applies_to)} | {item.name} | {item.audit_question}"
        )
        for item in bundle.dimensions
    )
    revisions = ", ".join(f"{name}@{revision}" for name, revision in bundle.rubric_revisions.items())
    return (
        f"Protocol: {bundle.policy.name}@{bundle.policy.revision}\n"
        f"Rubrics: {revisions}\n"
        "Score policy: pure weighted scoring with no score caps. Severity and individual low scores "
        "must never clamp or replace the weighted score.\n"
        f"Formula: {bundle.policy.score_formula}\n\n"
        f"Scale anchors:\n{anchors}\n\n"
        f"Evidence rules:\n{evidence_rules}\n\n"
        f"Dimensions:\n{dimensions}"
    )


def aggregate_dimension_scores(
    bundle: ProductionRubricBundle,
    scores: Mapping[str, float | None],
) -> tuple[float, dict[AuditCategory, float]]:
    category_values: dict[AuditCategory, list[tuple[float, float]]] = defaultdict(list)
    for dimension in bundle.dimensions:
        score = scores.get(dimension.id)
        if score is None:
            continue
        numeric = float(score)
        if numeric < 0 or numeric > 10:
            raise ValueError(f"rubric score out of range for {dimension.id}: {numeric}")
        category_values[dimension.category].append((dimension.weight, numeric))

    category_scores: dict[AuditCategory, float] = {}
    for category, values in category_values.items():
        total_weight = sum(weight for weight, _score in values)
        category_scores[category] = round(
            sum(weight * score for weight, score in values) / total_weight,
            4,
        )
    if not category_scores:
        raise ValueError("production audit has no applicable dimension scores")

    available_weight = sum(bundle.policy.category_weights[category] for category in category_scores)
    if available_weight <= 0:
        raise ValueError("production audit has no weighted categories")
    weighted_score = sum(
        score * bundle.policy.category_weights[category]
        for category, score in category_scores.items()
    ) / available_weight
    return round(weighted_score, 4), category_scores


__all__ = [
    "AuditCategory",
    "ProductionAuditPolicy",
    "ProductionRubricBundle",
    "ProductionRubricDimension",
    "aggregate_dimension_scores",
    "default_rubric_dir",
    "load_production_rubric_bundle",
    "render_production_rubric",
]
