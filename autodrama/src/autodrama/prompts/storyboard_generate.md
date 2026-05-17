# 任务

为指定集生成分镜 JSON。分镜会写入 slots 目录，不写入 state.json。

# 输入

标题：{{title}}

集键名：{{episode_key}}

本集最终剧本：
{{episode_script}}

角色资产：
{{roles}}

道具资产：
{{props}}

场景资产：
{{layouts}}

BGM 资产：
{{bgms}}

画面风格：{{visual_style_label}}

画面风格要求：{{visual_style_prompt}}

# 要求

- `episode_key` 必须等于输入的集键名。
- 生成 3-9 个分镜，按 `index` 从 1 递增。
- 每个分镜必须引用已有 `layout_id`，如出现角色、道具、BGM，也必须引用已有 ID。
- `duration_seconds` 合计应接近本集目标时长，单个镜头通常 3-8 秒。
- `ref_frame_prompt` 用于参考帧生图，必须包含画面风格、场景、人物、道具、镜头构图。
- `video_prompt` 用于视频生成，必须包含动作、镜头运动、情绪和画面连续性。
- 不要引用不存在的角色、道具、场景或 BGM。
- 输出必须符合调用方提供的 JSON schema。
