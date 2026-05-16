# 任务

根据用户提供的剧情大纲，规划完整短剧的总大纲和分集大纲。

# 输入

标题：{{title}}

剧情大纲：
{{raw_script}}

目标集数：{{episode_count}}

单集目标时长：{{episode_duration_seconds}} 秒

必须使用的分集键名：{{episode_keys}}

# 要求

- 由你根据剧情节奏、冲突推进和爽点位置自主分集。
- 必须严格规划 {{episode_count}} 集，不能多集或少集。
- 每集目标时长约 {{episode_duration_seconds}} 秒。
- `episode_count` 必须等于 {{episode_count}}。
- `target_duration_seconds` 必须等于 {{episode_duration_seconds}}。
- `episode_outlines` 必须包含且只包含这些键名：{{episode_keys}}。
- `outline` 写整体剧情概述，`episode_outlines` 写每一集的独立分集大纲。
- 剧情应当包含时间、地点、人物，发生了什么事情。剧情中出现的角色都需要有名字。
- 剧情要有清晰冲突、反转或爽点。
- 不要生成旁白。
- 输出必须符合调用方提供的 JSON schema。
