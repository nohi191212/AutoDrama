# 任务

检查场景列表，合并重复场景并统一命名。

# 输入

标题：{{title}}

当前场景：
{{layouts}}

画面风格：{{visual_style_label}}

画面风格要求：{{visual_style_prompt}}

# 要求

- 如果多个场景本质上是同一空间，只保留一个，并合并 `episode_keys`。
- 保留所有剧情需要的空间变化。
- 修正 `prompt`，保证所有场景都是无人物场景图 prompt，并符合画面风格。
- `merge_notes` 简短说明合并或未合并原因。
- 输出必须符合调用方提供的 JSON schema。
