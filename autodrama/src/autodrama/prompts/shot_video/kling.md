固定输入说明：
- {{storyboard_input_slot}}：当前 shot 的 12 宫格故事板整图，只锁定构图、景别、机位、动作方向、镜头节奏和顺序。
- 人物参考：{{roleboard_input_slots}}，只锁定人物身份、脸型、发型、服装、体态、配饰和年龄感。
- 场景参考：{{layout_input_slots}}，只锁定空间结构、材质、光照、尺度和可取景区域。
- 道具参考：{{prop_input_slots}}，只锁定道具造型、材质、尺寸和识别细节。

输入清单：
{{shot_video_inputs_json}}

当前 shot 分镜逐秒内容：
{{video_prompt}}

模型强相关负向规则：
{{negative_rules}}

Kling Omni 执行要求：
生成 {{duration_seconds}} 秒电影级真人剧视频。严格按当前 shot 的逐秒分镜推进动作和镜头运动；参考图只作为输入锚点，不使用 subject element，不生成参考图版式、三视图布局、宫格或面板编号。
