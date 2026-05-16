# 任务

打磨多集详细剧本，提升爽点、逻辑性和镜头可执行性。

# 输入

标题：{{title}}

详细剧本：
{{detailed_script}}

目标集数：{{episode_count}}

单集目标时长：{{episode_duration_seconds}} 秒

必须使用的分集键名：{{episode_keys}}

# 要求

- 输出 `final_script`，键名必须保持且只包含：{{episode_keys}}。
- 不要合并、拆分、重命名或删除任何集。
- 每集内容适合约 {{episode_duration_seconds}} 秒短片。
- 修复明显逻辑漏洞。
- 增强关键视觉线索，方便后续分镜。
- 不要加入旁白。
- 输出必须符合调用方提供的 JSON schema。
