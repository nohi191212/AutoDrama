"""Small, experiment-facing primitives for image-prompt template evolution."""

from .protocol import (
    EvolutionProposal,
    JudgeReport,
    PatchConstraints,
    RoundDecision,
    ScoringPolicy,
    TemplatePatch,
    aggregate_judges,
    apply_template_patches,
    build_evolution_prompt,
    build_gemini_batch,
    decide_round,
    load_rubric_bundle,
)

__all__ = [
    "EvolutionProposal",
    "JudgeReport",
    "PatchConstraints",
    "RoundDecision",
    "ScoringPolicy",
    "TemplatePatch",
    "aggregate_judges",
    "apply_template_patches",
    "build_evolution_prompt",
    "build_gemini_batch",
    "decide_round",
    "load_rubric_bundle",
]
