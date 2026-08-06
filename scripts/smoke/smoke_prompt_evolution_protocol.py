from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.prompt_evolution.protocol import (  # noqa: E402
    AxisDiagnosis,
    BranchProposal,
    DimensionAssessment,
    EvolutionProposal,
    HardFailure,
    JudgeReport,
    PatchConstraints,
    Region,
    SceneSpec,
    TemplatePatch,
    TrialOutcome,
    aggregate_judges,
    apply_template_patches,
    build_gemini_batch,
    decide_holdout,
    decide_round,
    load_rubric_bundle,
)


RUBRICS = SRC / "autodrama" / "prompts" / "image_rubrics"
TEMPLATE = SRC / "autodrama" / "prompts" / "key_vision_prompt" / "default.md"


def make_report(reviewer: str, weapon_score: float, *, hard_failure: bool) -> JudgeReport:
    bundle = load_rubric_bundle(
        RUBRICS / "general_rubrics-v2.json",
        RUBRICS / "xuanhuan-v2-rubrics.json",
    )
    assessments = []
    for dimension in bundle.dimensions.values():
        score = weapon_score if dimension.id == "prop_rigidity_topology" else 9.0
        assessments.append(
            DimensionAssessment(
                dimension_id=dimension.id,
                score=score,
                severity="critical" if dimension.id == "prop_rigidity_topology" and score <= 4 else "minor",
                evidence="Visible localized image evidence.",
                defect=(
                    "The rigid spear visibly forks into a Y topology."
                    if dimension.id == "prop_rigidity_topology"
                    else "One insignificant local imperfection remains."
                ),
                regions=(
                    [Region(label="forked spear", x1=0.4, y1=0.3, x2=0.7, y2=0.7)]
                    if dimension.id == "prop_rigidity_topology" and score <= 4
                    else []
                ),
            )
        )
    failures = (
        [
            HardFailure(
                failure_class="physical_critical",
                dimension_ids=["prop_rigidity_topology"],
                evidence="A single straight spear becomes a visible Y-shaped object.",
                regions=[Region(label="fork", x1=0.4, y1=0.3, x2=0.7, y2=0.7)],
            )
        ]
        if hard_failure
        else []
    )
    return JudgeReport(
        reviewer=reviewer,
        mode="combined",
        image_id="r02-combat",
        assessments=assessments,
        hard_failures=failures,
        overall_observation="Polished image with a decisive rigid-weapon topology failure.",
    )


def main() -> None:
    bundle = load_rubric_bundle(
        RUBRICS / "general_rubrics-v2.json",
        RUBRICS / "xuanhuan-v2-rubrics.json",
    )
    assert bundle.dimensions["force_path"].category == "physical"
    assert bundle.dimensions["x2_skin_finish"].category == "style"

    aggregated = aggregate_judges(
        make_report("primary", 3, hard_failure=True),
        make_report("gpt-5.6-sol", 8, hard_failure=False),
        bundle,
    )
    assert aggregated.raw_score > 8
    assert aggregated.final_score == 4.9
    assert aggregated.score_cap == 4.9
    assert aggregated.adjudication_required
    assert "prop_rigidity_topology" in aggregated.disagreements
    derived_cap = aggregate_judges(
        make_report("primary", 4, hard_failure=False),
        make_report("gpt-5.6-sol", 4, hard_failure=False),
        bundle,
    )
    assert derived_cap.final_score == 4.9

    markdown = TEMPLATE.read_text(encoding="utf-8")
    replacement = TemplatePatch(
        operation="replace",
        target_section="cinematic_precision_rules",
        match=(
            "Treat ownership as exclusive: assign each active hand or foot only its declared contact, "
            "keep every forbidden or free limb visibly separated, and never let effects hide this ledger."
        ),
        text=(
            "Treat ownership as exclusive: each active hand or foot has one declared contact; "
            "show a clean gap around every free or forbidden limb, with effects behind the ledger."
        ),
        reason="The old clause is verbose and weakly localizes exclusion gaps.",
        expected_gain="Shorter wording should improve visible hand ownership and contact gaps.",
        risk="May over-separate relaxed hands.",
        evidence_scene_ids=["combat"],
    )
    deletion = TemplatePatch(
        operation="delete",
        target_section="prompt_budget",
        match="Avoid redundant quality superlatives and contradictory lens jargon.",
        reason="This duplicates the surrounding budget and cleanup instructions.",
        expected_gain="Deletion reduces template competition without removing spatial evidence.",
        risk="Gemini may reintroduce lens jargon.",
        evidence_scene_ids=["dialogue"],
    )
    kept = TemplatePatch(
        operation="keep",
        target_section="shot_contract_design",
        match="Fix one camera anchor",
        reason="The hard camera anchor remains consistently beneficial.",
        expected_gain="Keeping it preserves the spatial control baseline.",
        risk="No direct regression expected.",
    )
    applied = apply_template_patches(
        markdown,
        [replacement, deletion, kept],
        PatchConstraints(
            required_literals=["one continuous physical location", "Fix one camera anchor"],
            forbidden_terms=["叶凡"],
        ),
    )
    assert applied.final_chars < applied.original_chars
    assert "Avoid redundant quality superlatives" not in applied.markdown

    axes = [
        AxisDiagnosis(
            axis=axis,
            observed_pattern="A visible cross-scene defect pattern persists.",
            evidence_scene_ids=["s1", "s2"],
            suspected_template_cause="The current instruction is either weak or over-constrained.",
            recommended_direction="replace",
        )
        for axis in (
            "anatomy_force",
            "prop_space_topology",
            "camera_episode_frame",
            "material_light_low_ai",
        )
    ]
    proposal = EvolutionProposal(
        axes=axes,
        branches=[
            BranchProposal(branch_id="B", hypothesis="Clarify exclusive contact with fewer words.", patches=[replacement]),
            BranchProposal(branch_id="C", hypothesis="Remove a low-value duplicate cleanup clause.", patches=[deletion]),
        ],
    )
    common_inputs = {
        "story_context": "A generic test scene.",
        "global_visual_style": "A generic visual style.",
        "director_brief": "Show a causal narrative instant.",
        "render_contract": "Landscape episode frame.",
        "continuity_contract": "No extra constraints.",
    }
    scenes = [
        SceneSpec(id=f"s{index}", title=f"Scene {index}", kind=kind, inputs=common_inputs)
        for index, kind in enumerate(("dialogue", "combat", "overhead"), start=1)
    ]
    batch = build_gemini_batch(
        "r01",
        markdown,
        proposal,
        scenes,
        PatchConstraints(required_literals=["one continuous physical location", "Fix one camera anchor"]),
    )
    assert len(batch["items"]) == 9
    assert {item["strategy"] for item in batch["items"]} == {"A", "B", "C"}

    outcomes = []
    values = {
        "A": [7.0, 7.0, 7.0],
        "B": [8.0, 8.0, 6.5],
        "C": [6.8, 7.1, 6.9],
    }
    for branch_id, scores in values.items():
        for scene, score in zip(scenes, scores, strict=True):
            outcomes.append(
                TrialOutcome(
                    scene_id=scene.id,
                    branch_id=branch_id,
                    final_score=score,
                    complexity_penalty=0.05 if branch_id != "A" else 0,
                )
            )
    decision = decide_round(outcomes)
    assert decision.promote and decision.winner == "B"

    holdout_outcomes = []
    for branch_id, scores in {"A": [7.0, 7.0, 7.0], "B": [7.5, 7.3, 6.8]}.items():
        for scene, score in zip(scenes, scores, strict=True):
            holdout_outcomes.append(
                TrialOutcome(
                    scene_id=scene.id,
                    branch_id=branch_id,
                    final_score=score,
                    complexity_penalty=0.05 if branch_id == "B" else 0,
                )
            )
    holdout = decide_holdout(holdout_outcomes)
    assert holdout.passed and holdout.candidate_branch == "B"

    regressed = [
        item.model_copy(update={"final_score": score})
        for item, score in zip(
            holdout_outcomes,
            [7.0, 7.0, 7.0, 7.5, 6.8, 6.7],
            strict=True,
        )
    ]
    assert not decide_holdout(regressed).passed
    print("prompt evolution protocol smoke: OK")


if __name__ == "__main__":
    main()
