# 任务

根据去重后的结构化场景资产 `layouts` 和导演预生成的 `visual_tone`，输出后续场景图生成使用的纯文字提示词。

这个节点只写提示词文字，不生成图片，不输出尺寸比例，不输出画幅词，不输出模型名称。

# 输入

去重后的场景资产 layouts：
{{layouts}}

导演 visual_tone：
{{visual_tone}}

# 输出字段

只输出符合 JSON schema 的对象：

- `layout_prompts`：数组，每项对应输入 `layouts` 中的一个场景资产。

每项必须包含：

- `name`：必须与输入 layout 的 name 完全一致。
- `group`：必须与输入 layout 的 group 一致。
- `asset_role`：必须与输入 layout 的 asset_role 一致。
- `reference_asset_name`：必须与输入 layout 的 reference_asset_name 一致。
- `prompt_type`：base 必须是 `text_to_image`；variant 必须是 `image_edit`。
- `prompt`：纯文字生图提示词。

# 通用要求

- 每个输入 layout 都必须输出一个提示词，不要新增输入中不存在的 name。
- 提示词必须继承 `visual_tone`，但不要另行预设画面尺寸、画幅比例、横竖版、分辨率或固定画风词。
- 不要出现 `16:9`、`9:16`、`1:1`、横向、竖向、portrait、landscape 等尺寸、比例或画幅要求。
- 所有场景默认是无人空场景。不要出现人物、背影、手、剪影、人群、照片里的真人、海报人物、字幕、水印、logo、可读门牌或屏幕文字；除非场景资产明确需要，也不要生成可读文字。
- 提示词要服务后续镜头参考，让视频生成能判断角色可以站在哪里、道具可以放在哪些表面、镜头可以从哪些方向取景。
- 不要写路径、URL、文件名、节点名、项目 ID 或模型名称。

# base：text_to_image

当 `asset_role=base` 时，输出 `prompt_type="text_to_image"`。

base prompt 要写成专业、可复用的无人空场景资产图提示词，不是剧情最终帧、封面、分镜、气氛概念图或设计图板。

必须清楚描述：

- 空间类型和功能。
- 入口、墙面、地面、天花或天空边界。
- 固定结构和固定陈设。
- 可走动区域、可绕行路径、可放置道具的表面。
- 前中后景或镜头可取景锚点。
- 材质、基础光线、环境状态和整体氛围。

# variant：image_edit

当 `asset_role=variant` 时，输出 `prompt_type="image_edit"`。

variant prompt 要写成基于参考图像的编辑提示词，必须明确：

- 以 `reference_asset_name` 对应的基准场景图为参考。
- 保持原场景空间结构、入口位置、墙地顶或天空边界、固定陈设、材质关系、可行动线和镜头锚点不变。
- 只改变 `state_delta` 中写明的状态，例如昼夜、天气、灯光、停电、破损、污染、烟尘、火光、仪式痕迹或战斗后变化。

variant prompt 不要重复 base 的完整空间介绍，不要新增未说明的房间、门窗、楼梯、巨大装置、人物、可读文字或剧情动作。

输出必须符合调用方提供的 JSON schema。
