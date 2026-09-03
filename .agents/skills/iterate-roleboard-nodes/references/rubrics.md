# Roleboard evaluation rubrics

## Contents

1. Shared scoring rules
2. Image-generation and downstream-image rubric
3. Gemini prompt-output rubric
4. Audit-system rubric
5. Production acceptance and repair routing
6. Evidence-writing rules

## 1. Shared scoring rules

Score every applicable dimension from 0 to 10 using visible or textual evidence:

| Anchor | Meaning |
|---:|---|
| 0 | absent, opposite, or unusable |
| 2 | severe failure dominates the result |
| 4 | major defects; only fragments work |
| 6 | broadly usable but visibly flawed or unstable |
| 8 | strong production result with minor defects |
| 10 | exceptional, specific, clean, and fully supported |

Use odd values only between adjacent anchors. Mark a dimension not applicable only when the contract genuinely does not require it. Cropped, hidden, blurred, too small, or unreadable required evidence is a low score, not N/A.

Calculate each scope score as a normalized weighted mean. Apply hard gates after the raw score. Fatal-gate failure makes a candidate ineligible; it does not disappear inside an average.

Use the bundled JSON files as the machine-readable authority. This document explains their boundaries.

## 2. Image-generation and downstream-image rubric

Composite score:

- asset-scope mean: 82%;
- cast-scope mean: 18%.

### Asset scope

| Dimension | Weight | What to inspect |
|---|---:|---|
| `exact_three_view_layout` | 12 | Exactly one front, one true profile, and one back view in that order; no extra angle, close-up, panel, or duplicate. |
| `full_body_scale_spacing` | 8 | All heads and feet visible; equal apparent scale, shared baseline, clean separation, useful margins, no overlap. |
| `source_identity_fidelity` | 10 | Age, gender presentation, hard identity invariants, wardrobe facts, role status, and declared variant changes match source facts. |
| `cross_view_identity_consistency` | 10 | Face, skull, hair, body, garment construction, footwear, palette, and signature details remain the same person across all views. |
| `reference_role_separation` | 8 | Spatial template influences only layout; key vision influences only style; no copied template warrior, sword, crown, armor, key-vision person, pose, or scenery. |
| `facial_specificity` | 10 | Face has readable, role-specific geometry: skull/face length, jaw, cheekbones, brow, eyes, nose, mouth, age structure, and asymmetry without generic beauty defaults. |
| `body_proportion_role_fit` | 8 | Head-to-body ratio, shoulders, torso, hips, limb length, posture, age, sex, occupation, and health read as plausible and intentional. |
| `wardrobe_design_logic` | 8 | Silhouette, layers, closures, footwear, material, wear, palette, and ornament follow role/era/status and work from all three views. |
| `design_restraint` | 6 | One dominant silhouette idea and one or two supporting details; no random fantasy filigree, over-armoring, excessive jewelry, glowing trim, or mobile-game skin clutter. |
| `key_vision_style_fidelity` | 8 | Medium, facial rendering, material response, palette relationships, light character, and finish belong to the key vision without copying its scene. |
| `anatomy_cleanliness` | 6 | Face, ears, neck, shoulders, hands, fingers, legs, feet, and profile/back structures are plausible and not fused or duplicated. |
| `artifact_cleanliness` | 3 | No labels, text, watermark, logo, frame lines, collage marks, stray props, or unexplained objects. |
| `production_usability` | 3 | Board is immediately useful as a reusable identity reference for later frontal, keyframe, and video nodes. |

### Cast scope

| Dimension | Weight | What to inspect |
|---|---:|---|
| `pairwise_face_distinctiveness` | 30 | Every role pair can be distinguished by face geometry without relying on hair, clothes, age label, or color alone. |
| `silhouette_distinctiveness` | 20 | Body build, posture, hair mass, garment outline, and proportion separate roles at thumbnail scale. |
| `wardrobe_role_distinctiveness` | 20 | Costumes encode different age/status/function without breaking the same culture or making everyone a hero. |
| `shared_world_cohesion` | 20 | Rendering, material system, palette family, lighting treatment, and design culture remain one project. |
| `ai_archetype_avoidance` | 10 | Avoid repeated oval faces, identical almond eyes, sharp V-jaws, porcelain skin, generic flowing hair, random filigree, and symmetric “AI concept art” polish. |

### Fatal image gates

1. `exact_three_views`: exactly three required views and no others.
2. `single_same_identity`: all three views show one consistent identity.
3. `full_body_visibility`: all required anatomy and clothing are visible at useful scale.
4. `source_identity`: no contradiction of age, gender presentation, identity facts, wardrobe, or variant relationship.
5. `reference_contamination`: no copied template/key-vision identity, costume, prop, or scene.
6. `severe_anatomy`: no production-breaking face, hand, limb, or body defect.
7. `clean_output`: no text, watermark, collage, extra subject, or stray prop.
8. `cast_clone`: the cast does not reuse a near-identical face/body/costume base.
9. `variant_lineage`: a variant is recognizably the same role with only authorized changes.
10. `shared_style`: no role falls into a different medium or rendering family.

## 3. Gemini prompt-output rubric

Score the JSON output without seeing the candidate template name. This is a text-quality screen; final selection still depends mostly on downstream images.

| Dimension | Weight | Boundary |
|---|---:|---|
| `json_schema_validity` | 5 | Exact four-key JSON, parseable, no prose or Markdown outside it. |
| `source_fact_fidelity` | 16 | Preserves every relevant hard fact and does not convert a design choice into a false story fact. |
| `stable_state_separation` | 10 | Excludes pose, emotion, injury, held prop, action, energy, and event-only state. |
| `exact_view_contract` | 10 | Writes one 16:9 board with front/profile/back, equal scale, full body, baseline, spacing, and identity consistency. |
| `reference_semantics` | 8 | Clearly separates spatial template, key-vision style, and optional same-role identity responsibilities. |
| `facial_observability` | 12 | Uses concrete face geometry and age evidence that can be checked in pixels; avoids generic beauty shorthand. |
| `body_silhouette_observability` | 8 | Gives plausible, visible body proportions, posture, and silhouette decisions. |
| `wardrobe_logic` | 8 | Specifies functional structure, layers, material, palette, wear, footwear, and rear/profile readability. |
| `restrained_signature_design` | 8 | Adds only safe stable choices on unconstrained axes; limits dominant motif and ornaments. |
| `cast_contrast` | 8 | Separates the target from named cast archetypes without copying or mentioning them as subjects in the image. |
| `positive_executability` | 4 | States desired pixels positively and in priority order; exclusions are short and targeted. |
| `concision_consistency` | 2 | No repeated, contradictory, or low-value quality adjectives; length fits the task. |
| `negative_prompt_precision` | 1 | Negative prompt addresses likely failures without restating the entire positive prompt. |

### Fatal prompt gates

- `exact_json`: output parses and matches the schema.
- `no_source_contradiction`: no hard fact is changed.
- `no_transient_state`: no shot-only state enters stable identity.
- `exact_views`: no extra view, panel, close-up, label, or conflicting layout.
- `no_medium_conflict`: no 2D/3D/live-action conflict with the frozen project style.
- `no_extra_entity`: no other named role, creature, or unrequested prop appears as an image subject.

Calculate the final compiler score as:

`0.30 * prompt_checkpoint_mean + 0.70 * downstream_image_checkpoint_mean`

Apply downstream image gates before combining. An invalid or hard-gate-failing prompt candidate is ineligible regardless of its text score.

## 4. Audit-system rubric

Score the audit candidate against hidden gold labels, not against its own confidence.

| Dimension | Weight | Boundary |
|---|---:|---|
| `visible_evidence_grounding` | 15 | Findings cite actual pixels/regions and never treat prompt claims as proof. |
| `rubric_coverage` | 10 | Every required dimension appears once with applicable/score/evidence/severity. |
| `hard_gate_detection` | 15 | Every gold hard-gate failure is detected and no gate is skipped through N/A. |
| `approval_correctness` | 15 | Accepted/rejected outcome matches deterministic policy and gold label. |
| `severity_calibration` | 8 | Cosmetic, local, major, and critical defects receive consistent severity. |
| `repair_route_correctness` | 12 | Chooses accept/edit/reroll/regenerate_prompt/block_upstream correctly. |
| `repair_instruction_quality` | 10 | Repair text is complete, local when editing, preserves unaffected identity/layout/style, and is directly executable. |
| `cast_clone_detection` | 8 | Detects repeated face/body/costume bases in cast context. |
| `output_schema_actionability` | 4 | Structured output parses and contains enough data for deterministic code. |
| `leakage_resistance` | 3 | Does not infer success from the current prompt, candidate name, prior score, or expected label. |

### Fatal audit gates

- `no_critical_false_accept`: zero accepted images with a critical gold defect.
- `complete_dimension_coverage`: all required dimensions are returned exactly once.
- `correct_hard_gate_outcome`: all gold hard gates produce rejection.
- `correct_repair_class`: no global identity/layout failure is routed to a cosmetic edit and no upstream contradiction is papered over by a reroll.
- `cast_clone_recall`: every gold cast-clone case is detected.

Track classification metrics separately from the 0–10 quality score. A high prose-quality score cannot compensate for a critical false acceptance.

## 5. Production acceptance and repair routing

Recommended initial production policy for roleboards:

- weighted asset score at least 8.0;
- weighted cast score at least 7.8;
- every gate passes;
- every gate dimension scores at least 7.0;
- no `critical` defect;
- no unresolved `major` identity, layout, anatomy, contamination, or style defect.

Use these action boundaries:

### `edit`

Use only when identity, three-view structure, source facts, and shared style are already correct and the defect is local: a small hand/foot defect, stray mark, local seam, isolated extra ornament, minor background artifact, or one bounded material inconsistency. The edit prompt must explicitly preserve all unaffected views and identity.

### `reroll`

Use when the prompt is sound but the image has global stochastic failure: missing/extra view, cross-view identity drift, severe anatomy, wrong body, template leakage, major style collapse, or generalized costume corruption.

### `regenerate_prompt`

Use when the prompt itself is generic, contradictory, lacks face/body design, misstates references, requests the wrong medium/layout, or contains shot-only state.

### `block_upstream`

Use when identity facts or variant relationships are missing or contradictory and any repair would invent canonical identity. Record the exact missing fields and return to `role_finalize` or its source stage.

Never force-accept after the maximum attempt count. Persist the rejection and stop downstream publication.

## 6. Evidence-writing rules

Good evidence identifies subject, region, relation, and consequence:

- “The profile view has a shorter jaw and a higher nasal bridge than the front view, so the three panels do not depict one face.”
- “柳菡烟 and young 叶凡 share the same oval skull, eyebrow arc, eye spacing, nose, mouth, and V-shaped jaw; only hair and clothing differ.”
- “The side view carries the template sword and layered shoulder armor although the role contract contains neither.”
- “All three figures share a common foot baseline and differ by less than roughly 3% in height; head and footwear remain fully visible.”

Bad evidence is abstract or prompt-derived:

- “looks good”;
- “more premium”;
- “AI feeling” without naming visible fingerprints;
- “the prompt says it is the same character”;
- “probably a 3D render” without material/light evidence.

For each dimension below 8, name the repairable defect. For each score above 9, name the visible proof that justifies exceptional performance.
