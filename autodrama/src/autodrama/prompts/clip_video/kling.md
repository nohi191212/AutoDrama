固定输入说明：
- {{start_frame_input_slot}}：当前 clip 的首帧，必须作为视频起始画面与姿态锚点。
- {{end_frame_input_slot}}：当前 clip 的尾帧，必须作为视频结束画面与姿态锚点。
- {{storyboard_input_slot}}：当前 clip 的 12 宫格故事板整图，只用于辅助理解 Camera Shot 的构图、动作方向和切镜边界；不要把宫格、箭头或编号画进成片。
- 人物主体：由生成节点按当前 clip 的角色列表追加 Kling subject element，并通过 @role_1、@role_2 等占位符绑定；人物身份与音色以主体为唯一准则。

镜头与片段规则：
- 从首帧自然演进到尾帧，只在明确的 Camera Shot 边界切镜；不要把故事板面板当成独立镜头。
- 人物姿态、朝向和空间位置必须按 Camera Shot 描述连续变化，不得自行增加起身、转身或走位。

当前 clip 的 Camera Shot：
{{video_prompt}}

模型强相关负向规则：
{{negative_rules}}

Kling Omni 执行要求：
生成 {{duration_seconds}} 秒电影级真人剧视频。严格按当前 clip 的 2-3 个 Camera Shot 推进，只在明确边界切镜。固定机位必须严格保持固定，不得自行增加推近、拉远、摇移、环绕、升降、手持晃动、跟随、变焦或拉焦。角色身份和音色以 Kling subject element 为准。首尾帧控制起止状态，故事板只辅助镜头顺序。不要生成参考图版式、三视图、宫格、箭头或面板编号。
