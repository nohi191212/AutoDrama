固定输入说明：
- {{clip_start_frame_slot}}：clip_start_frame。首个 clip 时是当前 clip 的首帧；非首个 clip 时是上一 clip 的尾帧，只用于当前视频开头连续性。
- {{clip_end_frame_slot}}：clip_end_frame。当前 clip 的尾帧，视频必须最终收束到这张图。
- {{storyboard_input_slot}}：当前 clip 的 12 宫格故事板整图，只用于构图、景别、机位、动作方向、镜头节奏、camera shot 边界和面板顺序。
- 人物参考：{{roleboard_input_slots}}，只用于人物身份、脸型、发型、服装、体态、配饰和年龄感。
- 场景参考：{{layout_input_slots}}，只用于空间结构、材质、光照、尺度和可取景区域。
- 道具参考：{{prop_input_slots}}，只用于道具造型、材质、尺寸和可识别细节。

首尾帧与衔接规则：
{{clip_continuity_instructions}}

输入清单：
{{shot_video_inputs_json}}

当前 clip 的 camera shot 与十二宫格面板内容：
{{video_prompt}}

模型强相关负向规则：
{{negative_rules}}

生成要求：
生成 {{duration_seconds}} 秒电影级真人剧质感视频。严格依据当前 clip 的 camera shot 段落连续推进动作、情绪、声音和对白；不要把每个秒点、每个面板或每句描述都当作切镜点。只有 video_prompt 明确标出的 Camera Shot 边界才允许切镜，大部分镜头保持 3-6 秒连续运动。首尾帧优先级高于 storyboard、人物、场景和道具参考；12 宫格故事板只作为关键帧顺序和切镜标记参考，参考图片只作为静态身份、空间、道具和构图锚点，不要直接展示参考图版式或三视图布局。非首个 clip 必须从上一 clip 尾帧开始并立刻硬切到当前 clip 的 P01 内容，不要把上一 clip 尾帧丝滑变形为当前内容。
