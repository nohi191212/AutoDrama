# AutoDrama Python package

源代码位于 `autodrama/src/autodrama`。镜头预生成采用 `clip_to_shots → layout_to_background_prompt → shot_background_shot_reference → shot_background_image_generation → shot_keyframe_prompt → shot_keyframe_image_generation → shot_manifest_generation` 链路。每个 clip 只绑定一个场景；每个 shot 的人物站位与相机合同会先生成机位示意图，再与场景空间母版一起约束无人背景。相关最终提示词模板均为英文，并直接发送给裸模型 API。

所有旧 storyboard 产物均不兼容；请从新的 shot 链路重新生成。
