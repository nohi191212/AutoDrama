# 任务

为 Seedream 图像模型生成场景参考图提示词。根据结构化场景资产 `layouts` 和导演预生成的 `visual_tone`，输出纯文字提示词。

这个节点只写提示词文字，不生成图片，不输出尺寸比例，不输出画幅词，不输出模型名称。

# 输入

去重后的场景资产 layouts：
{{layouts}}

导演 visual_tone：
{{visual_tone}}

# 输出字段

- `layout_prompts`：数组，每项对应输入 `layouts` 中的一个场景资产。
- 每项必须包含 `name`、`group`、`asset_role`、`reference_asset_name`、`prompt_type`、`prompt`。
- `name`、`group`、`asset_role`、`reference_asset_name` 必须与输入对应 layout 完全一致。
- base 的 `prompt_type` 必须是 `text_to_image`；variant 的 `prompt_type` 必须是 `image_edit`。

# 要求

- 每个输入 layout 都必须输出一个提示词，不要新增输入中不存在的 name。
- 提示词必须继承 `visual_tone`，但不要另行预设画面尺寸、画幅比例、横竖版、分辨率或固定画风词。
- 不要出现 `16:9`、`9:16`、`1:1`、横向、竖向、portrait、landscape 等尺寸、比例或画幅要求。
- base prompt 要清晰列出空间边界、固定装置、可行走区域、光源、材质、环境粒子或天气状态，便于模型保持空间一致。
- variant prompt 要明确“参考 `reference_asset_name` 的原场景图保持结构不变”，只描述 `state_delta` 中的状态差异；不要重复原场景完整空间信息。
- 默认无人空场景。禁止人物、背影、手、剪影、人群、照片里的真人、海报人物、字幕、水印、logo、可读门牌或屏幕文字。
- 输出必须符合调用方提供的 JSON schema。
