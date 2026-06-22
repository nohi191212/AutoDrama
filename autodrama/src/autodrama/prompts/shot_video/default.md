固定输入说明：
- {{storyboard_input_slot}}：当前 shot 的 12 宫格故事板整图，只用于构图、景别、机位、动作方向、镜头节奏和顺序。
- 人物参考：{{roleboard_input_slots}}，只用于人物身份、脸型、发型、服装、体态、配饰和年龄感。
- 场景参考：{{layout_input_slots}}，只用于空间结构、材质、光照、尺度和可取景区域。
- 道具参考：{{prop_input_slots}}，只用于道具造型、材质、尺寸和可识别细节。

输入清单：
{{shot_video_inputs_json}}

当前 shot 分镜逐秒内容：
{{video_prompt}}

模型强相关负向规则：
{{negative_rules}}

生成要求：
生成 {{duration_seconds}} 秒电影级真人剧质感视频。严格依据当前 shot 分镜逐秒内容执行动作、情绪、声音和对白；参考图片只作为静态身份、空间、道具和构图锚点，不要直接展示参考图版式或三视图布局。
