# 任务

根据总大纲和分集大纲扩写为多集详细剧本。

# 输入

标题：{{title}}

原始故事：
{{raw_script}}

总大纲：
{{outline}}

分集大纲：
{{episode_outlines}}

目标集数：{{episode_count}}

单集目标时长：{{episode_duration_seconds}} 秒

必须使用的分集键名：{{episode_keys}}

画面风格：{{visual_style_label}}

画面风格要求：{{visual_style_prompt}}

# 要求

- 由你根据每集分集大纲自主扩写具体剧情、场景和对白。
- `detailed_script` 必须包含且只包含这些键名：{{episode_keys}}。
- 每集内容适合约 {{episode_duration_seconds}} 秒短片。
- 每集之间要有连续性，但每集都要有独立冲突或钩子。
- 保留动作、对白、场景变化和关键情绪。
- 场景、光线、材质、人物调度和关键视觉道具要与画面风格一致，方便后续场景图和视频生成。
- 不要混用与画面风格冲突的视觉描述。
- 不要生成旁白。
- 输出必须符合调用方提供的 JSON schema。
- 输出中，冒号""不能再有""，要替换成「」

// 原始（会报错）
{"text": "他说"你好"，然后就走了"}

// 你的方案（合法 JSON）
{"text": "他说「你好」，然后就走了"}
