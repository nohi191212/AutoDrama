# Image audit rubrics

The same dimension catalogs are shared by production delivery audit and the offline template-evolution tournament:

- `general_rubrics-v2`: style-agnostic physical structure, story contract, spatial logic and cinematic frame quality. Its three categories total 85%: physical 40%, contract 30%, cinematic 15%.
- `<visual-style>-rubrics`: only the visible aesthetic and pixel finish of the named style. `xuanhuan-v2-rubrics` contributes the remaining 15%.

Score every applicable dimension from visible image evidence. Start from the nearest `0 / 2 / 4 / 6 / 8 / 10` anchor and use an odd score only between adjacent anchors. `N/A` is allowed only when a dimension genuinely does not apply; cropped, hidden, blurred or unreadable required evidence must be scored from what is actually visible rather than inferred.

Production `key_vision_image_audit` uses `production_protocol-v1.json`. It computes a pure weighted score from the current production judge's per-dimension evidence. The score is never capped, clamped or replaced by a gate or severity. The default delivery decision requires a weighted score of at least 7.0, no critical defect and no applicable gate below 6.0; these approval rules do not alter the reported score.

The offline prompt-evolution tournament continues to use the stricter dual-judge and nonlinear-cap rules in `evaluation_protocol-v3.json`. Keeping the protocols separate prevents a production delivery preference from silently changing historical experiment rankings.

When a converged template is audited for previously uncovered defects, add a new dimension only if the defect is directly observable, recurs in at least two images, cannot be fully explained by an existing dimension, and can plausibly be influenced by prompting. Every new dimension must define all six anchors and be back-scored across historical images.
