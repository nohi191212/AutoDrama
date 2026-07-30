为下列单一角色造型写一条可直接交给图像模型的中文身份板提示词。

人物：{{character_intro}}

当前造型：{{appearance_asset}}

角色板风格：{{roleboard_style_prompt}}

构图：{{roleboard_view_requirement}}

只输出 JSON，且只含 `roleboard_prompt`、`roleboard_negative_prompt`、`voice_profile_prompt`、`design_notes`。

- `roleboard_prompt` 只写可见画面：主体身份、外形、服装或表面、构图与水墨二维国漫风格；不要复述资料、项目背景或写成制作指南。画面只能出现当前命名主体；资料里提到的其他具名角色或生物即使是同伴，也不可一同画入。未明确的特征直接省略，不要把“不得臆造”等说明写进画面提示。必须严格采用构图要求里的正面、侧面、背面三个完整全身视图，不要写出任何其他数目或额外视图。
- 稳定身份只能读取当前造型的 `identity_invariants`，服装只能读取 `wardrobe`；不得从人物简介、剧情文本或旧描述字段补全。变体只能采用其结构字段明确声明的差异，并保持所引用基础造型的其余身份特征一致。不得把单镜动作、姿势、情绪、手持物或瞬时效果写进身份板。
- `roleboard_negative_prompt` 简短覆盖身份漂移、额外主体、畸形、视图重叠、裁切、文字、字幕、水印和 logo。
- 有台词时 `voice_profile_prompt` 写稳定的声音画像；否则为空字符串。`design_notes` 仅保留确实需要人工确认的歧义，没有则为空字符串。
