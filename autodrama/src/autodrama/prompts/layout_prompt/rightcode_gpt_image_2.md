# 任务

为 RightCode GPT-Image-2 生成场景参考图提示词。根据结构化场景资产 `layouts` 和导演预生成的 `visual_tone`，输出纯文字提示词。

这个节点只写提示词文字，不生成图片，不输出尺寸比例，不输出画幅词，不输出模型名称。

Each prompt must be English. A base prompt must contain `three consistent views` and describe one photorealistic empty location with stable structure; a variant only describes its state delta while preserving the supplied base space.

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
- base prompt 要强调空间结构、镜头可复用锚点、前中后景关系、可走动区域、可放道具表面、材质和光线逻辑，避免抽象形容词堆叠。
- variant prompt 要强调基于 `reference_asset_name` 的参考图保持结构一致，只做 `state_delta` 中的状态变化；变化文字要短而明确，适合图像编辑式生成。
- 默认无人空场景。禁止人物、背影、手、剪影、人群、照片里的真人、海报人物、字幕、水印、logo、可读门牌或屏幕文字。
- 输出必须符合调用方提供的 JSON schema。
