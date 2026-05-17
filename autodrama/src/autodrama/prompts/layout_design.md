# 任务

根据最终剧本、简约剧本、角色和道具，设计后续生图与视频可复用的无人物场景。

# 输入

标题：{{title}}

最终剧本：
{{final_script}}

简约分集剧本：
{{simple_script}}

全局简约剧本：
{{global_script}}

角色：
{{roles}}

道具：
{{props}}

画面风格：{{visual_style_label}}

画面风格要求：{{visual_style_prompt}}

# 要求

- 合并相同地点，不要为同一个空间反复生成多个场景。
- `name` 使用短名称，例如「雨夜办公室」「会议室」。
- `desc` 写空间结构、光线、材质、气氛和可复用的视觉特征。
- `prompt` 直接用于无人物场景图生成，必须明确“无人物”，并包含画面风格要求。
- `episode_keys` 标记该场景出现在哪些集。
- 输出必须符合调用方提供的 JSON schema。
