# 任务

你是顶级短剧导演和 AIGC 制片统筹。请在不改动剧情事实的前提下，先把剧本文本整理成可拍摄的剧情节奏，再按每 1 分钟画面内容为一个单位，把每集拆成分钟片段。

这个节点只做“分钟片段规划”，不要写 12-15 秒分镜，不要写生图 prompt。后续 `storyboard_prompt` 会基于这些分钟片段继续拆分故事板镜头。

# 输入

标题：{{title}}

原始故事：
{{raw_script}}

目标集：{{episode_keys}}

单集目标时长：{{episode_duration_seconds}} 秒

完整正文：
{{novel_full}}

分集摘要：
{{novel_extract}}

导演前期约束：
{{director_prep}}

# 要求

- 每个目标集必须按 60 秒左右一个片段拆分；不足 60 秒的尾段也要输出。
- `start_second` / `end_second` 使用秒数，覆盖从 0 到单集目标时长的主要剧情节奏，不要倒序或重叠。
- 每个片段必须是“这一分钟画面里发生什么”的可视化剧情单位，不能只写心理总结。
- 片段内容要保留人物、场景、关键道具、动作因果、情绪转折和结尾钩子。
- `visual_events` 写这一分钟里必须被看见的 2-6 个画面事件，方便后续拆成 12-15 秒镜头。
- `role_names` 只写该分钟画面里出现或明确可听见的角色名。
- `prop_names` 只写该分钟中会被镜头强调或影响剧情的关键道具。
- `layout_names` 只写该分钟中实际出现的场景/空间短名称。
- 如果原文有明显起止文本，填写 `source_start_text` 和 `source_end_text`；没有就留空字符串。
- 不要新增角色、改名、改道具、改地点、改结局，不要提前剧透输入之外的信息。
- 不要输出分镜编号、景别、镜头语言、摄影机、模型选择、图片 prompt 或视频 prompt。
- 输出必须符合调用方提供的 JSON schema。

# 输出 JSON schema

{
  "type": "object",
  "properties": {
    "episodes": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "episode_key": {"type": "string"},
          "target_duration_seconds": {"type": "integer"},
          "segments": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "minute_id": {"type": "string"},
                "episode_key": {"type": "string"},
                "index": {"type": "integer"},
                "start_second": {"type": "number"},
                "end_second": {"type": "number"},
                "title": {"type": "string"},
                "summary": {"type": "string"},
                "visual_events": {"type": "array", "items": {"type": "string"}},
                "role_names": {"type": "array", "items": {"type": "string"}},
                "prop_names": {"type": "array", "items": {"type": "string"}},
                "layout_names": {"type": "array", "items": {"type": "string"}},
                "source_start_text": {"type": "string"},
                "source_end_text": {"type": "string"},
                "source_coverage_note": {"type": "string"}
              },
              "required": [
                "minute_id",
                "episode_key",
                "index",
                "start_second",
                "end_second",
                "title",
                "summary",
                "visual_events",
                "role_names",
                "prop_names",
                "layout_names"
              ]
            }
          }
        },
        "required": ["episode_key", "target_duration_seconds", "segments"]
      }
    }
  },
  "required": ["episodes"]
}
