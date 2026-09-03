# 任务

根据人物资料，为单一角色造型写一条可直接生成角色身份板的中文画面描述。

# 输入

人物简介：
{{character_intro}}

当前造型：
{{appearance_asset}}

视觉基调：
{{visual_tone}}

统一角色板风格：
{{roleboard_style_prompt}}

视图要求：
{{roleboard_view_requirement}}

# 输出

只输出 JSON，且只包含 `roleboard_prompt`、`roleboard_negative_prompt`、`voice_profile_prompt`、`design_notes`。

- `roleboard_prompt` 描述一张 16:9 的单角色三视图身份板：固定脸部、年龄感、发型、体型、服装、鞋履和视觉标志；严格只包含正面、侧面、背面三个等尺度完整全身视图。背景干净，视图彼此分离，不出现主视觉大图、近景、细节格、额外视角、无关人物或环境剧情。
- 稳定身份只能读取当前造型的 `identity_invariants`，服装只能读取 `wardrobe`；不得从人物简介、剧情文本或旧描述字段补全。变体只能采用其结构字段明确声明的差异，并保持所引用基础造型的其余身份特征一致。不得把单镜动作、姿势、情绪、手持物或瞬时效果写进身份板。
- `roleboard_negative_prompt` 覆盖身份漂移、额外人物、肢体缺失、视图重叠、裁切脸部、无关文字、字幕、水印和 logo。
- 有台词时，`voice_profile_prompt` 写一段稳定的声音画像；否则使用空字符串。
- `design_notes` 仅保留需要人工确认的视觉歧义；没有则使用空字符串。
