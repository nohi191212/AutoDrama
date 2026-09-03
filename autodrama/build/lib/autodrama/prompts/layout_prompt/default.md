# Task

Write an English image-generation prompt for every supplied scene asset.

Scene assets:
{{layouts}}

Unified visual direction:
{{visual_tone}}

# Output contract

Return JSON that conforms to the supplied schema. Emit exactly one `layout_prompts` item for every input scene. Preserve `name`, `group`, `asset_role`, and `reference_asset_name` exactly.

- A base scene uses `prompt_type: "text_to_image"`.
- A variant scene uses `prompt_type: "image_edit"` and references its declared base scene.
- Every generated `prompt` must be written in English.

# Base scene spatial-anchor prompt

Describe one reusable 2:3 scene spatial-anchor sheet in the unified visual direction. It must show the same complete physical location in two vertically stacked, complementary high-angle isometric views taken from opposite corners. Both views must preserve identical topology, entrances, fixed architecture, fixed set dressing, material identity, scale, paths, and lighting logic.

Make spatial boundaries, doors, windows, stairs, circulation paths, performance zones, foreground/midground/background anchors, and stable light sources unambiguous. Preserve production-quality materials and the requested visual style; do not imitate a plain white clay model merely because the layout is technical. Include a small unobtrusive north arrow and simple floor-plan orientation inset, but no prose or readable signage.

The sheet is an empty scene reference: no people, hands, body parts, silhouettes, crowds, camera rigs, FOV cones, character markers, shot annotations, subtitles, watermarks, logos, or readable screen text.

# Variant scene edit prompt

Keep the referenced base sheet's two views, topology, entrances, fixed structures, materials, scale, paths, north orientation, and framing unchanged. Apply only the declared `state_delta` consistently to both views. Do not introduce new geometry, camera setups, people, or annotations.

Do not put model names, resolution controls, workflow terms, or filesystem paths into any generated prompt.
