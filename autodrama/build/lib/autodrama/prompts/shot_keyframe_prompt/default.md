# Task

Write an English image-generation prompt for one opening keyframe. Image 1 is the generated shot background and locked camera anchor. Later images bind character and prop identity only.

Shot event:
{{shot_description}}

Narrative angle:
{{narrative_angle}}

Opening state:
{{opening_state}}

Explicit character placements:
{{character_placements}}

Explicit camera specification:
{{camera}}

Reference guide:
{{reference_guide}}

Visual-quality contract:
{{visual_quality}}

Exact text-rendering instruction:
{{text_rendering_instruction}}

Return JSON only with the single field `prompt_content`. Write `prompt_content` in English and describe exactly one opening keyframe. Preserve Image 1's camera, field of view, perspective, spatial structure, fixed objects, and lighting. Place every referenced character at the specified scene anchor, frame position, depth layer, facing, gaze, and opening pose. Preserve face, hair, body type, costume, and prop identity from their dedicated reference images. Do not redesign the background, narrate the full action, or introduce a different camera setup.
