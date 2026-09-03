# Task

Convert the current continuous story clip into consecutive production shots of 3–15 seconds each.

The clip has approximately {{available_seconds}} seconds available. The sum of all shot durations should closely match that budget.

Image 1 is the only scene spatial anchor for this clip. It defines the scene topology, fixed architecture, entrances, paths, scale, and stable set dressing. Every output shot must use exactly this scene and must describe camera placement and character blocking relative to visible anchors in Image 1.

# Bound scene

Exact scene ID: `{{scene_id}}`

Scene description:
{{scene_description}}

# Story context

Current clip:
{{clip_text}}

Necessary continuity from the previous clip:
{{previous_context}}

Necessary continuity into the next clip:
{{next_context}}

# Available foreground visual assets

`ref_ids` may use only IDs from this list. Do not put the scene ID in `ref_ids`; the scene is bound separately through `scene_id` and Image 1.

{{asset_index}}

The complete image-reference budget is {{reference_budget}} per shot, including the one bound scene/background image. Therefore `ref_ids` may contain at most {{foreground_reference_budget}} character and prop image IDs.

# Output contract

Return JSON only. Top-level keys must be consecutive `shot_1`, `shot_2`, and so on, starting at 1. Each value must contain exactly:

- `scene_id`
- `shot_description`
- `narrative_angle`
- `opening_state`
- `character_placements`
- `camera`
- `ref_ids`
- `video_prompt`
- `duration_seconds`
- `entity_states`
- `dialogue_lines`
- `overlay_text_spec`
- `allowed_props`

# Shot rules

- Cover the entire clip in story order. `duration_seconds` is an integer from 3 through 15.
- `scene_id` must exactly equal `{{scene_id}}` in every shot.
- Write visual planning prose and `video_prompt` in English. Preserve spoken dialogue text exactly in its source language.
- `shot_description` states the visible event in one concise sentence.
- `narrative_angle` states one observation direction and one dramatic focus. Split reverse angles, reaction shots, or viewpoint changes into separate shots.
- `opening_state` describes only the opening instant's temporary action state, occlusion, and movable-prop state. Do not hide character blocking or camera placement in this prose; use the structured fields below.
- `ref_ids` contains only indispensable visible character-appearance and prop image IDs. It must never contain a scene/layout ID.

## Explicit character blocking

`character_placements` contains one item for every visually referenced character and no others. Each item contains exactly:

- `role_id`: a valid role ID from the asset index metadata.
- `scene_position`: a precise position relative to fixed anchors visible in Image 1, such as "0.8 m east of the central table, beside the window-side chair".
- `screen_position`: intended frame position such as "left third" or "center-right".
- `depth_layer`: `foreground`, `midground`, or `background`.
- `body_facing`: body orientation relative to a scene anchor or another character.
- `gaze_target`: explicit gaze target or null.
- `pose`: the exact opening pose.

Every `character_placements.role_id` must also have a matching `entity_states` item and a referenced appearance image in `ref_ids`.

## Explicit camera contract

`camera` contains exactly:

- `scene_position`: precise camera location relative to fixed Image 1 anchors.
- `target`: the scene point or character position at which the optical axis is aimed.
- `shooting_angle`: explicit viewing angle, such as eye-level frontal, low three-quarter, high oblique, profile, or over-the-shoulder.
- `shot_size`: explicit framing size.
- `camera_height_m`: positive numeric height in metres.
- `pitch_degrees`: numeric pitch from -90 through 90; negative looks downward and positive looks upward.
- `field_of_view_degrees`: numeric horizontal field of view greater than 1 and less than 180.
- `focal_length_mm`: numeric full-frame-equivalent focal length from 8 through 600.
- `movement`: an explicit movement plan; use `locked-off` when stationary.

Camera position, target, angle, shot size, focal length, and field of view must be mutually consistent and drawable on Image 1.

## State, dialogue, props, and text

- `entity_states` describes only temporary shot state. Each item uses `schema_version=1`, a valid `entity_id`, and optional `appearance_id`, `pose`, `emotion`, `injury`, `held_props`, `energy_state`, and `event_refs`.
- `allowed_props` contains only valid prop IDs. Every `held_props` value must also appear in `allowed_props`.
- `dialogue_lines` preserves the source dialogue in order. Use consecutive `line_index` values from 1. Include the prescribed speaker, delivery, emotion, source evidence, and provenance fields. Use null rather than guessing an unknown speaker.
- Use `overlay_text_spec` only when exact readable text must appear in the frame; otherwise use null. Overlay timing must remain inside the shot duration.
- `video_prompt` describes visible action, performance, rhythm, and camera intent in English. Do not include aspect ratio, resolution, provider names, workflow terms, subtitles, or watermark instructions.
