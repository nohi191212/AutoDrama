固定输入说明：
- {{clip_start_frame_slot}}：clip_start_frame。首个 clip 时是当前 clip 的首帧；非首个 clip 时是上一 clip 的尾帧，只用于当前视频开头连续性。
- {{clip_end_frame_slot}}：clip_end_frame。当前 clip 的尾帧，视频必须最终收束到这张图。
- {{storyboard_input_slot}}：当前 clip 的 12 宫格故事板整图，只锁定构图、景别、机位、动作方向、camera shot 边界、镜头节奏和面板顺序。
- 人物参考：{{roleboard_input_slots}}，只锁定人物身份、脸型、发型、服装、体态、配饰和年龄感。
- 场景参考：{{layout_input_slots}}，只锁定空间结构、材质、光照、尺度和可取景区域。
- 道具参考：{{prop_input_slots}}，只锁定道具造型、材质、尺寸和识别细节。

首尾帧与衔接规则：
{{clip_continuity_instructions}}

输入清单：
{{shot_video_inputs_json}}

当前 clip 的 camera shot 与十二宫格面板内容：
{{video_prompt}}

模型强相关负向规则：
{{negative_rules}}

Kling Omni 执行要求：
生成 {{duration_seconds}} 秒电影级真人剧视频。严格按当前 clip 的 Camera Shot 段落推进动作和镜头运动；不要逐秒硬切，不要把每个故事板面板当成独立镜头。首尾帧优先级高于 storyboard、人物、场景和道具参考。只在 video_prompt 明确标出的 Camera Shot 边界处切镜，大部分镜头保持 3-6 秒连续运动。非首个 clip 必须从上一 clip 尾帧开始并立刻硬切到当前 clip 的 P01 内容，不要做丝滑变形过渡。参考图只作为输入锚点，不使用 subject element，不生成参考图版式、三视图布局、宫格或面板编号。
