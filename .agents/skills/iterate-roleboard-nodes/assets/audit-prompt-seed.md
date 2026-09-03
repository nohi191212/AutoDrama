Audit the supplied roleboard using only visible image evidence. Prompt text and source contracts define what should exist; they are not proof that the pixels satisfy it.

<audit_scope>{{audit_scope}}</audit_scope>
<asset_name>{{asset_name}}</asset_name>
<hard_appearance_contract>{{hard_appearance_contract}}</hard_appearance_contract>
<variant_relationship>{{variant_relationship}}</variant_relationship>
<cast_contract>{{cast_contract}}</cast_contract>
<current_generation_prompt>{{current_generation_prompt}}</current_generation_prompt>

<reference_images>
Image 1 is the roleboard under audit.
{{spatial_template_reference_clause}}
{{key_vision_reference_clause}}
{{same_role_identity_reference_clause}}
{{cast_sheet_reference_clause}}
</reference_images>

<reference_boundaries>
The spatial template may prove only expected layout, scale, spacing, baseline, view order, and stance. Its face, body, crown, hair, armor, sword, garments, ornaments, and clay material must not appear unless independently required by the role contract.

The key vision may prove only project rendering medium, facial treatment, material response, palette relationship, light character, and finish. Its people, pose, props, scenery, and composition must not be copied.

A same-role base reference, when present, may prove stable identity lineage. It does not authorize copying age markers, current clothing, held props, pose, or event state into a variant.
</reference_boundaries>

<method>
1. First describe what Image 1 actually contains: number/order of views, crop, relative scale, baseline, spacing, body visibility, and any extra panels or objects.
2. Inspect whether front/profile/back show one face, skull, hair, body, garment construction, footwear, palette, and signature design.
3. Compare visible age, gender presentation, identity facts, wardrobe, and variant changes with the hard contract.
4. Inspect face specificity, body plausibility, wardrobe logic, ornament restraint, anatomy, and production usability.
5. Compare medium/material/light/finish with the key vision while checking template and key-vision identity contamination.
6. In cast scope, compare every pair by face geometry first, then body, hair mass, silhouette, wardrobe, and style cohesion. Hair or color alone cannot prove distinction.
7. Score every supplied rubric dimension exactly once. Required but cropped, hidden, blurred, too small, or unreadable evidence receives a low score, never N/A.
8. Choose the repair action from the policy after identifying the causal layer.
</method>

<action_policy>
- accept: every delivery gate passes and no unresolved major defect remains.
- edit: identity, exact three-view structure, source facts, and style are fundamentally correct; the defect is local and bounded.
- reroll: prompt is sound but the image has global stochastic layout, identity, anatomy, contamination, or style failure.
- regenerate_prompt: the prompt is generic, contradictory, incomplete, requests the wrong layout/medium, or includes transient state.
- block_upstream: source identity or variant facts are missing/contradictory and repair would invent canon.

Never route a missing/extra view, clone face, wrong identity, wrong age, severe anatomy, template-warrior leakage, major style collapse, or variant-lineage failure to cosmetic edit.
</action_policy>

<rubric>
{{rendered_rubric}}
</rubric>

<output_contract>
Return one JSON object with exactly:
{
  "assessments": [
    {
      "dimension_id": "...",
      "applicable": true,
      "score": 0,
      "severity": "none|minor|major|critical",
      "evidence": "visible pixel evidence",
      "defect": "specific defect or empty string",
      "regions": ["short region descriptions"]
    }
  ],
  "gate_results": {"gate_id": true},
  "recommended_action": "accept|edit|reroll|regenerate_prompt|block_upstream",
  "issues": ["specific visible or source-contract issues"],
  "edit_instruction": "targeted preservation-aware edit instruction or empty string",
  "revised_prompt": "complete replacement generation prompt only for regenerate_prompt, otherwise empty string",
  "missing_upstream_fields": ["required missing or contradictory fields only for block_upstream"],
  "rationale": "concise evidence-based decision"
}

Do not output an approved boolean. Production code recomputes weights, scores, gates, and approval from trusted rubric data.
</output_contract>
