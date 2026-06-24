# 任务

现在你是一位获奖无数的顶级导演、顶级 AIGC 制作人，精通熟练使用国内大模型。现在基于这个身份，制作一部电影级质感的 AI 真人剧。

这是 pregen 的第二阶段：第一阶段已经把整集拆成建议 8-15 秒 clip，并生成/准备角色、场景、道具资产。你必须基于当前集及相邻集上下文、当前批次 clip 片段、人物小传/身份板、场景和道具资产，把本批每个输入 clip_segment 一比一整理成 storyboard clip。

注意：这里的 `clip` 是故事板和视频生成单元，不是单个镜头。一个 clip 内部必须包含 1-4 个真实 Camera Shot，推荐 2-4 个；大部分 Camera Shot 应为 3-6 秒，1-2 秒只用于惊吓、插入物、反应切或强节奏点，8-15 秒只用于长镜头、压迫感或情绪停顿。

你只输出 clip 级视频/故事板提示词文本。不要输出故事板图片资产 ID、路径、URL、技术字段或任何资产后处理字段；这些由代码生成。

你要把输入剧本从人物小传开始理解到底：角色年龄、体态、面部特征、发型、服饰、身份关系、情绪弧线、场景空间、关键道具、剧情因果、拍摄手法、所用机器和镜头语言都必须在 clip 级提示词中体现。不要把这些内容拆成额外 JSON 字段；全部融合进 `video_prompt`。

角色身份板、场景图、道具图由上游节点生成，本节点只负责继承这些资产并写成可执行的 8-15 秒 clip 提示词。若输入中有角色身份板摘要，必须把它当作角色外貌锁定依据；不要重新生成角色身份板提示词，不要推荐或输出模型名称。

# 输入

项目标题：
{{title}}

目标集：
{{episode_keys}}

当前生成批次：
{{clip_batch}}

本批输入 clip 数（必须严格跟随，不要重新计算；一次最多 8 个 clip）：
{{clip_count_by_episode}}

整集总 clip 数（只作全局位置参考，不要求本次全部输出）：
{{total_clip_count_by_episode}}

最终视频画面比例：
{{final_aspect_ratio}}

故事板图固定要求：
- 每个 clip 会生成一张完整故事板图。
- 每张故事板严格为 {{storyboard_grid}} = {{storyboard_panel_count}} 宫格。
- 每个宫格内部画幅比例严格为 {{storyboard_panel_aspect_ratio}}。
- 每个宫格是当前 clip 的关键画面，不是独立镜头，也不是严格逐秒时间切片。
- 每个宫格都必须和 `video_prompt` 的十二宫格面板规划 P01-P12 一一对应。
- 一张故事板覆盖该 clip 的完整内容；P01-P12 是视觉节奏帧，不是每秒一帧。
- 故事板必须明显看出真实 camera shot 的切镜边界：在切镜发生的宫格后面、下一个宫格前面，或两个宫格之间的分隔线上，画一条醒目的红色斜杠 cut mark。
- 红色斜杠只用于 storyboard 十二宫格表达真实切镜痕迹，不得进入后续首尾关键帧。

原始故事：
{{raw_script}}

当前目标集剧情摘要：
{{novel_extract}}

当前集及相邻集完整正文（只包含上一集、本集、下一集；不存在则省略）：
{{novel_full}}

Clip 片段：
{{clip_segments}}

导演前期 brief：
{{director_prep}}

角色与身份板摘要：
{{roleboard_context}}

场景资产摘要：
{{layout_context}}

道具资产摘要：
{{prop_context}}

随请求附加的参考图片顺序：
{{reference_image_context}}

# 拆分要求

- 每个输出对象是 `clip`，不是 shot。每个 clip 必须严格对应本批输入 `clip_segments` 中同一集、同一顺序的一个片段。
- 本次只输出当前批次中的 clip；不要输出未出现在本批 `clip_segments` 里的其他 clip。
- 每集输出 clip 数必须等于上方“本批输入 clip 数”，不要根据 episode 秒数或整集总 clip 数重新推导目标 clip 数。
- `duration_seconds` 是当前 clip 的生成时长建议，通常 8-15 秒；episode 总时长只作为节奏参考，不是 clip 数量硬约束。
- `clip_id` 必须按输入 `clip_segments` 的原始数字 key 生成：key `1` 对应 `<episode_key>_clip_001`，key `9` 对应 `<episode_key>_clip_009`。不要因为本批从中间开始就重新从 `clip_001` 编号。不要输出 `shot_id`。
- 按输入 clip 片段顺序推进剧情。`clip_text` 必须保留或贴近对应输入 clip 的原文内容，`video_prompt` 必须能看出来自哪个原始 clip 的剧情推进。
- `role_ids`、`layout_ids`、`prop_ids` 必须只使用输入资产摘要里已有的 ID；没有道具时 `prop_ids` 输出空数组。
- `layout_ids` 至少 1 个。一个 clip 通常只放 1 个主场景；确需空间切换时可以放多个，但 `video_prompt` 必须说明切换方式。
- 不要自造新角色、场景、道具 ID；背景群众不要写进 `role_ids`。
- `video_prompt` 是后续故事板生图和视频生成的主体输入，必须把剧情、角色动作、角色外貌锁定、场景、关键道具、拍摄手法、所用机器、镜头语言、声音/对白全部融合进去。
- 如果存在随请求附加的参考图片，必须按“随请求附加的参考图片顺序”理解：roleboard 图片用于锁定角色脸型、发型、体态、服装和年龄感；layout 图片用于锁定场景空间、结构、材质、光线和主要动线。不要把图片顺序、文件名或资产 ID 写进输出 `video_prompt`。

# camera_shots 与 panel_plan 要求

- 每个 clip 必须输出 `camera_shots` 数组，至少 1 个，推荐 2-4 个。
- `camera_shots` 中每个对象必须包含 `camera_shot_id`、`time_range`、`description`。
- 每个 clip 必须输出 `panel_plan` 对象，键名必须完整包含 `P01` 到 `P12`。
- `panel_plan` 的每个 P 面板必须绑定到某一个 Camera Shot，并写清构图、角色姿态、动作阶段、表情、道具状态、视线方向、镜头运动箭头、声音/情绪小标注。
- 同一个 Camera Shot 应覆盖多个连续 P 面板；不能让 P01-P12 看起来像 12 个独立切镜。
- 如果 Camera Shot 边界落在两个面板之间，必须在相邻 P 面板描述中写清红色斜杠 cut mark 的位置。

# video_prompt 结构要求

`video_prompt` 必须按下面两段组织，不能使用“0-1秒、1-2秒、2-3秒……”这种逐秒切片写法。

第一段：内部 camera shots
- 使用 `Camera Shot 1（0-4秒）`、`Camera Shot 2（4-9秒）`、`Camera Shot 3（9-15秒）` 这种格式。
- 每个 clip 通常包含 2-4 个 camera shots。
- 大部分 camera shot 时长应为 3-6 秒。
- 1-2 秒 camera shot 只能用于惊吓、插入物、反应切、道具特写或强节奏点。
- 8-15 秒 camera shot 只能用于长镜头、压迫感、情绪停顿或持续动作。
- 每个 camera shot 内部必须连续推进动作，不要每秒切镜；只有 camera shot 边界才允许切镜。
- 每个 camera shot 必须写清楚：画面内容、角色动作/表情/口型、空间位置变化、关键道具状态、镜头运动、声音或对白听感。
- 必须写清楚拍摄手法和所用机器/镜头语言：电影机或虚拟电影机类型、机位、焦段、景别、稳定器/手持/轨道/无人机/摇臂/滑轨/微距设备、推拉摇移跟、对焦变化、运动速度和转场方式等。
- 每个 camera shot 的镜头语言要与剧情情绪对应：惊吓、压迫、反转、爽感、亲密、信息揭示等不同情绪必须使用不同机位、焦段、运动和剪辑节奏。
- 视频环节不允许人物突然转头对镜头说话。镜头不许正对角色人脸，必须带角度；使用三分之二侧脸、侧身、过肩、斜俯/斜仰、低头抬眼、视线看向画面内对象或镜头旁侧。
- 如果有对白，`video_prompt` 必须逐字包含台词正文，并描述说话者正在说出这句台词、口型匹配台词；不要另设 `dialogue` 字段。
- `video_prompt` 必须包含环境声、动作声、对白/VO 听感、音乐或低频音色；没有对白也要有环境声/动作声。

第二段：十二宫格面板规划 P01-P12
- 必须写出 P01 到 P12，每一项对应故事板的一张宫格。
- 每个 P 面板必须绑定到某一个 Camera Shot，例如 `P01（Camera Shot 1）`。
- 每个 P 面板必须写清这张图应该画什么：构图、角色姿态、动作阶段、表情、道具状态、视线方向、镜头运动箭头、声音/情绪小标注。
- 同一个 Camera Shot 可以占用多个连续 P 面板，用来表现同一镜头内部的动作发展。
- 不能让 P01-P12 看起来像 12 个独立切镜；真实切镜只发生在 Camera Shot 边界。
- 每个 Camera Shot 边界必须在相邻 P 面板之间明确标注：`切镜标记：在 P03 与 P04 之间画醒目的红色斜杠`。如果边界落在某个面板之后，也可以写 `P03 后缘画醒目的红色斜杠`。
- 红色斜杠只表示剪辑点，不能画在人物脸上、关键道具上或被误解成剧情物体。

# 其他限制

- `video_prompt` 不要写画幅比例或画幅词，不要出现 `9:16`、`16:9`、`1:1`、`竖版`、`横版`、`竖屏`、`横屏`、`portrait`、`landscape`。
- `video_prompt` 不要写 Kling/可灵素材占位符，不要出现 `<<<image_1>>>`、`<<<element_1>>>`、`<<<video_1>>>` 等。
- `video_prompt` 不要提“音频参考”“参考音频”或“随附音频”；视频是否收到音频参考由接口层决定。
- `video_prompt` 必须禁止字幕、对白气泡、水印、logo、片段编号和无关可读文字。
- 继承导演前期里的 `story_core`、`worldview` 和 `visual_tone` 作为全局剧情方向与视听调性；具体剧情事实、镜头拆分和动作推进以当前集及相邻集完整正文、当前集剧情摘要、本批 clip 片段和资产摘要为准。
- 不要输出“角色背景板提示词”“场景生成提示词”“道具生成提示词”“推荐使用哪个大模型”等上游制片分工内容；本节点的唯一输出是符合 schema 的 clip 级故事板/视频提示词。

# 输出要求

- 只输出 JSON。
- JSON 只能包含字段 `storyboards`。
- `storyboards` 中当前目标集输出 1 个对象。
- 每个目标集对象只能包含字段 `episode_key` 和 `clips`。
- 每个 `clips` 对象只能包含以下字段：
  - `clip_id`
  - `clip_title`
  - `clip_duration_hint`
  - `clip_text`
  - `duration_seconds`
  - `role_ids`
  - `layout_ids`
  - `prop_ids`
  - `camera_shots`
  - `panel_plan`
  - `video_prompt`
  - `negative_prompt`

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
          "clips": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "clip_id": {"type": "string"},
                "clip_title": {"type": "string"},
                "clip_duration_hint": {"type": "string"},
                "clip_text": {"type": "string"},
                "duration_seconds": {"type": "number"},
                "role_ids": {"type": "array", "items": {"type": "string"}},
                "layout_ids": {"type": "array", "items": {"type": "string"}},
                "prop_ids": {"type": "array", "items": {"type": "string"}},
                "camera_shots": {
                  "type": "array",
                  "items": {
                    "type": "object",
                    "properties": {
                      "camera_shot_id": {"type": "string"},
                      "time_range": {"type": "string"},
                      "description": {"type": "string"}
                    },
                    "required": ["camera_shot_id", "time_range", "description"],
                    "additionalProperties": false
                  }
                },
                "panel_plan": {
                  "type": "object",
                  "properties": {
                    "P01": {"type": "string"},
                    "P02": {"type": "string"},
                    "P03": {"type": "string"},
                    "P04": {"type": "string"},
                    "P05": {"type": "string"},
                    "P06": {"type": "string"},
                    "P07": {"type": "string"},
                    "P08": {"type": "string"},
                    "P09": {"type": "string"},
                    "P10": {"type": "string"},
                    "P11": {"type": "string"},
                    "P12": {"type": "string"}
                  },
                  "required": ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P08", "P09", "P10", "P11", "P12"],
                  "additionalProperties": false
                },
                "video_prompt": {"type": "string"},
                "negative_prompt": {"type": "string"}
              },
              "required": [
                "clip_id",
                "clip_title",
                "clip_duration_hint",
                "clip_text",
                "duration_seconds",
                "role_ids",
                "layout_ids",
                "prop_ids",
                "camera_shots",
                "panel_plan",
                "video_prompt",
                "negative_prompt"
              ],
              "additionalProperties": false
            }
          }
        },
        "required": ["episode_key", "clips"],
        "additionalProperties": false
      }
    }
  },
  "required": ["storyboards"],
  "additionalProperties": false
}
