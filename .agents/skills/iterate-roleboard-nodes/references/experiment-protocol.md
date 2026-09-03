# Roleboard node evolution protocol

## Contents

1. Experimental unit and invariants
2. Budget tiers and call accounting
3. Dataset and holdout allocation
4. Phase 1: image-generation contract
5. Phase 2: Gemini prompt compiler
6. Phase 3: audit and repair loop
7. Selection, checkpoint, and stopping rules
8. Supervised visual-review procedure
9. Failure handling

## 1. Experimental unit and invariants

Treat a candidate as a versioned bundle with an immutable ID and hash. A bundle may contain only the variable authorized for its phase.

### Freeze in every comparison round

- role/appearance source facts;
- key-vision file bytes;
- spatial-template file bytes;
- image provider, model, quality, size, and `n=1`;
- text provider/model when applicable;
- number of samples per role;
- candidate-to-role allocation;
- judge prompt, rubric revision, and thresholds;
- image preprocessing and contact-sheet layout;
- concurrency unless throttling forces a recorded reduction.

### Change in only one named family

- Phase 1: image wrapper or reference policy;
- Phase 2: Gemini context/compiler or, in a separate round, temperature;
- Phase 3: audit prompt/rubric/normalizer or, in a separate round, repair strategy.

Do not merge two challengers after observing their images and call the result tested. The integration must enter a new round as its own candidate.

## 2. Budget tiers and call accounting

Choose a tier before paid calls. Budgets are ceilings, not targets.

| Tier | Image/edit calls | Gemini prompt calls | Audit calls | Intended use |
|---|---:|---:|---:|---|
| diagnostic | 24 | 32 | 24 | expose broken contracts; no production promotion |
| standard | 96 | 128 | 96 | one complete three-phase pass with replication |
| deep | 180 | 240 | 180 | multiple cycles, holdout, repair A/B, convergence |

Recommended allocation for a standard run:

- Phase 1 image generation: 48 image calls;
- Phase 2 prompt compilation: 64 Gemini calls and 32 downstream image calls;
- Phase 3 audit/repair: 64 audit calls and up to 16 edit/reroll image calls;
- reserve the remainder for transport replacements and final production checkpoint.

Before dispatch, append a reservation event containing call kind, count, candidate IDs, sample IDs, and phase. A provider or transport failure consumes budget. It receives no visual score.

Replacement policy:

1. Allow one same-request replacement only for a recorded transient transport failure that returned no image/output.
2. If the replacement fails identically, allow one newly dispatched independent sample only when the checkpoint would otherwise be incomplete.
3. Do not retry safety, validation, schema, or user-correctable failures without changing the responsible input.
4. Never backfill a missing result with an old image.

## 3. Dataset and holdout allocation

Default current-project split:

- exploration: 叶凡/base, 柳菡烟/base, 李德海/base;
- holdout: 叶凡/youth_80_years_ago;
- cast set: all four appearances together;
- variant consistency pair: 叶凡/base versus 叶凡/youth_80_years_ago.

Why this split:

- the elderly lead tests age structure, wear, and non-beautified facial identity;
- the young woman tests avoidance of porcelain-face genericity and inappropriate transfer from the male template;
- the adult functional man tests hierarchy-appropriate costume design without “hero upgrade”;
- the youth variant tests same-person continuity across an 80-year age transformation.

Do not tune candidate wording on holdout images. If the holdout reveals a new failure class, record it, finish the current selection without changing the candidate, then start a new cycle with a newly declared hypothesis and a new holdout if available.

If only the four current appearances exist, the youth appearance may remain the fixed holdout across cycles, but never expose its candidate labels or scores before exploration selection.

## 4. Phase 1: image-generation contract

### Objective

Find the most reliable final prompt wrapper and reference policy for GPT-Image-2 while character facts are hand-authored and fixed. Answer:

- Does the white template improve exact layout without leaking its male-warrior identity?
- Does the key vision carry style more reliably than a generated anchor roleboard?
- Does the anchor roleboard cause face/body/costume homogenization?
- Which wording makes face geometry, body proportion, restrained design, and three-view consistency observable?

### Round 0: contract calibration

Generate one exploration sample for each of these diagnostic candidates:

1. `p1-current`: exact current production prompt and reference chain.
2. `p1-conflict-free`: remove only the 2D-ink versus 3D conflict; keep reference behavior unchanged.
3. `p1-spatial-style-explicit`: spatial template plus key vision with explicit, disjoint reference roles; no cross-role anchor.
4. `p1-spatial-only-control`: spatial template without key vision, used only to measure template identity leakage.
5. `p1-style-only-control`: key vision without template, used only to measure layout loss.

Do not select the spatial-only or style-only controls for production unless they independently satisfy both required contracts. Their purpose is causal diagnosis.

### Round 1: wrapper families

Use the best eligible reference policy from Round 0 for all candidates:

1. incumbent control;
2. `layout-proof`: strengthen exactly-three-view order, full-body scale, shared baseline, spacing, profile purity, and neutral stance;
3. `identity-proof`: strengthen one-person/three-view identity, face geometry, hair, body proportion, wardrobe invariants, and variant identity;
4. `restrained-design`: strengthen role-specific silhouette, material logic, ornament budget, and anti-generic design without adding story facts;
5. `balanced`: integrate only the shortest successful clauses from 2–4 in declared priority order.

### Round 2+: evidence-driven families

Derive at most four challengers from failures reproduced in at least two samples or two roles. Examples:

- profile view repeatedly becomes three-quarter view;
- heads or bodies vary across views;
- template sword/crown/armor leaks into targets;
- young characters share the same “perfect oval face”;
- roles inherit anchor facial structure;
- garments become over-designed game skins;
- facial detail is too small despite valid layout;
- style matches the template's white clay instead of the key vision.

Name the exact failure and the single control family before generating.

### Phase-1 round allocation

For five candidates:

1. exploration: `5 candidates x 3 roles x 1 sample = 15 images`;
2. knockout replication: `top 2 x 3 roles x 1 additional sample = 6 images`;
3. provisional selection: choose using all six samples per candidate;
4. holdout: `winner + incumbent x 1 holdout x 2 samples = 4 images`;
5. checkpoint: `winner x 4 appearances x 2 independent samples = 8 images`.

One complete phase-1 cycle therefore uses up to 33 successful image samples, plus authorized transport replacements. Reduce the number of challengers before reducing role coverage.

### Phase-1 eligibility

Require all of the following:

- no fatal gate failure in checkpoint samples;
- asset-scope mean at least 8.0 and minimum at least 7.2;
- cast-scope mean at least 7.8;
- exact-three-view, same-identity, source-fidelity, severe-anatomy, cleanliness, and reference-contamination gates pass in every holdout/checkpoint sample;
- pairwise cast-clone gate passes;
- checkpoint population variance no greater than 0.35;
- no role regresses more than 0.30 against the incumbent's paired exploration score.

If no candidate is eligible, keep the incumbent only as a diagnostic control and start another round. Do not lower gates to manufacture a winner.

## 5. Phase 2: Gemini prompt compiler

### Objective

Determine under which context, instruction, and temperature Gemini produces the best prompts under the frozen Phase-1 image system.

### Context/compiler candidates

Start with five templates:

1. `p2-current`: selected production prompt template exactly as-is.
2. `p2-minimum-sufficient`: appearance hard facts, designable axes, forbidden states, style/reference contract, and output schema only.
3. `p2-cast-aware`: candidate 2 plus a compact contrast matrix for all roles.
4. `p2-rubric-compiler`: candidate 3 plus silent fact/design/layout/self-audit instructions and observable output requirements.
5. `p2-evidence-optimizer`: candidate 4 plus only the repeated visible failure evidence from Phase 1.

Do not add complete novels, project titles, project IDs, file paths, episode counts, or unrelated production metadata. Test a story-evidence excerpt only if a stable visual fact is absent from the structured appearance and directly changes the role design.

### Text-first funnel

For each candidate:

1. produce two Gemini outputs for each exploration appearance;
2. validate exact JSON schema and reject invalid output without repair in the scoring set;
3. score with `roleboard-prompt-rubric-v1.json` using anonymized outputs;
4. retain only the top two eligible candidates for downstream images.

Default text budget: `5 candidates x 3 roles x 2 outputs = 30 Gemini calls`.

### Downstream image validation

For each of the top two candidates:

- generate one downstream image from every retained Gemini output: 12 images total;
- select a provisional compiler by 30% prompt score and 70% gated image score;
- run two independent Gemini-to-image samples on the holdout for the winner and incumbent: 4 images plus 4 Gemini calls;
- run a final checkpoint with two independent full-pipeline samples on all four appearances: 8 images plus 8 Gemini calls.

Do not reuse the same Gemini output for “independent pipeline” checkpoint samples. A stable compiler must survive fresh text sampling and fresh image sampling.

### Temperature sweep

After freezing context and compiler wording, compare temperatures `0.15`, `0.30`, and `0.45` unless the provider supports a narrower valid range. Use two text outputs per exploration role per temperature. Score text first, then generate downstream images only for the best two temperatures.

Select lower temperature when composite scores differ by less than 0.05 and it has lower invalid-output rate or variance.

### Phase-2 eligibility

- JSON validity rate 100% in checkpoint;
- no hard-fact, extra-role, transient-state, media-conflict, or view-contract gate failure;
- prompt mean at least 8.2;
- downstream image gate pass and thresholds equal to Phase 1;
- cast distinction does not regress from the frozen direct-prompt checkpoint by more than 0.10;
- unsupported design choice count does not increase without a visible-quality gain;
- full-pipeline population variance no greater than 0.40.

## 6. Phase 3: audit and repair loop

### Gold-set construction

Build at least 24 labeled cases:

- 6 accepted roleboards, including repeated samples and a valid age variant;
- 4 layout failures: extra/missing view, crop, unequal scale, wrong profile;
- 3 identity failures: face/hair/body changes across views;
- 3 cast failures: near-clone faces, silhouettes, or costumes;
- 2 reference-contamination failures: template warrior or key-vision character leakage;
- 2 anatomy failures;
- 2 style/design failures: medium mismatch, AI gloss, ornament overload;
- 2 cleanliness failures: text, watermark, collage marks, stray props.

Include both obvious and borderline examples. Two independent human/Codex passes must agree on the gold label or mark the case `ambiguous` and exclude it from approval-accuracy calculations.

### Required action labels

- `accept`: all delivery gates pass and score meets threshold.
- `edit`: identity/layout are fundamentally correct; the defect is local and can be described without redesigning the board.
- `reroll`: the prompt is sound but the image has global stochastic failure, severe anatomy, wrong layout, or reference leakage.
- `regenerate_prompt`: the current prompt is generic, contradictory, incomplete, or itself asks for the failed output.
- `block_upstream`: source identity facts/variant relations are missing or contradictory, so image repair would fabricate production identity.

### Audit candidates

1. current generic single-image audit;
2. rubric prompt, still trusting the model's final decision;
3. rubric prompt plus required dimension coverage and deterministic score/approval normalization;
4. candidate 3 plus cast-level audit and action routing;
5. candidate 4 plus visible failure memory limited to repeated false accepts/false rejects.

### Audit metrics

Report:

- critical false accept count;
- false accept and false reject rates;
- macro-F1 across five action labels;
- exact action accuracy;
- gate-detection recall;
- dimension evidence completeness;
- revised-prompt locality and executability;
- cast-clone recall;
- mean image calls and audit calls to convergence;
- edit success versus reroll success by defect class.

### Audit eligibility

- zero critical false accepts;
- gate-detection recall 100% on the gold set;
- exact action accuracy at least 85%;
- macro-F1 at least 0.80;
- false reject rate on accepted samples at most 10%;
- required-dimension coverage 100%;
- cast-clone recall 100%;
- no accepted asset produced by forced acceptance;
- at least 80% of repairable cases converge within two repair attempts.

### Edit-versus-reroll A/B

Use only edit-eligible cases. For each defect class, compare targeted edit and fresh reroll from the same starting asset and same audit feedback. Keep model, refs, size, and quality fixed. Score preservation of identity, unaffected views, style, and layout as well as defect correction.

Adopt edit for a defect class only if it has both:

- at least the same final acceptance rate as reroll;
- at least 0.20 higher preservation score or at least 20% fewer image calls to acceptance.

Otherwise use reroll. After one failed targeted edit, allow one reroll; do not repeatedly edit a drifting image.

## 7. Selection, checkpoint, and stopping rules

### Candidate ranking

Rank only eligible candidates. Compare, in order:

1. fatal/critical gate pass;
2. holdout pass;
3. checkpoint mean;
4. minimum role score;
5. cast distinction;
6. lower population variance;
7. lower complexity and fewer words/references;
8. lower call failure rate.

### New-high rule

A checkpoint is a new high only when:

- its mean exceeds the previous best by at least 0.05;
- no hard-gate pass rate regresses;
- cast distinction does not regress by more than 0.05;
- no individual role regresses by more than 0.25;
- the improvement appears in at least two roles or in one role plus the holdout.

### Stop immediately when

- the authorized call ceiling would be exceeded;
- three complete cycles produce no new high;
- two successive rounds reproduce the same fatal failure without a new causal hypothesis;
- the remaining blocker is upstream source quality or unavailable provider capability;
- all phase eligibility conditions pass and a fresh final checkpoint confirms the champion.

Do not stop merely because one aesthetically strong image appears.

## 8. Supervised visual-review procedure

Run three review passes without viewing prompt text or candidate names:

### Pass A: thumbnail/cast gestalt

Check exact panel count, spacing, silhouette readability, role-to-role distinction, generic face repetition, ornament density, and shared style.

### Pass B: full image

Check role facts, face geometry, age, body proportion, profile/back-view correctness, costume logic, material treatment, and key-vision style transfer.

### Pass C: detail crops

Check eyes, hairline, ears/profile, hands/fingers, feet/sole contact, garment closures, repeated motifs, boundary integration, extra props, text-like marks, and template contamination.

For every score below 8 or above 9, write concrete pixel evidence. Do not write only “高级”, “电影感”, “很 AI”, or “设计更好”.

Add a new rubric dimension only when the defect is:

1. directly visible;
2. reproduced in at least two independent samples;
3. not already explained by an existing dimension;
4. plausibly controllable by the node being optimized.

Freeze the revised rubric before rescoring all compared candidates.

## 9. Failure handling

Separate these statuses:

- `success`: usable output exists and is scored;
- `transport_failure`: no output due to connection/provider transport;
- `provider_rejection`: safety or provider validation rejection;
- `schema_failure`: malformed Gemini/audit output;
- `visual_failure`: output exists but fails rubric;
- `infrastructure_incomplete`: replacement policy exhausted;
- `blocked_upstream`: source contract prevents legitimate generation.

Never assign a visual score to a missing output. Never infer that a transport-failed candidate is worse. A candidate lacking required role coverage cannot win; defer selection or run an authorized replacement.
