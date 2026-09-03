# Task

Write one English image-generation prompt for every supplied shot. Image 1 is the scene spatial anchor and is the authoritative source for topology, entrances, fixed architecture, scale, materials, paths, and stable set dressing.

Scene description:
{{scene_description}}

Unified visual-quality contract (interpret it and express the resulting image prompt in English):
{{visual_quality}}

Shots:
{{shots}}

# Output

Return JSON only. Top-level keys must be consecutive `background_1`, `background_2`, and so on. Every item contains exactly:

- `prompt_content`
- `shot_index`
- `description`

# Rules

- Create exactly one background item for every input shot. Each input index appears exactly once as the singular `shot_index`; never group or reuse one background across shots.
- `description` is one concise English sentence for human review.
- `prompt_content` is an English prompt for one cinematic, empty shot background.
- Honor the structured camera position, target, shooting angle, shot size, camera height, pitch, field of view, and focal length exactly.
- Preserve Image 1's spatial topology, architectural identity, fixed objects, materials, scale, time state, and lighting logic.
- Use `character_placements` only to reserve believable empty staging areas and sight lines. The background itself must contain no people, bodies, body parts, silhouettes, or character markers.
- Include the required foreground, midground, and background geometry for the requested composition and occlusion relationships.
- Do not produce an isometric map, diagram, contact sheet, split view, UI, labels, arrows, camera icons, annotations, readable text, subtitles, logos, or watermarks.
