---
name: iterate-roleboard-nodes
description: Run controlled, evidence-driven iteration for AutoDrama's roleboard_image_generation, roleboard_prompt, and roleboard_image_audit nodes. Use when optimizing roleboard prompts or reference-image strategy, improving facial and body distinctiveness without cheap AI aesthetics, testing Gemini prompt-compilation conditions, designing roleboard audit rubrics and edit-versus-reroll repair loops, resuming a roleboard experiment, or promoting a validated roleboard pipeline into production.
---

# Iterate AutoDrama Roleboard Nodes

Build a reproducible three-stage experiment instead of tuning all roleboard variables at once. Optimize the direct image-generation contract first, freeze it, optimize Gemini prompt compilation second, then build and validate the production audit and repair loop.

## Mandatory operating rules

1. Treat `roleboard_image_generation -> roleboard_prompt -> roleboard_image_audit` as three different optimization surfaces even though production executes prompt generation first. Follow the experimental order in this skill to preserve causal attribution.
2. Keep production files and current accepted assets unchanged during exploration. Write experiments under repository `.tmp/` and promote only a validated champion.
3. Freeze model, provider, quality, output size, source facts, source images, sampling count, and judge protocol inside a comparison round. Change one named control family per challenger.
4. Reserve every text, image, edit, and audit call before dispatch. Count failed transport calls. Treat append-only `results.jsonl` or `ledger.jsonl` as authoritative under concurrency.
5. Score visible pixels, not prompt claims. Never let attractive rendering compensate for a failed identity, three-view, anatomy, reference-contamination, or cast-clone gate.
6. Use blind contact sheets and anonymized candidate IDs for comparative review. Inspect thumbnails first, full images second, and face/hands/feet/material boundaries third.
7. Require at least two independent downstream image samples before calling a prompt or template stable. Use a holdout appearance before promotion.
8. Do not force-accept a rejected roleboard. Route the failure to targeted edit, reroll, prompt regeneration, or upstream blocking.
9. Preserve only one production path after promotion. Remove obsolete roleboard prompt variants, dead reference plumbing, fallbacks, and stale tests in the same change unless the user explicitly asks for compatibility.
10. Do not run `pytest` in this repository. Use compile checks, focused smoke scripts under `scripts/smoke/`, and direct node runs.

## Load the needed references

- Read [references/project-contract.md](references/project-contract.md) before touching AutoDrama code, configuration, prompts, or the current `saodi_0803` outputs. It records the actual node interfaces, current reference order, and known conflicts.
- Read [references/experiment-protocol.md](references/experiment-protocol.md) before creating, resuming, or changing an experiment. It defines the phase order, sample allocation, candidate families, holdout rules, budgets, and stop conditions.
- Read [references/rubrics.md](references/rubrics.md) before scoring images, Gemini outputs, or audit decisions. Use the bundled JSON rubrics instead of inventing dimensions mid-round.
- Read [references/artifact-contract.md](references/artifact-contract.md) before writing lab artifacts, ledgers, batch files, evaluations, or reports.
- Read [references/promotion-and-repair.md](references/promotion-and-repair.md) before changing production or implementing edit/reroll behavior.

## Determine the requested mode

Choose exactly one mode and state it in the lab manifest:

- `design`: inspect the current system and produce an experiment plan without paid calls.
- `execute`: create a lab, run authorized calls, score, and iterate until a stop condition.
- `resume`: reconstruct counts and state from append-only records, never from memory or a mutable summary.
- `promote`: implement a previously validated champion, run non-pytest checks, and record provenance.

If the user asks for “迭代”, “实验”, or “找最佳方案” without a call budget, finish preflight and a budgeted design first. Do not silently spend paid calls.

## Step 0: Establish a trustworthy baseline

1. Locate the repository root, active config, project ID, project output directory, `role_finalize.json`, current key-vision audit, and `roleboard_template.png`. Resolve the current locked key-vision image only from an explicit user-confirmed path or an unambiguous promotion/selection record. Never infer it from a conventional filename such as `key_vision_original.png`, from modification time, or from an experiment score alone.
2. Hash all inputs and production files that affect the three nodes. Record provider/model/quality/size/temperature/reference order and relevant configuration.
3. Enumerate every role appearance from `role_finalize`. Build:
   - a hard-fact contract for age, gender presentation, identity invariants, wardrobe, and variant relationship;
   - a designable-axis list for unspecified face, body, hair, and costume choices;
   - a forbidden-state list for action, emotion, injury, held props, energy, and event-only state;
   - a cast contrast matrix showing how every pair will remain visually distinguishable.
4. Fail preflight if required assets are missing, a role has no base appearance, a variant lacks a base reference, or style/spatial reference roles are ambiguous.
5. Diagnose contract conflicts before generating. A baseline containing mutually exclusive media such as “水墨二维” and “3D 国漫” is diagnostic evidence, not an eligible incumbent.
6. Initialize the lab with `scripts/init_lab.py`, passing both `--key-vision` and `--key-vision-selection-basis`; when a reviewed contract bundle exists, also pass `--contract-bundle` so the frozen character contracts are snapshotted with the lab. Keep generated artifacts under `.tmp/`. If the chosen key vision is later corrected, invalidate the entire lab and create a new lab; never replace its frozen snapshot in place.

## Step 1: Optimize `roleboard_image_generation`

Optimize the prompt wrapper and reference policy with hand-authored, frozen character contracts. Do not involve Gemini yet.

1. Use the same role facts and core character design text for every candidate.
2. Always distinguish reference responsibilities:
   - spatial template: layout, view order, scale, baseline, spacing, and neutral stance only;
   - key vision: medium, rendering, material response, palette, light, and finish only;
   - base roleboard, when testing a variant: same-character identity only.
3. Explicitly forbid copying the template's male face, crown, armor, sword, hair, garment, or body type, and forbid copying people, scenery, pose, or composition from the key vision.
4. Test the current anchor chain against independent-base reference policies. Treat “all roles resemble the anchor” as a cast-level failure, not a desirable consistency effect.
5. Use the phase-1 candidate families and round structure in the experiment protocol. Keep the incumbent as control and change one family at a time.
6. Score every asset with `assets/roleboard-image-rubric-v1.json`, then score the full cast as a set. Use `scripts/score_candidates.py` for deterministic aggregation.
7. Promote a checkpoint only when all hard gates pass, the holdout passes, the cast-level distinctiveness score does not regress, and repeated samples meet the stability threshold.
8. Freeze the winning wrapper, reference order, reference roles, size, quality, and model before Step 2.

The bundled [assets/image-generation-wrapper-seed.md](assets/image-generation-wrapper-seed.md) is a starting hypothesis, not a presumed winner.

## Step 2: Optimize `roleboard_prompt`

Freeze the Step-1 image-generation champion and vary only Gemini's inputs, compiler instruction, or sampling parameter.

1. Run a text-first funnel. Generate multiple JSON outputs cheaply, validate the schema, and score text before paying for downstream images.
2. Compare minimum-sufficient context against cast-aware and evidence-aware context. Do not feed full novel text merely because it is available; include only facts that affect stable visible design.
3. Allow constrained design completion on genuinely unspecified visual axes, but never invent plot facts, rank, props, injuries, magic state, relationships, or event-only costume damage.
4. Require observable face and body decisions. Avoid generic phrases such as “五官清秀”, “面容硬朗”, “气质出尘”, or “优雅长袍” unless followed by concrete geometry, proportion, material, or silhouette evidence.
5. Require cast-aware differentiation without breaking shared-world cohesion. Limit each appearance to one dominant silhouette idea and one or two restrained signature details.
6. Keep the spatial contract and reference-role language stable. Gemini must not restyle the project or reinterpret the white template as character identity.
7. After selecting a compiler, sweep temperature separately. Do not mix a temperature change with a template or context change.
8. Rank candidates by 30% prompt score and 70% gated downstream-image score. Use lower variance, lower prompt complexity, and fewer unsupported design decisions as tie-breakers.
9. Freeze the compiler template, input context, temperature, output schema, and length range before Step 3.

Use [assets/gemini-compiler-seed.md](assets/gemini-compiler-seed.md) as a controlled seed. Preserve exact JSON-only output during experiments.

## Step 3: Optimize `roleboard_image_audit`

Build the audit against a labeled gold set, not only fresh champion images.

1. Assemble accepted images and controlled failures covering exact-three-view errors, crop/scale errors, cross-view identity drift, generic or cloned faces, implausible bodies, template leakage, key-vision style mismatch, ornament overload, anatomy defects, text/watermark, and variant identity loss.
2. Label each sample with the expected action: `accept`, `edit`, `reroll`, `regenerate_prompt`, or `block_upstream`. Keep labels hidden from the judge.
3. Compare the current generic audit with rubric-only, deterministic-rubric, and asset-plus-cast variants.
4. Require complete dimension coverage and visible evidence. Let code recompute scores and approval; never trust the model's `approved` boolean as authority.
5. Run both:
   - an asset pass for layout, identity, anatomy, design, style, and cleanliness;
   - a cast pass for pairwise facial distinction, silhouette distinction, wardrobe logic, shared style, and anchor/template contamination.
6. Reject any audit candidate with a critical false acceptance. Require action-routing accuracy and repair convergence defined in the experiment protocol.
7. A/B targeted image edit against full reroll only on edit-eligible defects. Preserve the original image and attempt lineage.
8. Freeze the rubric revision, thresholds, judge prompt, deterministic normalizer, action policy, maximum attempts, and history schema together.

Use [assets/audit-prompt-seed.md](assets/audit-prompt-seed.md) and `assets/roleboard-audit-rubric-v1.json` as initial controls.

## Step 4: Produce the decision package

For every completed phase, produce:

- baseline, candidates, winner, and independent checkpoint samples;
- per-sample dimension scores with visible evidence and failed gates;
- candidate mean, population variance, minimum score, gate pass rate, and role coverage;
- cast-level contact sheets and pairwise distinction findings;
- transport failures separated from visual failures;
- exact hashes for winning prompts, rubrics, and source inputs;
- one next-round hypothesis or a documented stop reason.

Generate a standalone HTML report and a concise `FINAL_SUMMARY.md`. Do not present an unvalidated temporary winner as production-ready.

## Step 5: Promote safely

1. Reconstruct the champion from recorded artifacts and verify its hashes.
2. Apply only champion-backed changes. Do not smuggle untested wording or reference changes into the promotion patch.
3. Update all in-repository callers and fixtures to the new contract. Delete the obsolete path.
4. Run compilation, focused smoke scripts, and a direct single-asset path before a full cast run.
5. Run the production audit on every roleboard and the cast pass. Do not publish rejected assets downstream.
6. Record the experiment directory, champion IDs, hashes, rubric revision, validation commands, and results in the production output metadata or promotion record.

## Scripts and machine-readable assets

- `scripts/init_lab.py`: create an immutable input snapshot, lab structure, manifest, and append-only ledger.
- `scripts/make_contact_sheet.py`: create randomized blind contact sheets and a separate answer key.
- `scripts/score_candidates.py`: validate evaluation coverage, apply hard gates, aggregate scoped scores, and rank candidates.
- `assets/roleboard-image-rubric-v1.json`: generation and downstream-image rubric.
- `assets/roleboard-prompt-rubric-v1.json`: Gemini output rubric.
- `assets/roleboard-audit-rubric-v1.json`: audit-decision rubric.
- `assets/lab-config.example.json`: reproducible default experiment configuration.

Treat scripts as deterministic helpers, not substitutes for visual inspection. Record every manual judgment with visible evidence.
