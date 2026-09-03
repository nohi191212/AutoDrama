# AutoDrama roleboard project contract

## Contents

1. Current project snapshot
2. Production node interfaces
3. Reference-image semantics
4. Known conflicts and experimental consequences
5. Preflight checks
6. Production command surface

## 1. Current project snapshot

Re-inspect these facts at the start of every lab; do not assume they stayed unchanged.

- Active config: repository `saodi.yaml`.
- Project ID: `saodi_0803`.
- Current project output: `outputs/saodi_0803`.
- Required spatial guide: `.assets/image_templates/roleboard_template.png`.
- Current style anchor: unresolved until an exact image is user-confirmed or identified by an unambiguous promotion/selection record. The conventional `outputs/saodi_0803/assets/images/key_visions/key_vision_original.png` path has contained an obsolete image and must not be treated as authoritative by filename alone.
- Current image model route: `aibox:gpt-image-2`, roleboard size `3840x2160`.
- Current text model route: `aibox:gemini-3.6-flash`.
- Current roleboard prompt template selection: `aibox_gpt_image_2_guan`.

The present `role_finalize.json` contains four appearance assets:

| Role / appearance | Purpose in the lab | Current source density |
|---|---|---|
| 叶凡 / base | elderly primary, high identity evidence | comparatively rich |
| 柳菡烟 / base | young female primary | sparse |
| 李德海 / base | adult male functional role | sparse |
| 叶凡 / youth_80_years_ago | same-identity age variant | sparse, references base |

Use the three base appearances as the default exploration set and the youth variant as the default holdout. If the role set changes, preserve the same coverage: old/young, female/male, primary/functional, and at least one base-to-variant relationship.

Sparse identity input is an experimental factor. It must not be hidden. A fidelity-only compiler will tend to output generic faces because it is currently forbidden to complete unspecified stable design axes. Test constrained design completion explicitly rather than blaming the image model alone.

## 2. Production node interfaces

### `roleboard_prompt`

Relevant implementation:

- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/services/role_service.py`
- `autodrama/src/autodrama/prompts/roleboard_prompt/*.md`
- `autodrama/src/autodrama/repositories/roleboard_prompt_repo.py`
- `autodrama/src/autodrama/core/schemas.py`

The node runs once per role appearance and writes `RoleboardPromptItem` values. The current model output contract contains:

- `roleboard_prompt`
- `roleboard_negative_prompt`
- `voice_profile_prompt`
- `design_notes`

The service prepares extensive context, including novel extracts, full novel text, role index, visual tone, key-vision metadata, and image provider/model. A specific template may use only a subset. Do not infer that prepared context is actually visible to Gemini; inspect the selected template.

The current service passes `temperature=0.45` directly to `generate_json`, so the `saodi.yaml` node value does not necessarily control the call. Treat this as a production-contract issue. During experiments, record the actual temperature sent over the provider boundary.

The current `final_roleboard_prompt()` returns the core model output unchanged. Its style-prefix parameters are calculated but discarded. Do not score nonexistent style injection.

### `roleboard_image_generation`

Relevant implementation:

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/providers/aibox/image/gpt_image.py`
- `saodi.yaml`

Current behavior:

1. Select the first primary base appearance as anchor unless configured otherwise. For the current role order, this is 叶凡/base.
2. Generate the anchor from configured style-reference images, if any, followed by the key vision.
3. Generate every other base appearance from the anchor roleboard only.
4. Generate each variant from the anchor roleboard plus its same-role base identity reference.
5. Append hard-coded image-generation text after the role prompt.

The current `roleboard_style_reference_dir` is not set in `saodi.yaml`, so no dedicated template or style-reference directory is active. The required white-model template is therefore not part of the current request.

The helper `apply_roleboard_style_context()` exists but is not called by roleboard generation. Do not count dead code as behavior.

The anchor chain is a plausible cause of cast homogenization: later base roles receive the first roleboard as their only image reference, so the image model may import the anchor's face, body, costume, and design language. Treat independent-base generation from spatial template plus key vision as a required challenger.

### `roleboard_image_audit`

Relevant implementation:

- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
- `autodrama/src/autodrama/prompts/image_asset_audit/default.md`
- `autodrama/src/autodrama/core/schemas.py`

Current roleboard audit uses the generic `ImageAuditDecision`:

- a free `approved` boolean chosen by the judge;
- unstructured issues and rationale;
- one revised prompt;
- no required dimension coverage;
- no deterministic weighted-score recomputation;
- no cast-level comparison;
- no edit-versus-reroll action taxonomy.

On rejection it updates the in-memory roleboard prompt and forces single-asset roleboard regeneration. It does not perform targeted image edit. The base implementation defaults to two repairs unless node parameters say otherwise.

The project contains a stronger rubric-backed pattern in `key_vision_image_audit`: structured assessments, required dimension coverage, code-computed score and decision gates. When a user says `keyboard_image_audit` in this project, verify whether they mean `key_vision_image_audit`; there is currently no `keyboard_image_audit` node.

Do not copy the key-vision forced-accept behavior into roleboards. A failed identity asset must remain rejected.

## 3. Reference-image semantics

Assign each image exactly one declared responsibility in the prompt and request metadata.

### Spatial template

Source: `.assets/image_templates/roleboard_template.png`.

Allowed transfer:

- one horizontal 16:9 board;
- exactly three separated full-body views;
- front, true profile, back order;
- approximately equal height and shared foot baseline;
- similar spacing, margins, neutral stance, and unobstructed silhouette;
- weak neutral background and orthographic/long-lens appearance.

Forbidden transfer:

- the pictured male identity, face, body build, crown, ponytail, armor, shoulder pieces, sword, boots, garment layers, ornaments, or white-clay material;
- any assumption that all target roles are warriors or male;
- template-specific props or costume silhouette.

### Key vision

Allowed transfer:

- medium and render family;
- facial rendering treatment;
- skin, hair, fabric, wood, stone, and metal response;
- light softness/direction character, atmospheric restraint, palette relationship, and finishing level;
- shared-world cultural and production-design language.

Forbidden transfer:

- copying either depicted character;
- copying the gate, mountains, staff, action, pose, composition, or environment;
- using the key vision to override the role's hard identity facts.

### Same-role base identity

Use only for a variant. Transfer stable facial identity, bone structure, hair lineage when temporally valid, body lineage, and invariant design motifs. Apply only the declared age/time/wardrobe changes. Do not transfer old-age wrinkles, current clothing, held props, pose, or event state when producing a youth variant.

### Anchor roleboard

Treat use of a different character's roleboard as an experimental variable, never as a neutral style reference. Score cross-role face, body, and costume contamination. Prefer the key vision as the shared style reference when it produces equal style stability with better cast distinction.

## 4. Known conflicts and experimental consequences

### Media conflict

The current AIBOX Gemini template asks for `水墨二维国漫`, while `saodi.yaml` describes high-finish 3D guoman. The image-generation wrapper also appends `保持参考图的水墨二维国漫渲染`. Compare both against the explicitly confirmed key vision; do not claim the key vision's medium from a stale project path. Resolve any contradiction in a diagnostic candidate before judging aesthetics.

### Generic-design pressure

The current prompt template says unspecified features must be omitted and stable identity may only come from `identity_invariants` and `wardrobe`. That policy protects facts but makes sparse roles collapse into generic “清秀少女” and “硬朗成年男性” archetypes. Separate:

- immutable source facts;
- safe, stable design choices on unconstrained axes;
- forbidden story inventions.

The compiler may design on the second list but must log those choices.

### Spatial-template contamination

The template is not a neutral stick figure; it contains a highly specific male warrior. Simply adding it as image 1 may worsen clone-face and costume leakage. Require explicit reference-role language and score contamination in every round.

### Audit blind spot

A single-image audit cannot determine whether multiple roles have nearly the same face or silhouette. Add a cast-level pass or a cast contact-sheet reference inside `roleboard_image_audit`; do not pretend per-image scores solve cast diversity.

## 5. Preflight checks

Record pass/fail and evidence for each item:

1. `role_finalize.json` parses and contains at least one base appearance.
2. Every variant names an existing base appearance of the same role.
3. The exact key-vision image was explicitly user-confirmed or proven by a unique promotion/selection record; its path, selection basis, and hash are recorded. A conventional filename, generation JSON, audit JSON, newest modification time, or highest experiment score is not sufficient by itself.
4. The roleboard spatial template exists, opens successfully, and has a landscape ratio near the requested output.
5. Active prompt template, wrapper text, config style, and key vision do not contain unresolved medium conflicts.
6. Actual provider/model/size/quality/temperature and maximum reference count are recorded.
7. No old roleboard output will be silently reused during a forced experiment.
8. The experiment can target a single asset without losing unrelated generation output.
9. Image audit calls can be run explicitly even when historical config flags are false.
10. The paid-call budget and replacement policy are authorized before dispatch.

## 6. Production command surface

Use the repository's configured Python environment and `run/start.cmd`. Typical direct checks after promotion are:

```powershell
run\start.cmd --config saodi.yaml --project saodi_0803 --only roleboard_prompt --force
run\start.cmd --config saodi.yaml --project saodi_0803 --only roleboard_image_generation --assets <asset_id> --force
run\start.cmd --config saodi.yaml --project saodi_0803 --only roleboard_image_audit --assets <asset_id>
```

Confirm the current CLI allow-list before relying on `--assets`; repository behavior may have changed. Run the full cast only after the single-asset base and variant paths pass.
