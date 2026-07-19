# Task

为短剧项目《{{title}}》生成一张视频 {{frame_role_label}} 关键帧。

这是一张用于视频首尾帧约束的单张电影画面，不是角色板，不是场景三视图，不是拼贴图，不是 storyboard 十二宫格。

# Clip Context

- Episode: {{episode_key}}
- Clip: {{clip_id}}
- Clip title: {{clip_title}}
- Frame role: {{frame_role}}
- Source panel: {{panel_ref}}
- Final video aspect ratio: {{final_aspect_ratio}}
- Provider/model: {{provider_name}} / {{model_name}}

Clip text:
{{clip_text}}

Clip duration hint:
{{clip_duration_hint}}

Camera shots:
{{camera_shots_json}}

Panel plan:
{{panel_plan_json}}

Target panel content:
{{panel_text}}

Video prompt:
{{video_prompt}}

Negative prompt:
{{negative_prompt}}

Source storyboard asset:
- asset_id: {{source_storyboard_asset_id}}
- local_path: {{source_storyboard_asset_path}}
- remote_url: {{source_storyboard_asset_url}}

# Authoritative Project Visual Style

{{visual_style_prompt}}

The visual style above is the authoritative rendering target for every clip keyframe in this project. Match its medium, CGI/live-action treatment, material rendering, color palette, contrast, exposure, lighting quality, atmospheric effects, lens character, texture density, and finishing level. Do not replace it with a generic photographic or live-action look.

# Image References

If reference images are attached to this request, interpret them in this order:

- image_1: current clip 12-panel storyboard sheet; use it only to locate and understand the target panel `{{panel_ref}}`.
- image_2: project key vision; this is the shared style and world anchor for every clip. Use it to lock the authoritative rendering medium, material language, palette, contrast, exposure, cinematic lighting, atmosphere, lens character, texture density, and finishing level. Do not copy its characters or composition unless required by the target panel.
- following roleboard images: lock character identity, face shape, hair, body type, age feeling, costume, and role-specific details; they do not override image_2's project-wide rendering style.
- following layout images: lock scene space, structure, materials, lighting direction, scale, and movement paths; they do not override image_2's project-wide rendering style.
- following prop images, if any: lock prop shape, material, color, scale, and usage state.

Use the reference content as production guidance, but generate exactly one finished cinematic frame. Do not reproduce the storyboard grid, roleboard layout, scene three-view sheet, or prop design sheet.
The black-and-white storyboard sheet controls composition and action only; never inherit its pencil-sketch medium, monochrome palette, arrows, panel borders, or annotation style into the finished frame.

# Visual Requirements

- Generate exactly one clean finished cinematic frame in the authoritative project visual style above.
- The output canvas must be {{final_aspect_ratio}} vertical video frame composition when {{final_aspect_ratio}} is a vertical ratio; do not output a square image even if the storyboard reference sheet is square.
- If `frame_role` is `start`, the image must faithfully represent the current clip P01 / first panel content.
- If `frame_role` is `end`, the image must faithfully represent the current clip P12 / last panel content.
- Preserve character identity, face shape, hair, clothing, posture, scene layout, props, lighting direction, camera angle, focal length, and mood from the storyboard plan.
- Keep rendering medium, character stylization, surface materials, palette, contrast curve, exposure, light softness, atmospheric density, lens response, texture density, and finishing level consistent with image_2 and with every other clip keyframe.
- Convert the storyboard sketch into a finished cinematic frame; do not copy the storyboard sheet layout.
- No panels, no grid, no split-screen, no red diagonal slash cut mark, no arrows, no production notes, no UI, no frame labels.
- No subtitles, speech bubbles, watermarks, logos, QR codes, readable text, file names, asset IDs, or model names.
- Keep the frame visually stable and suitable as a first/last frame reference for video generation.
