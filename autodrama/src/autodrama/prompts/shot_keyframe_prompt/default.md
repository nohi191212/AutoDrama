根据参考图生成一张镜头起始关键帧的画面描述。图1是必须保持的镜头背景与机位锚点；后续图片只用于绑定人物或道具身份。

镜头事件：
{{shot_description}}

唯一叙事角度：
{{narrative_angle}}

起始状态：
{{opening_state}}

参考图说明：
{{reference_guide}}

画面质量要求：
{{visual_quality}}

精确文字渲染要求：
{{text_rendering_instruction}}

只输出 JSON，且只能包含 `prompt_content`。`prompt_content` 只描述一张起始关键帧：保持图1的机位、透视、空间结构和光线；明确主体、人物在前中后景的位置、朝向、视线、遮挡与动作开始前状态。保持角色图中的脸、发型、服装、体型和道具图中的造型尺度。不要重新设计背景，不要描述完整动作过程或视频运镜。
