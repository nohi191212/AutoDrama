# 任务

为 ToAPI GPT-Image-2 生成场景参考图提示词。根据结构化场景资产 `layouts` 和导演预生成的 `visual_tone`，输出纯文字提示词。

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
- base prompt 要写成完整无人空场景资产图提示词，重点让 GPT-Image-2 稳定理解空间：空间类型、入口方向、墙地天花或天空边界、固定结构、主要陈设、可站位/可绕行区域、可放置道具的表面、材质、主光和环境状态都要清楚。
- variant prompt 要写成参考图编辑式文本：以 `reference_asset_name` 的基准图为参考，保持原场景空间结构、固定陈设、材质关系、可行动线和镜头锚点不变，只改变 `state_delta` 描述的状态。不要重复原场景完整介绍。
- 默认无人空场景。禁止人物、背影、手、剪影、人群、照片里的真人、海报人物、字幕、水印、logo、可读门牌或屏幕文字。
- 文字要具体，避免只写“高级”“阴森”“华丽”等概念词。
- 输出必须符合调用方提供的 JSON schema。
