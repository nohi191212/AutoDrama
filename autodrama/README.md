# AutoDrama Python package

源代码位于 `autodrama/src/autodrama`。镜头预生成采用 `clip_to_shots → layout_to_background_prompt → shot_background_image_generation → shot_keyframe_prompt → shot_keyframe_image_generation → shot_manifest_generation` 链路。

所有旧 storyboard 产物均不兼容；请从新的 shot 链路重新生成。
