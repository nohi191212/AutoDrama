from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from statistics import mean, pvariance
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Category = Literal["physical", "contract", "cinematic", "style"]
PatchKind = Literal["add", "replace", "delete", "keep"]
Severity = Literal["none", "minor", "major", "critical"]
FailureClass = Literal[
    "physical_critical",
    "contract_critical",
    "evidence_critical",
    "gate_major",
]


class Region(BaseModel):
    label: str
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)
    x2: float = Field(ge=0, le=1)
    y2: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def ordered(self) -> "Region":
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("region must have positive normalized width and height")
        return self


class HardFailure(BaseModel):
    failure_class: FailureClass
    dimension_ids: list[str] = Field(min_length=1)
    evidence: str = Field(min_length=8)
    regions: list[Region] = Field(min_length=1)


class DimensionAssessment(BaseModel):
    dimension_id: str
    applicable: bool = True
    score: float | None = Field(default=None, ge=0, le=10)
    severity: Severity = "none"
    evidence: str = Field(min_length=8)
    defect: str = ""
    regions: list[Region] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence(self) -> "DimensionAssessment":
        if self.applicable and self.score is None:
            raise ValueError("applicable dimensions require a score")
        if not self.applicable and self.score is not None:
            raise ValueError("non-applicable dimensions must use score=null")
        if self.applicable and self.score is not None and self.score < 10 and not self.defect.strip():
            raise ValueError("every sub-perfect score needs a concrete defect")
        if self.severity in {"major", "critical"} and not self.regions:
            raise ValueError("major and critical defects require normalized image regions")
        return self


class JudgeReport(BaseModel):
    reviewer: str
    mode: Literal["blind", "contract", "combined"]
    image_id: str
    assessments: list[DimensionAssessment]
    hard_failures: list[HardFailure] = Field(default_factory=list)
    overall_observation: str = Field(min_length=8)

    @model_validator(mode="after")
    def unique_dimensions(self) -> "JudgeReport":
        ids = [item.dimension_id for item in self.assessments]
        duplicates = [key for key, count in Counter(ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate dimension assessments: {duplicates}")
        return self


class RubricDimension(BaseModel):
    id: str
    name: str
    category: Category
    weight: float = Field(gt=0)
    gate: bool = False
    applies_to: list[str] = Field(default_factory=lambda: ["all"])
    audit_question: str = ""


class RubricBundle(BaseModel):
    dimensions: dict[str, RubricDimension]


class ScoringPolicy(BaseModel):
    name: str = "image-evaluation-v3"
    category_weights: dict[Category, float] = Field(
        default_factory=lambda: {
            "physical": 0.40,
            "contract": 0.30,
            "cinematic": 0.15,
            "style": 0.15,
        }
    )
    caps: dict[FailureClass, float] = Field(
        default_factory=lambda: {
            "physical_critical": 4.9,
            "contract_critical": 5.9,
            "evidence_critical": 5.9,
            "gate_major": 6.9,
        }
    )
    disagreement_threshold: float = 1.0
    gate_threshold: float = 6.0

    @model_validator(mode="after")
    def weights_sum_to_one(self) -> "ScoringPolicy":
        if abs(sum(self.category_weights.values()) - 1.0) > 1e-6:
            raise ValueError("category_weights must sum to 1")
        return self


class AggregatedDimension(BaseModel):
    dimension_id: str
    category: Category
    score: float
    primary_score: float
    sol_score: float
    evidence: list[str]
    regions: list[Region]
    disagreement: bool = False


class AggregatedReview(BaseModel):
    image_id: str
    raw_score: float
    final_score: float
    score_cap: float | None = None
    category_scores: dict[Category, float]
    dimensions: list[AggregatedDimension]
    hard_failures: list[HardFailure]
    failure_classes: list[FailureClass]
    adjudication_required: bool
    disagreements: list[str]


def load_rubric_bundle(*paths: str | Path) -> RubricBundle:
    dimensions: dict[str, RubricDimension] = {}
    for raw_path in paths:
        data = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        default_category = data.get("category")
        for item in data.get("dimensions", []):
            value = dict(item)
            value["category"] = value.get("category") or default_category or "style"
            dimension = RubricDimension.model_validate(value)
            if dimension.id in dimensions:
                raise ValueError(f"duplicate rubric dimension: {dimension.id}")
            dimensions[dimension.id] = dimension
    if not dimensions:
        raise ValueError("rubric bundle is empty")
    return RubricBundle(dimensions=dimensions)


def merge_review_passes(
    blind: JudgeReport,
    contract: JudgeReport,
    rubric: RubricBundle,
) -> JudgeReport:
    if blind.image_id != contract.image_id:
        raise ValueError("blind and contract passes refer to different images")
    blind_map = {item.dimension_id: item for item in blind.assessments}
    contract_map = {item.dimension_id: item for item in contract.assessments}
    merged: list[DimensionAssessment] = []
    for dimension_id, spec in rubric.dimensions.items():
        source = contract_map if spec.category == "contract" else blind_map
        if dimension_id in source:
            merged.append(source[dimension_id])
    return JudgeReport(
        reviewer=blind.reviewer,
        mode="combined",
        image_id=blind.image_id,
        assessments=merged,
        hard_failures=blind.hard_failures + contract.hard_failures,
        overall_observation=f"BLIND: {blind.overall_observation}\nCONTRACT: {contract.overall_observation}",
    )


def _weighted_category_score(
    items: list[tuple[RubricDimension, float]],
) -> float:
    if not items:
        raise ValueError("cannot score an empty applicable category")
    total_weight = sum(spec.weight for spec, _ in items)
    return sum(spec.weight * score for spec, score in items) / total_weight


def _judge_failure_classes(
    report: JudgeReport,
    rubric: RubricBundle,
    policy: ScoringPolicy,
) -> set[FailureClass]:
    failure_classes: set[FailureClass] = {item.failure_class for item in report.hard_failures}
    for assessment in report.assessments:
        if not assessment.applicable or assessment.score is None:
            continue
        spec = rubric.dimensions.get(assessment.dimension_id)
        if spec is None or not spec.gate or assessment.score >= policy.gate_threshold:
            continue
        failure_classes.add("gate_major")
        if assessment.score <= 4 and spec.category == "physical":
            failure_classes.add("physical_critical")
        if assessment.score <= 4 and spec.category == "contract":
            failure_classes.add(
                "evidence_critical" if assessment.dimension_id == "evidence_survival" else "contract_critical"
            )
    return failure_classes


def aggregate_judges(
    primary: JudgeReport,
    sol: JudgeReport,
    rubric: RubricBundle,
    policy: ScoringPolicy | None = None,
) -> AggregatedReview:
    policy = policy or ScoringPolicy()
    if primary.image_id != sol.image_id:
        raise ValueError("judge reports refer to different images")
    primary_map = {item.dimension_id: item for item in primary.assessments if item.applicable}
    sol_map = {item.dimension_id: item for item in sol.assessments if item.applicable}
    dimensions: list[AggregatedDimension] = []
    category_items: dict[Category, list[tuple[RubricDimension, float]]] = {
        "physical": [],
        "contract": [],
        "cinematic": [],
        "style": [],
    }
    disagreements: list[str] = []

    common = set(primary_map) & set(sol_map) & set(rubric.dimensions)
    if not common:
        raise ValueError("judge reports have no common rubric dimensions")
    for dimension_id in rubric.dimensions:
        if dimension_id not in common:
            continue
        spec = rubric.dimensions[dimension_id]
        left = primary_map[dimension_id]
        right = sol_map[dimension_id]
        assert left.score is not None and right.score is not None
        conservative = spec.category in {"physical", "contract"}
        score = min(left.score, right.score) if conservative else mean([left.score, right.score])
        is_disagreement = abs(left.score - right.score) > policy.disagreement_threshold
        if is_disagreement:
            disagreements.append(dimension_id)
        dimensions.append(
            AggregatedDimension(
                dimension_id=dimension_id,
                category=spec.category,
                score=round(score, 4),
                primary_score=left.score,
                sol_score=right.score,
                evidence=[left.evidence, right.evidence],
                regions=left.regions + right.regions,
                disagreement=is_disagreement,
            )
        )
        category_items[spec.category].append((spec, score))

    category_scores = {
        category: round(_weighted_category_score(items), 4)
        for category, items in category_items.items()
        if items
    }
    missing_categories = set(policy.category_weights) - set(category_scores)
    if missing_categories:
        raise ValueError(f"missing applicable categories in both reviews: {sorted(missing_categories)}")
    raw_score = sum(category_scores[key] * weight for key, weight in policy.category_weights.items())

    failures = primary.hard_failures + sol.hard_failures
    primary_failure_classes = _judge_failure_classes(primary, rubric, policy)
    sol_failure_classes = _judge_failure_classes(sol, rubric, policy)
    failure_classes = primary_failure_classes | sol_failure_classes
    for item in dimensions:
        spec = rubric.dimensions[item.dimension_id]
        if not spec.gate or item.score >= policy.gate_threshold:
            continue
        failure_classes.add("gate_major")
        if item.score <= 4 and spec.category == "physical":
            failure_classes.add("physical_critical")
        if item.score <= 4 and spec.category == "contract":
            failure_classes.add(
                "evidence_critical" if item.dimension_id == "evidence_survival" else "contract_critical"
            )
    caps = [policy.caps[item] for item in failure_classes]
    score_cap = min(caps) if caps else None
    final_score = min(raw_score, score_cap) if score_cap is not None else raw_score

    gate_conflict = primary_failure_classes != sol_failure_classes
    return AggregatedReview(
        image_id=primary.image_id,
        raw_score=round(raw_score, 4),
        final_score=round(final_score, 4),
        score_cap=score_cap,
        category_scores=category_scores,
        dimensions=dimensions,
        hard_failures=failures,
        failure_classes=sorted(failure_classes),
        adjudication_required=bool(disagreements or gate_conflict),
        disagreements=disagreements,
    )


class TemplatePatch(BaseModel):
    operation: PatchKind
    target_section: str
    match: str = ""
    text: str = ""
    reason: str = Field(min_length=8)
    expected_gain: str = Field(min_length=8)
    risk: str = Field(min_length=4)
    evidence_scene_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def operation_fields(self) -> "TemplatePatch":
        if self.operation == "add" and not self.text.strip():
            raise ValueError("add requires text")
        if self.operation == "replace" and (not self.match.strip() or not self.text.strip()):
            raise ValueError("replace requires match and text")
        if self.operation == "delete" and not self.match.strip():
            raise ValueError("delete requires match")
        return self


class PatchConstraints(BaseModel):
    max_operations: int = 4
    max_additions: int = 2
    max_growth_ratio: float = 0.06
    max_growth_chars: int = 500
    duplicate_similarity: float = 0.82
    protected_sections: set[str] = Field(
        default_factory=lambda: {"role", "inputs", "source_hierarchy", "mandatory_spatial_language"}
    )
    required_literals: list[str] = Field(default_factory=list)
    forbidden_terms: list[str] = Field(default_factory=list)


class AppliedTemplate(BaseModel):
    markdown: str
    original_chars: int
    final_chars: int
    operations: list[TemplatePatch]
    complexity_penalty: float


_SECTION_PATTERN = re.compile(r"<(?P<name>[a-zA-Z0-9_]+)>\s*(?P<body>.*?)\s*</(?P=name)>", re.S)


def _section(markdown: str, name: str) -> tuple[re.Match[str], str]:
    for match in _SECTION_PATTERN.finditer(markdown):
        if match.group("name") == name:
            return match, match.group("body")
    raise ValueError(f"template section not found: {name}")


def _replace_section(markdown: str, match: re.Match[str], name: str, body: str) -> str:
    replacement = f"<{name}>\n{body.strip()}\n</{name}>"
    return markdown[: match.start()] + replacement + markdown[match.end() :]


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[a-z0-9]+", left.lower()))
    right_tokens = set(re.findall(r"[a-z0-9]+", right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def apply_template_patches(
    markdown: str,
    patches: list[TemplatePatch],
    constraints: PatchConstraints | None = None,
) -> AppliedTemplate:
    constraints = constraints or PatchConstraints()
    if len(patches) > constraints.max_operations:
        raise ValueError("patch exceeds max_operations")
    if sum(item.operation == "add" for item in patches) > constraints.max_additions:
        raise ValueError("patch exceeds max_additions")
    original = markdown
    existing_units = [unit.strip() for unit in re.split(r"[\n。.!?]+", markdown) if unit.strip()]

    for patch in patches:
        section_match, body = _section(markdown, patch.target_section)
        changed_text = patch.text if patch.operation in {"add", "replace"} else ""
        lowered = changed_text.casefold()
        forbidden = [term for term in constraints.forbidden_terms if term.casefold() in lowered]
        if forbidden:
            raise ValueError(f"scene-specific terms leaked into template patch: {forbidden}")
        if patch.operation == "add":
            if any(_token_similarity(changed_text, unit) >= constraints.duplicate_similarity for unit in existing_units):
                raise ValueError("added rule is semantically duplicative")
            body = f"{body.rstrip()}\n{changed_text.strip()}"
        elif patch.operation == "replace":
            if patch.match not in body:
                raise ValueError(f"replace match not found in <{patch.target_section}>")
            body = body.replace(patch.match, patch.text, 1)
        elif patch.operation == "delete":
            if patch.target_section in constraints.protected_sections:
                raise ValueError(f"delete is forbidden in protected section <{patch.target_section}>")
            if patch.match not in body:
                raise ValueError(f"delete match not found in <{patch.target_section}>")
            body = body.replace(patch.match, "", 1)
        elif patch.operation == "keep" and patch.match and patch.match not in body:
            raise ValueError(f"keep assertion not found in <{patch.target_section}>")
        markdown = _replace_section(markdown, section_match, patch.target_section, body)

    for literal in constraints.required_literals:
        if literal not in markdown:
            raise ValueError(f"patch removed required template invariant: {literal!r}")
    growth = len(markdown) - len(original)
    allowed_growth = min(
        constraints.max_growth_chars,
        max(0, round(len(original) * constraints.max_growth_ratio)),
    )
    if growth > allowed_growth:
        raise ValueError(f"template grew by {growth} chars; allowed growth is {allowed_growth}")
    changed_ops = sum(item.operation != "keep" for item in patches)
    complexity_penalty = max(0, growth) / max(1, len(original)) * 2.0 + changed_ops * 0.015
    return AppliedTemplate(
        markdown=markdown,
        original_chars=len(original),
        final_chars=len(markdown),
        operations=patches,
        complexity_penalty=round(complexity_penalty, 4),
    )


class AxisDiagnosis(BaseModel):
    axis: Literal[
        "anatomy_force",
        "prop_space_topology",
        "camera_episode_frame",
        "material_light_low_ai",
    ]
    observed_pattern: str = Field(min_length=8)
    evidence_scene_ids: list[str] = Field(min_length=1)
    suspected_template_cause: str = Field(min_length=8)
    recommended_direction: Literal["add", "replace", "delete", "keep"]


class BranchProposal(BaseModel):
    branch_id: Literal["B", "C"]
    hypothesis: str = Field(min_length=8)
    patches: list[TemplatePatch] = Field(max_length=4)


class EvolutionProposal(BaseModel):
    axes: list[AxisDiagnosis] = Field(min_length=4, max_length=4)
    branches: list[BranchProposal] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def complete_axes_and_branches(self) -> "EvolutionProposal":
        required_axes = {
            "anatomy_force",
            "prop_space_topology",
            "camera_episode_frame",
            "material_light_low_ai",
        }
        if {item.axis for item in self.axes} != required_axes:
            raise ValueError("proposal must diagnose each of the four axes exactly once")
        if {item.branch_id for item in self.branches} != {"B", "C"}:
            raise ValueError("proposal must contain exactly branches B and C")
        return self


class SceneSpec(BaseModel):
    id: str
    kind: Literal["dialogue", "combat", "overhead", "detail", "face", "other"] = "other"
    title: str
    inputs: dict[str, str]
    contract: dict[str, Any] = Field(default_factory=dict)


def render_template(markdown: str, inputs: dict[str, str]) -> str:
    rendered = markdown
    for key, value in inputs.items():
        rendered = rendered.replace("{{" + key + "}}", value)
    unresolved = sorted(set(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", rendered)))
    if unresolved:
        raise ValueError(f"scene is missing template inputs: {unresolved}")
    return rendered


def build_gemini_batch(
    round_id: str,
    champion_markdown: str,
    proposal: EvolutionProposal,
    scenes: list[SceneSpec],
    constraints: PatchConstraints | None = None,
) -> dict[str, Any]:
    if len(scenes) != 3:
        raise ValueError("a tournament round requires exactly three scenes")
    constraints = constraints or PatchConstraints()
    branches: dict[str, AppliedTemplate] = {
        "A": AppliedTemplate(
            markdown=champion_markdown,
            original_chars=len(champion_markdown),
            final_chars=len(champion_markdown),
            operations=[],
            complexity_penalty=0,
        )
    }
    for branch in proposal.branches:
        branches[branch.branch_id] = apply_template_patches(
            champion_markdown, branch.patches, constraints
        )
    items: list[dict[str, Any]] = []
    for scene in scenes:
        for branch_id in ("A", "B", "C"):
            applied = branches[branch_id]
            items.append(
                {
                    "id": f"{round_id}--{branch_id}--{scene.id}",
                    "title": f"{round_id} · {branch_id} · {scene.title}",
                    "strategy": branch_id,
                    "scene_id": scene.id,
                    "prompt": render_template(applied.markdown, scene.inputs),
                    "template_sha256": hashlib.sha256(applied.markdown.encode("utf-8")).hexdigest(),
                    "complexity_penalty": applied.complexity_penalty,
                }
            )
    return {
        "round_id": round_id,
        "design": "3 scenes x control A + experimental B/C",
        "settings": {"model": "gemini-3.6-flash", "temperature": 0.2},
        "branches": {
            key: {
                "template_sha256": hashlib.sha256(value.markdown.encode("utf-8")).hexdigest(),
                "template_chars": value.final_chars,
                "complexity_penalty": value.complexity_penalty,
                "patches": [item.model_dump() for item in value.operations],
            }
            for key, value in branches.items()
        },
        "scenes": [scene.model_dump() for scene in scenes],
        "items": items,
    }


class PreviousTrial(BaseModel):
    scene_id: str
    branch_id: str
    image_path: str
    final_image_prompt: str
    evaluation: dict[str, Any]


def build_evolution_prompt(
    champion_markdown: str,
    previous_trials: list[PreviousTrial],
    patch_constraints: PatchConstraints | None = None,
    history: list[dict[str, Any]] | None = None,
) -> str:
    constraints = patch_constraints or PatchConstraints()
    evidence = [
        {
            "image_attachment_index": index + 1,
            "scene_id": trial.scene_id,
            "branch_id": trial.branch_id,
            "final_image_prompt": trial.final_image_prompt,
            "evaluation": trial.evaluation,
        }
        for index, trial in enumerate(previous_trials)
    ]
    schema = EvolutionProposal.model_json_schema()
    return (
        "You are evolving a general-purpose Gemini meta-template that compiles story scenes into GPT-Image-2 prompts. "
        "Inspect every attached previous-round image at original detail together with its independent audit. "
        "Diagnose all four axes from visible evidence; the axes are an analysis frame, not fixed edits. "
        "Propose exactly two competing branches B and C while A remains the unchanged control. "
        "Each branch may add, replace, delete, or explicitly keep rules. Prefer replacement/deletion when a rule is redundant, "
        "over-prescriptive, correlated with worse output, or merely restates another rule. Do not optimize for a named scene, "
        "character, weapon, camera angle, or current test set. Preserve the hard spatial skeleton. "
        "Every changed clause needs image evidence, an expected gain, and a regression risk. Return JSON only.\n\n"
        f"PATCH CONSTRAINTS:\n{constraints.model_dump_json(indent=2)}\n\n"
        f"CURRENT CHAMPION TEMPLATE:\n{champion_markdown}\n\n"
        f"PREVIOUS TRIAL EVIDENCE (attachments are in this exact order):\n{json.dumps(evidence, ensure_ascii=False, indent=2)}\n\n"
        f"PATCH HISTORY:\n{json.dumps(history or [], ensure_ascii=False, indent=2)}\n\n"
        f"REQUIRED OUTPUT SCHEMA:\n{json.dumps(schema, ensure_ascii=False)}"
    )


class TrialOutcome(BaseModel):
    scene_id: str
    branch_id: Literal["A", "B", "C"]
    final_score: float = Field(ge=0, le=10)
    hard_failure_classes: list[FailureClass] = Field(default_factory=list)
    adjudication_required: bool = False
    complexity_penalty: float = Field(default=0, ge=0)


class BranchStats(BaseModel):
    branch_id: str
    mean: float
    variance: float
    adjusted_mean: float
    wins_vs_control: int
    new_hard_failures: int
    eligible: bool


class RoundDecision(BaseModel):
    winner: Literal["A", "B", "C"]
    promote: bool
    reason: str
    stats: list[BranchStats]


class HoldoutDecision(BaseModel):
    candidate_branch: Literal["B", "C"]
    passed: bool
    reason: str
    control_mean: float
    candidate_mean: float
    control_adjusted_mean: float
    candidate_adjusted_mean: float
    wins_vs_control: int
    new_hard_failures: int
    unresolved_adjudications: int


def decide_round(outcomes: list[TrialOutcome]) -> RoundDecision:
    if len(outcomes) != 9:
        raise ValueError("round decision requires exactly nine outcomes")
    by_branch: dict[str, dict[str, TrialOutcome]] = {"A": {}, "B": {}, "C": {}}
    for outcome in outcomes:
        if outcome.scene_id in by_branch[outcome.branch_id]:
            raise ValueError("duplicate scene/branch outcome")
        by_branch[outcome.branch_id][outcome.scene_id] = outcome
    scene_ids = set(by_branch["A"])
    if len(scene_ids) != 3 or any(set(values) != scene_ids for values in by_branch.values()):
        raise ValueError("A/B/C must cover the same three scenes")

    stats: list[BranchStats] = []
    for branch_id in ("A", "B", "C"):
        branch = by_branch[branch_id]
        scores = [item.final_score for item in branch.values()]
        penalty = mean([item.complexity_penalty for item in branch.values()])
        wins = 0
        new_failures = 0
        for scene_id, item in branch.items():
            control = by_branch["A"][scene_id]
            if branch_id != "A" and item.final_score > control.final_score:
                wins += 1
            new_failures += len(set(item.hard_failure_classes) - set(control.hard_failure_classes))
        eligible = (
            branch_id == "A"
            or (
                wins >= 2
                and new_failures == 0
                and not any(item.adjudication_required for item in branch.values())
            )
        )
        stats.append(
            BranchStats(
                branch_id=branch_id,
                mean=round(mean(scores), 4),
                variance=round(pvariance(scores), 4),
                adjusted_mean=round(mean(scores) - penalty, 4),
                wins_vs_control=wins,
                new_hard_failures=new_failures,
                eligible=eligible,
            )
        )
    control_stat = stats[0]
    eligible_candidates = [item for item in stats[1:] if item.eligible]
    winner = max(eligible_candidates, key=lambda item: item.adjusted_mean, default=control_stat)
    promote = winner.branch_id != "A" and winner.adjusted_mean > control_stat.adjusted_mean
    return RoundDecision(
        winner=winner.branch_id,
        promote=promote,
        reason=(
            f"Promote {winner.branch_id}: it won {winner.wins_vs_control}/3 scenes without a new hard failure "
            "and retained positive complexity-adjusted gain."
            if promote
            else "Keep control A: no branch satisfied the 2/3 win, no-new-hard-failure, disagreement, and complexity rules."
        ),
        stats=stats,
    )


def decide_holdout(outcomes: list[TrialOutcome]) -> HoldoutDecision:
    if len(outcomes) != 6:
        raise ValueError("holdout decision requires exactly six outcomes")
    branches = {item.branch_id for item in outcomes}
    candidates = branches - {"A"}
    if len(candidates) != 1 or not candidates <= {"B", "C"}:
        raise ValueError("holdout outcomes require control A and exactly one candidate branch")
    candidate_id = next(iter(candidates))
    by_branch: dict[str, dict[str, TrialOutcome]] = {"A": {}, candidate_id: {}}
    for outcome in outcomes:
        if outcome.scene_id in by_branch[outcome.branch_id]:
            raise ValueError("duplicate holdout scene/branch outcome")
        by_branch[outcome.branch_id][outcome.scene_id] = outcome
    scene_ids = set(by_branch["A"])
    if len(scene_ids) != 3 or set(by_branch[candidate_id]) != scene_ids:
        raise ValueError("holdout control and candidate must cover the same three scenes")

    control_scores = [item.final_score for item in by_branch["A"].values()]
    candidate_scores = [item.final_score for item in by_branch[candidate_id].values()]
    control_penalty = mean([item.complexity_penalty for item in by_branch["A"].values()])
    candidate_penalty = mean(
        [item.complexity_penalty for item in by_branch[candidate_id].values()]
    )
    wins = 0
    new_failures = 0
    for scene_id, candidate in by_branch[candidate_id].items():
        control = by_branch["A"][scene_id]
        if candidate.final_score > control.final_score:
            wins += 1
        new_failures += len(
            set(candidate.hard_failure_classes) - set(control.hard_failure_classes)
        )
    unresolved = sum(item.adjudication_required for item in by_branch[candidate_id].values())
    control_mean = mean(control_scores)
    candidate_mean = mean(candidate_scores)
    control_adjusted = control_mean - control_penalty
    candidate_adjusted = candidate_mean - candidate_penalty
    passed = (
        wins >= 2
        and new_failures == 0
        and unresolved == 0
        and candidate_adjusted >= control_adjusted
    )
    return HoldoutDecision(
        candidate_branch=candidate_id,
        passed=passed,
        reason=(
            f"Holdout passed: {candidate_id} won {wins}/3 fixed scenes without a new hard failure or unresolved adjudication."
            if passed
            else "Holdout failed: candidate did not preserve the 2/3 win, no-new-hard-failure, resolved-adjudication, and adjusted-mean constraints."
        ),
        control_mean=round(control_mean, 4),
        candidate_mean=round(candidate_mean, 4),
        control_adjusted_mean=round(control_adjusted, 4),
        candidate_adjusted_mean=round(candidate_adjusted, 4),
        wins_vs_control=wins,
        new_hard_failures=new_failures,
        unresolved_adjudications=unresolved,
    )
