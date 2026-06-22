固定输入说明：
- {{storyboard_input_slot}}：当前 shot 的 12 宫格故事板整图，用于锁定构图、景别、机位、动作方向、镜头节奏和镜头顺序。
- 人物参考：{{roleboard_input_slots}}，用于锁定人物身份、脸型、发型、体态、服装、配饰和年龄感。
- 场景参考：{{layout_input_slots}}，用于锁定空间结构、材质、光照、尺度、入口/窗/墙/地面关系和可取景区域。
- 道具参考：{{prop_input_slots}}，用于锁定道具造型、材质、尺寸和识别细节。

输入清单：
{{shot_video_inputs_json}}

当前 shot 分镜逐秒内容：
{{video_prompt}}

模型强相关负向规则：
{{negative_rules}}

Seedance 执行要求：
生成 {{duration_seconds}} 秒 9:16 真人短剧视频。动作按逐秒内容连续推进，人物不要突然正视镜头，镜头运动保持电影感和短剧节奏；不要把 12 宫格故事板画成最终画面，不要复刻三视图版式。
