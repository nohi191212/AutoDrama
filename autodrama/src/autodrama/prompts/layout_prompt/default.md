# Task

Write an English image-generation prompt for every supplied scene asset.

Scene assets:
{{layouts}}

Unified visual direction:
{{visual_tone}}

# Output contract

Return JSON that conforms to the supplied schema. Emit exactly one `layout_prompts` item for every input scene. Preserve `name`, `group`, `asset_role`, and `reference_asset_name` exactly.

The top-level value must be an object, never a bare array. Its shape is:

```json
{
  "layout_prompts": [
    {
      "name": "Example location",
      "group": "example_group",
      "asset_role": "base",
      "reference_asset_name": null,
      "prompt_type": "text_to_image",
      "prompt": "A concise example scene prompt"
    }
  ]
}
```

Use the example only to preserve the envelope and field names; replace every example value with the corresponding input data.

- A base scene uses `prompt_type: "text_to_image"`.
- A variant scene uses `prompt_type: "image_edit"` and references its declared base scene.
- Every generated `prompt` must be written in English.

# Base scene prompt

Describe one single, empty, reusable location in the unified visual direction as a production-ready scene reference image. Write for a model with strong spatial reasoning but a weak grasp of figurative layout: make the structure readable as an actual place, not a checklist of objects.

State the setting type and anchor structure up front (for example a mountain pass, a courtyard gatehouse, an open forest clearing). Then describe it as a spatial narrative rather than an inventory. Lead with a single, decisive circulation line — one main path / stair / road that a camera or a character would follow through the space — and how the largest set pieces sit along it. Say explicitly how the main entrance relates to the rest (for example the stair rises to a gatehouse and continues beyond it into a courtyard, rather than passing through the gateway's piers).

Keep a clear foreground / midground / background layering and state the relative depth between elements, so the eye reads where things are in space. Describe how the ground meets the sky or the surrounding walls. Give the dominant light source, the time-of-day feel, and the atmosphere as a concrete physical state (mist, haze, sun angle, moisture on stone) rather than vague adjectives.

Preserve production-quality materials and the requested visual style. Do not imitate a plain white clay model merely because the layout is technical, and do not let the reference image's objects (people, characters, specific props, incense burner, pine tree, composition, camera framing) leak into this scene.

Keep it one coherent view from a readable, natural camera height and angle. Do not split the scene into paired or stacked panels, and do not add a floor-plan inset, a north arrow, a compass, crop marks, a border, or any diagram element. No prose, no readable signage, no labels.

The image is an empty scene reference: no people, hands, body parts, silhouettes, crowds, camera rigs, FOV cones, character markers, shot annotations, subtitles, watermarks, logos, or readable screen text. Plaques and inscriptions may only be blurred, illegible marks.

# Variant scene edit prompt

Keep the referenced base image's scene structure, entrances, fixed structures, materials, scale, and framing unchanged. Apply only the declared `state_delta`, described briefly and concretely so an image-edit model can apply it. Do not introduce new geometry, camera setups, people, or annotations.

Do not put model names, resolution controls, workflow terms, or filesystem paths into any generated prompt.
