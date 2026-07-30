# 任务

把角色相关的自由文本一次性提取为版本化的角色声音需求契约。该结果会被持久化；后续声音选择只能消费这些结构字段，不会重新扫描角色文本。

# 输入

角色声音相关上下文：
{{role_context}}

# 要求

- 只提取输入中有直接证据支持的声音需求，不补写剧情，不依据角色名猜测任何属性。
- `language` 只能是 `zh`、`en`、`ja`、`es`、`other` 或 `unspecified`。
- `gender_presentation` 描述期望的声音呈现，只能是 `female`、`male`、`neutral` 或 `unspecified`，不得把它解释为生理属性。
- `age_impression` 只能是 `child`、`teen`、`young_adult`、`adult`、`mature`、`elderly` 或 `unspecified`。
- `performance_traits` 只记录与表演直接相关、且有证据支持的要求。
- `baseline_emotion` 没有明确依据时使用 `null`。
- `hard_constraints` 只记录用户或上游明确提出的硬性声音约束，不把普通描述升级为硬约束。
- 无法确定的枚举字段必须使用 `unspecified`，不得根据代词、亲属称谓、职位或姓名猜测。
- `provenance.source` 必须是 `model`；`evidence` 逐项引用支持判断的最短原文，`confidence` 使用 0 到 1。
- 输出必须符合调用方提供的 JSON schema，不得添加额外字段。
