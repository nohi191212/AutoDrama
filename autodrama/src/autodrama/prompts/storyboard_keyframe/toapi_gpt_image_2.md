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

# Visual Requirements

- Generate exactly one clean cinematic live-action frame.
- If `frame_role` is `start`, the image must faithfully represent the current clip P01 / first panel content.
- If `frame_role` is `end`, the image must faithfully represent the current clip P12 / last panel content.
- Preserve character identity, face shape, hair, clothing, posture, scene layout, props, lighting direction, camera angle, focal length, and mood from the storyboard plan.
- Convert the storyboard sketch into a finished cinematic frame; do not copy the storyboard sheet layout.
- No panels, no grid, no split-screen, no red diagonal slash cut mark, no arrows, no production notes, no UI, no frame labels.
- No subtitles, speech bubbles, watermarks, logos, QR codes, readable text, file names, asset IDs, or model names.
- Keep the frame visually stable and suitable as a first/last frame reference for video generation.
