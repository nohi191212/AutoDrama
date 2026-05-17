# 任务

打磨多集详细剧本，提升爽点、逻辑性和镜头可执行性。

# 输入

标题：{{title}}

详细剧本：
{{detailed_script}}

目标集数：{{episode_count}}

单集目标时长：{{episode_duration_seconds}} 秒

必须使用的分集键名：{{episode_keys}}

画面风格：{{visual_style_label}}

画面风格要求：{{visual_style_prompt}}

# 要求

- 输出 `final_script`，键名必须保持且只包含：{{episode_keys}}。
- 不要合并、拆分、重命名或删除任何集。
- 每集内容适合约 {{episode_duration_seconds}} 秒短片。
- 修复明显逻辑漏洞。
- 增强关键视觉线索，方便后续分镜。
- 所有增强的视觉线索都要符合画面风格，并能被后续人物形象图、场景图和视频生成复用。
- 不要混用与画面风格冲突的视觉描述。
- 不要加入旁白。
- 输出必须符合调用方提供的 JSON schema。
- 输出中，冒号""不能再有""，要替换成「」

// 原始（会报错）
{"text": "他说"你好"，然后就走了"}

// 你的方案（合法 JSON）
{"text": "他说「你好」，然后就走了"}
