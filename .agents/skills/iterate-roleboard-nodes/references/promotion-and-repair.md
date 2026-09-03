# Production promotion and roleboard repair policy

## Contents

1. Promotion prerequisites
2. Expected production change surfaces
3. Reference-policy implementation requirements
4. Audit architecture
5. Edit, reroll, prompt regeneration, and upstream block
6. Attempt limits and lineage
7. Validation sequence
8. Rollback and provenance

## 1. Promotion prerequisites

Promote only when the lab contains:

- an eligible Phase-1 image-generation checkpoint;
- an eligible Phase-2 fresh Gemini-to-image checkpoint;
- an eligible Phase-3 audit gold-set result;
- a passing cast audit;
- exact hashes for all champion templates, rubrics, references, and source inputs;
- a report and `champion.json` that say `production_eligible=true`;
- no unresolved input or provider ambiguity.

Do not promote a visual favorite selected from one sample. Do not combine untested clauses during implementation.

## 2. Expected production change surfaces

Inspect and update the smallest coherent current path, usually:

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`;
- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`;
- `autodrama/src/autodrama/services/role_service.py`;
- `autodrama/src/autodrama/prompts/roleboard_prompt/*.md`;
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`;
- `autodrama/src/autodrama/core/schemas.py`;
- `autodrama/src/autodrama/image_audit_rubrics.py` or a dedicated roleboard rubric loader;
- `autodrama/src/autodrama/prompts/image_rubrics/` and a roleboard-specific audit prompt;
- `autodrama/src/autodrama/config.py`, active/example configs, and model catalog when the contract changes;
- focused smoke scripts and mock-provider outputs.

Use one current implementation. If the champion replaces anchor chaining, obsolete style-prefix helpers, a prompt variant, or generic roleboard audit behavior, remove the superseded path and update all callers/fixtures.

Do not repurpose a misleading configuration field. A spatial template is not a style-reference directory. Prefer explicit current fields such as:

- `roleboard_spatial_template_path`;
- `roleboard_reference_policy`;
- `roleboard_base_reference_order`;
- `roleboard_variant_reference_order`.

Name fields according to the validated champion, not according to every experiment candidate.

## 3. Reference-policy implementation requirements

### Request metadata

Record every reference with:

- 1-based request index;
- asset ID/path/hash;
- semantic role: `spatial_template`, `key_vision_style`, `same_role_identity`, or an explicitly validated alternative;
- source role/appearance when applicable;
- whether identity transfer is allowed.

The final prompt must use the same indices as the request order. Add a focused smoke check that fails if prompt indices and actual reference order diverge.

### Base appearances

If independent-base policy wins, every base appearance must receive the same spatial template and key vision. Do not feed another character's roleboard merely to keep style consistent.

### Variants

Provide spatial template, key vision, and the validated same-role base reference in the tested order. Explicitly describe invariant and changed attributes. A youth variant must not inherit elderly wrinkles, posture, current worn clothing, or event state unless source facts require them.

### Template/style isolation

Keep positive responsibility statements near the top of the prompt and explicit exclusions directly after each reference role. Do not rely on a generic negative prompt to stop reference leakage.

## 4. Audit architecture

Implement roleboard audit with two deterministic stages under the same production node.

### Asset pass

For each board, send:

1. current roleboard image;
2. spatial template;
3. key vision;
4. same-role base image for a variant, when applicable.

Supply the hard-fact contract and machine-rendered rubric. Require one assessment for every asset dimension and gate. The judge returns evidence, score, severity, defect region, and recommended action. Code validates coverage, replaces judge-supplied weights/gate flags with trusted rubric values, computes scores, and decides approval.

### Cast pass

After all asset passes succeed, build a contact sheet containing one consistent representative per appearance and send it with a compact cast contract. Require every cast dimension and pairwise duplicate findings. Reject affected assets when cast clone or shared-style gates fail.

Do not mark individual assets permanently accepted before the cast pass. Persist `asset_passed` separately from final `approved`.

### Decision schema

Use a structured decision containing at least:

- assessments;
- detected gate failures;
- recommended action;
- issues and affected regions;
- edit instruction when action is edit;
- complete revised generation prompt when action is regenerate_prompt;
- missing upstream fields when action is block_upstream;
- rationale grounded in pixels.

Ignore or omit a model-generated `approved` field. Approval is code-derived.

## 5. Edit, reroll, prompt regeneration, and upstream block

Use this decision tree:

```text
Source identity/variant facts missing or contradictory?
├─ yes -> block_upstream
└─ no
   Prompt contains contradiction, generic design, wrong layout/style, or transient state?
   ├─ yes -> regenerate_prompt, then generate from scratch
   └─ no
      Identity + exact three-view structure + shared style fundamentally correct?
      ├─ no -> reroll from the frozen prompt and canonical references
      └─ yes
         Defect local, bounded, and editable without redesign?
         ├─ yes -> targeted edit once
         └─ no -> reroll
```

### Edit-eligible defects

- one malformed but non-identity-defining hand or foot;
- a small stray mark, pseudo-text fragment, or isolated extra ornament;
- one local garment closure/seam defect;
- a bounded background blemish;
- a local material inconsistency;
- a small profile-view accessory mismatch when all identity geometry is stable.

### Never use targeted edit for

- missing/extra view or wrong view order;
- crop, unequal scale, overlapping panels, or layout collapse;
- different faces/bodies across views;
- generic/clone face across the cast;
- wrong age, gender presentation, role identity, or wardrobe contract;
- template warrior, sword, armor, crown, or key-vision character leakage;
- major medium/style mismatch;
- global anatomy corruption;
- wrong same-person variant lineage;
- an image whose prompt is itself defective.

### Targeted edit prompt contract

State:

- exact defect and region;
- exact desired visible correction;
- identity, three-view layout, scale, baseline, spacing, face, hair, body, garments, palette, style, and unaffected views that must remain unchanged;
- prohibition on redesign, new props, new text, new panels, or global restyling.

Use the current board as the primary edit reference and retain canonical spatial/style/identity references in the champion-tested order if the provider supports the count.

### Reroll contract

Use the frozen, recorded full generation prompt and canonical references. Add only the audit's shortest causal correction when the experiment proved that feedback improves the failure class. Do not accumulate all historical issues into an ever-growing prompt.

### Prompt regeneration contract

Return to the frozen Gemini compiler with:

- original hard-fact/designable/forbidden contracts;
- the current cast contrast matrix;
- only the current prompt's diagnosed defects;
- no rejected image as factual evidence of who the character should be.

Persist a new prompt version and hash. Generate from scratch.

## 6. Attempt limits and lineage

Recommended initial policy:

- initial generation: attempt 0;
- at most one targeted edit when eligible;
- at most two fresh rerolls after the initial image;
- at most one prompt-regeneration cycle per asset before blocking for review;
- maximum three post-initial image calls per asset;
- no forced acceptance.

Each attempt must record:

- parent asset ID/path/hash;
- prompt version/hash;
- ordered reference IDs/hashes;
- audit revision and issues;
- chosen action and policy reason;
- provider request ID and output hash;
- per-attempt scores and gates;
- final disposition.

Preserve original and intermediate images. Do not overwrite history even when the current generation-node output points to the latest accepted asset.

For cast-level rejection, repair only implicated roles and rerun both their asset pass and the full cast pass. Do not rerun unrelated accepted roleboards.

## 7. Validation sequence

Follow this order after implementation:

1. Parse and validate new prompt/rubric/config assets.
2. Run `python -m compileall` on changed Python packages and smoke scripts.
3. Run focused smoke scripts for:
   - reference order and metadata;
   - base generation path;
   - variant generation path;
   - prompt schema and design-choice logging;
   - audit dimension coverage and deterministic score;
   - every repair action;
   - attempt limit and no-force-accept behavior;
   - cast audit clone detection;
   - preservation of unrelated assets during single-asset repair.
4. Run a fake-provider full chain from `roleboard_prompt` through `roleboard_image_audit`.
5. Run one configured-provider base asset.
6. Run its audit and, if deliberately needed, one controlled repair.
7. Run one same-role variant.
8. Run the full four-appearance cast.
9. Run the cast pass and confirm rejected assets do not enter downstream accepted-asset lookup.

Do not use `pytest`. Put agent-authored verification scripts under repository `scripts/smoke/` and their outputs under repository `.tmp/`.

## 8. Rollback and provenance

Before promotion, record hashes of all replaced production files and current accepted assets. A rollback must restore the complete previous contract, not a mixture of old prompts and new reference ordering.

Persist production provenance containing:

- experiment directory;
- Phase 1/2/3 champion IDs and hashes;
- spatial template and key-vision hashes;
- model/provider parameters;
- rubric revision and thresholds;
- checkpoint scores, variance, and gates;
- validation commands/results;
- promotion timestamp.

If any champion hash differs from the lab record during promotion, stop and rebuild the patch from the recorded artifact.
