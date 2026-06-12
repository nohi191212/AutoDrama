# 任务

你是短剧分镜导演。请根据导演前期的镜头节拍、完整剧情正文、剧情摘要、角色身份板信息和项目画幅，把每个目标集拆解成 12 宫格故事板脚本。

# 输入

项目标题：
{{title}}

目标集：
{{episode_keys}}

目标宫格数量：
{{panel_count}}

最终画面比例：
{{aspect_ratio}}

建议故事板网格：
{{grid}}

原始故事：
{{raw_script}}

目标集剧情摘要：
{{novel_extract}}

目标集完整正文：
{{novel_full}}

导演前期与镜头节拍：
{{director_prep}}

角色与身份板摘要：
{{roleboard_context}}

# 输出要求

- 只输出 JSON。
- JSON 只能包含字段 `storyboards`。
- `storyboards` 中每个目标集输出 1 个对象，`episode_key` 必须使用输入中的集 key。
- 每个 `panels` 必须严格为 {{panel_count}} 个镜头，`index` 从 1 到 {{panel_count}} 连续递增。
- 每个镜头必须写清楚：
  - `shot_size`: 景别，例如远景、全景、中景、近景、特写、过肩、低角度近景。
  - `camera_position`: 机位和镜头方向，例如平视、低机位、俯拍、侧后方、过肩、贴地。
  - `composition`: 构图和主体位置，说明前景/中景/背景、左右关系、视线方向和关键道具位置。
  - `action`: 角色或道具在这一格里的明确动作，不要只写情绪。
  - `emotion`: 当前情绪和表演强度。
  - `camera_movement`: 推、拉、摇、移、跟、手持、固定、轻微升降等；静态镜头也要写“固定镜头”。
  - `sound_effects`: 环境声、动作声、转场声、对白前后听感或音乐提示；没有对白也要写声音设计。
- 12 个镜头必须覆盖当前集核心剧情起承转合，不要把同一动作重复拆成多个近似镜头。
- 优先继承导演前期里的 `shot_beats`；如果节拍少于 12 个，请在不增加新剧情事实的前提下拆细动作、反应、道具证据和空间转换。
- 角色外观、服装、道具和身份必须遵守角色身份板摘要；不要新增角色，不要改名，不要把背景人群当主要角色。
- `image_prompt` 必须是一条可直接交给图像模型的 12 宫格黑白线稿故事板生成 prompt，要求每格编号 1-12，动作、构图、镜头顺序准确，画面干净，不追求最终画质，不需要上色。
- 故事板图可以有很小的镜头编号和极短镜头标题，但不要出现字幕、对白气泡、水印、logo、文件名、项目名或大段文字。

Required JSON schema:
{
  "type": "object",
  "properties": {
    "storyboards": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "episode_key": {"type": "string"},
          "aspect_ratio": {"type": "string"},
          "grid": {"type": "string"},
          "story_summary": {"type": "string"},
          "panels": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "index": {"type": "integer"},
                "title": {"type": "string"},
                "shot_size": {"type": "string"},
                "camera_position": {"type": "string"},
                "composition": {"type": "string"},
                "action": {"type": "string"},
                "emotion": {"type": "string"},
                "camera_movement": {"type": "string"},
                "sound_effects": {"type": "string"},
                "transition": {"type": "string"}
              },
              "required": [
                "index",
                "title",
                "shot_size",
                "camera_position",
                "composition",
                "action",
                "emotion",
                "camera_movement",
                "sound_effects"
              ]
            }
          },
          "image_prompt": {"type": "string"}
        },
        "required": ["episode_key", "aspect_ratio", "grid", "panels", "image_prompt"]
      }
    }
  },
  "required": ["storyboards"]
}
