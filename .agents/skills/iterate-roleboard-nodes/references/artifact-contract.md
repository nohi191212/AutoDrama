# Roleboard lab artifact contract

## Contents

1. Directory layout
2. Immutable inputs and hashes
3. Candidate and batch records
4. Append-only call ledger
5. Evaluation records
6. Blind-review artifacts
7. Reports and promotion bundle
8. Resume rules

## 1. Directory layout

Create each experiment under repository `.tmp/` with a unique timestamped name:

```text
.tmp/roleboard-node-evolution-<project-id>-<YYYYMMDD-HHMMSS>/
├── experiment.json
├── PROTOCOL.md
├── ledger.jsonl
├── results.jsonl
├── inputs/
│   ├── manifest.json
│   ├── config.yaml
│   ├── role_finalize.json
│   ├── roleboard_prompt.baseline.json
│   ├── key_vision.png
│   ├── key_vision_prompt.json
│   ├── key_vision_image_audit.json
│   ├── roleboard_template.png
│   └── production-files.json
├── contracts/
│   ├── appearances.json
│   └── cast-contrast-matrix.json
├── candidates/
│   ├── image_generation/
│   ├── roleboard_prompt/
│   └── image_audit/
├── batches/
├── responses/
│   ├── gemini/
│   ├── image/
│   └── audit/
├── images/
│   ├── exploration/
│   ├── holdout/
│   ├── checkpoint/
│   └── repair/
├── evaluations/
│   ├── image.jsonl
│   ├── prompt.jsonl
│   ├── audit.jsonl
│   └── rankings/
├── contact_sheets/
│   ├── manifests/
│   ├── blind/
│   └── answer_keys/
├── reports/
│   └── report-standalone.html
├── promotion/
│   ├── champion.json
│   ├── patch-plan.json
│   └── verification.json
└── FINAL_SUMMARY.md
```

Do not store task-generated files in system temp directories. Keep them inside the repository `.tmp/` lab.

## 2. Immutable inputs and hashes

`inputs/manifest.json` must record for every input:

- logical role, such as `spatial_template`, `key_vision`, or `role_finalize`;
- source path and snapshot path;
- byte size and SHA-256;
- image width/height/mode/format when applicable;
- creation timestamp;
- whether the file is required or optional.

For `key_vision`, `experiment.json` must additionally record the exact source path, SHA-256, and `selection_basis` (`user_confirmed` or `promotion_record`). When the basis is `promotion_record`, snapshot that record under `inputs/`. Do not infer authority from a conventional filename, file modification time, provider generation metadata, audit approval, or experiment ranking alone.

`inputs/production-files.json` must hash every production file capable of changing the result, including selected prompts, service/node code, schemas, provider code, rubric code, and active config.

When a frozen contract bundle is supplied, snapshot its `appearances.json` and `cast-contrast-matrix.json` under `contracts/` and record both source paths and hashes in `experiment.json`. Do not edit a contract file after any candidate result exists; create a new bundle and lab instead.

Never mutate files under `inputs/`. If source inputs change, create a new lab or a new explicitly versioned snapshot and invalidate comparisons that cross the change.

## 3. Candidate and batch records

Each candidate needs a manifest:

```json
{
  "candidate_id": "p1-r01-balanced-v1",
  "phase": "image_generation",
  "parent_id": "p1-r00-spatial-style-explicit",
  "status": "challenger",
  "changed_family": "balanced-shortest-clauses",
  "hypothesis": "...",
  "frozen_controls": {
    "model": "gpt-image-2",
    "size": "3840x2160",
    "quality": "high",
    "reference_policy": "spatial,key_vision"
  },
  "files": [
    {"path": "wrapper.md", "sha256": "..."}
  ],
  "created_at": "..."
}
```

Write the hypothesis and changed family before calls. Do not edit a candidate after any result exists. Create a new candidate ID.

Each batch item needs:

- globally unique `sample_id`;
- candidate, phase, round, split, role, appearance, and replicate IDs;
- prompt file/hash;
- ordered reference list with role and hash;
- model, provider, size, quality, temperature when applicable;
- expected output path;
- whether it is exploration, holdout, checkpoint, or repair.

## 4. Append-only call ledger

Use `ledger.jsonl` as the authoritative budget record. Append one line before dispatch and one completion line after the result.

Reservation event:

```json
{
  "event": "reserve",
  "call_id": "img-p1-r01-c03-li-dehai-r1",
  "kind": "image",
  "phase": "image_generation",
  "candidate_id": "p1-r01-c03",
  "sample_id": "li-dehai-r1",
  "count": 1,
  "at": "..."
}
```

Completion event:

```json
{
  "event": "complete",
  "call_id": "img-p1-r01-c03-li-dehai-r1",
  "status": "success",
  "request_id": "...",
  "elapsed_seconds": 83.2,
  "output_path": "images/exploration/...png",
  "output_sha256": "...",
  "at": "..."
}
```

Allowed completion statuses:

- `success`;
- `transport_failure`;
- `provider_rejection`;
- `schema_failure`;
- `cancelled_before_dispatch`.

A reservation counts against budget unless a `cancelled_before_dispatch` event proves that no request was sent. Do not decrement counts by rewriting history.

Use `results.jsonl` for full request/result metadata. Never let parallel workers overwrite a shared JSON object. A derived `ledger-summary.json` may be regenerated, but it is not authoritative.

## 5. Evaluation records

Store one JSON object per line. The bundled scorer expects:

```json
{
  "candidate_id": "p1-r01-c03",
  "sample_id": "blind-017",
  "scope": "asset",
  "role_id": "role_李德海",
  "appearance_id": "role_李德海_appearance_base",
  "status": "scored",
  "scores": {
    "exact_three_view_layout": 9,
    "full_body_scale_spacing": 8
  },
  "gates": {
    "exact_three_views": true,
    "single_same_identity": true
  },
  "evidence": {
    "exact_three_view_layout": "Exactly three separated views...",
    "full_body_scale_spacing": "..."
  },
  "judge_id": "codex-blind-pass-1",
  "rubric_sha256": "...",
  "image_sha256": "..."
}
```

For a cast-scope record, omit role/appearance or set them to `cast`, and evaluate one fixed set of role images. Record the component image hashes.

For a missing output, write status `transport_failure` or the appropriate failure class and omit scores. The scorer must not convert it to zero.

If two judges score the same sample, keep two records with different `judge_id` values. Aggregate judge scores only after checking disagreement. Require adjudication when:

- a fatal gate differs;
- approved versus rejected differs;
- the overall raw score differs by more than 1.0;
- an action label differs between local edit and any global/upstream route.

## 6. Blind-review artifacts

The blind contact-sheet manifest may contain real candidate IDs, but the rendered image must show only random blind IDs. Store the answer key separately.

For every contact sheet, record:

- randomization seed;
- source image paths and hashes;
- blind ID mapping;
- thumbnail size, crop policy, padding, background, columns, and title;
- script version/hash.

Never crop faces, hands, feet, or panel edges to make a prettier sheet. Use fit-within and letterbox padding. Generate separate detail sheets when needed.

## 7. Reports and promotion bundle

`report-standalone.html` must work offline and contain or embed:

- experimental question and frozen controls;
- call-budget accounting and failure classes;
- baseline versus candidate versus checkpoint images;
- hard-gate table;
- dimension and scope summaries;
- per-role and cast-level results;
- variance and minimum score, not only mean;
- blind-review evidence;
- exact candidate prompt/template text;
- limitations and stop reason;
- champion hash and promotion recommendation.

`promotion/champion.json` must include:

- phase champions and their hashes;
- input hashes;
- rubric and judge revisions;
- reference policy and order;
- model parameters;
- checkpoint metrics;
- experiment path;
- explicit `production_eligible` boolean and reasons.

Do not set `production_eligible=true` for a diagnostic-tier lab.

## 8. Resume rules

To resume:

1. read `experiment.json` and input hashes;
2. reconstruct reservations and completions from `ledger.jsonl`;
3. compare input and production-file hashes with the current workspace;
4. mark any hash drift and decide whether it invalidates the pending phase;
5. enumerate missing expected samples from batch manifests;
6. resume only pending, authorized calls;
7. never repeat a successful call merely because a mutable summary is stale;
8. rebuild rankings and reports from raw results/evaluations.

If input bytes or a frozen control changed, do not silently continue the same round. Close it as invalidated and create a new versioned round.
