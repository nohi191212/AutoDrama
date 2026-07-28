为下列单一角色造型写一条可直接交给图像模型的中文身份板提示词。当前造型优先；变体只改变其中明确变化的内容，其余身份特征保持一致。

人物：{{character_intro}}

当前造型：{{appearance_asset}}

角色板风格：{{roleboard_style_prompt}}

构图：{{roleboard_view_requirement}}

只输出 JSON，且只含 `roleboard_prompt`、`roleboard_negative_prompt`、`voice_profile_prompt`、`design_notes`。

- `roleboard_prompt` 只写可见画面：主体身份、外形、服装或表面、构图与水墨二维国漫风格；不要复述资料、项目背景或写成制作指南。画面只能出现当前命名主体；资料里提到的其他具名角色或生物即使是同伴，也不可一同画入。未明确的特征直接省略，不要把“不得臆造”等说明写进画面提示。必须严格采用构图要求里的正面、侧面、背面三个完整全身视图，不要写出任何其他数目或额外视图。
- `roleboard_negative_prompt` 简短覆盖身份漂移、额外主体、畸形、视图重叠、裁切、文字、字幕、水印和 logo。
- 有台词时 `voice_profile_prompt` 写稳定的声音画像；否则为空字符串。`design_notes` 仅保留确实需要人工确认的歧义，没有则为空字符串。
