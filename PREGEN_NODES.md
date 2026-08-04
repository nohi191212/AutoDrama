# 预生成节点

## 标记说明

- `【必须】`：完整运行默认 pregen 到 `shot_manifest_generation` 时必须经过的节点。图像审核节点也是交付门禁，不能当作普通检查跳过。
- `【可选】`：不属于默认必跑路径，只有在启用对应能力、采用替代路径或需要单独补资产时才运行。

## 默认预生成链路

默认链路在 `shot_manifest_generation` 结束。以下顺序与当前 `workflows/nodes/__init__.py` 中的 `PREGEN_NODES` 一致：

1. `【必须】` `script_import`：导入剧本并建立集级脚本结构。
2. `【必须】` `script_detail_expand`：扩写每集可生成所需的详细剧情文本。
3. `【必须】` `script_novel_extract`：从每集剧情中提取后续节点使用的结构化事实。
4. `【必须】` `key_vision_prompt`：生成项目主视觉图提示词。
5. `【必须】` `key_vision_image_generation`：生成主视觉图，作为后续视觉资产的风格参考。
6. `【必须】` `key_vision_image_audit`：审核主视觉图是否通过视觉交付门禁。
7. `【必须】` `role_extract_primary`：提取主要角色及其出场信息。
8. `【必须】` `role_extract_functional`：提取功能性角色及其出场信息。
9. `【必须】` `role_finalize`：合并、去重并固化角色和造型结构。
10. `【必须】` `roleboard_prompt`：为角色板及各造型生成提示词。
11. `【必须】` `roleboard_image_generation`：生成角色板图。
12. `【必须】` `roleboard_image_audit`：审核角色板图。
13. `【必须】` `prop_extract`：提取剧情中需要固定视觉身份的道具。
14. `【必须】` `prop_finalize`：合并、去重并固化道具结构。
15. `【必须】` `layout_extract`：提取剧情中的场景和空间布局。
16. `【必须】` `layout_finalize`：合并、去重并固化场景结构。
17. `【可选】` `layout_prop_boundary_review`：审核场景与道具边界；仅在 `app.enable_llm_audit=true` 时随默认整链执行。
18. `【必须】` `prop_prompt`：生成道具图提示词。
19. `【必须】` `layout_prompt`：生成场景母版图提示词。
20. `【必须】` `prop_image_generation`：生成道具图。
21. `【必须】` `prop_image_audit`：审核道具图。
22. `【必须】` `layout_image_generation`：生成场景母版/三视图。
23. `【必须】` `layout_image_audit`：审核场景母版图。
24. `【必须】` `clip_segment`：将每集剧情切分为完整的 clip 段落。
25. `【必须】` `clip_to_shots`：将 clip 规划为可生成的 shot，并建立角色、道具、场景引用。
26. `【必须】` `layout_to_background_prompt`：根据场景母版和 shot 规划可复用的无人背景提示词。
27. `【必须】` `shot_background_image_generation`：生成 shot 背景图。
28. `【必须】` `shot_background_image_audit`：审核 shot 背景图。
29. `【必须】` `shot_keyframe_prompt`：根据背景、角色和道具引用生成 shot 首帧提示词。
30. `【必须】` `shot_keyframe_image_generation`：生成 shot 关键帧图。
31. `【必须】` `shot_keyframe_image_audit`：审核 shot 关键帧图。
32. `【必须】` `shot_manifest_generation`：生成最终 shot manifest，作为 Generation 的输入。

### 默认链路中的条件节点

`layout_prop_boundary_review` 保留在默认节点注册顺序中，但默认整链运行时会根据配置决定是否执行：

- `app.enable_llm_audit=true`：在 `layout_finalize` 后执行；
- `app.enable_llm_audit=false`：默认整链跳过；需要时可通过 `pregen --only layout_prop_boundary_review` 显式运行。

其余图像审核节点当前属于必须的交付门禁，即使配置中存在历史上的图像审核开关，也不应从默认链路中删除。

## 默认链路之外的可选节点

这些节点仍然可以通过 `pregen --only <node>` 执行，但不应插入默认链路：

- `script_outline`：`script_import` 的替代脚本入口。
- `script_novel`：`script_detail_expand` 的替代脚本入口。
- `role_subject_frontal_image_generation`：生成角色正面主体参考图。
- `role_subject_frontal_image_audit`：审核角色正面主体参考图，应在对应生成节点之后执行。
- `role_kling_voice_generation`：为角色生成 Kling 相关声音资产。
- `role_subject_video_generation`：生成角色主体视频参考资产。
- `role_subject_element_generation`：生成角色主体元素/可复用主体资产。
- `role_voice_select`：从声音目录中显式选择角色音色。
- `bgm_design`：规划背景音乐。
- `bgm_generation`：生成背景音乐；需要先有 `bgm_design` 输出。

可选节点的使用场景：

- 使用 `script_outline`/`script_novel` 时，是有意采用替代脚本路径，不要与默认脚本节点无目的地重复运行；
- 角色正面图、主体视频、主体元素和角色音色节点只在下游 provider 或项目确实需要这些参考资产时运行；
- `bgm_design` 与 `bgm_generation` 只在项目需要生成 BGM 时运行。

## 依赖和局部重跑规则

- `roleboard_image_generation` 依赖 `key_vision_image_generation`；角色板图生成前必须先完成主视觉图及其审核。
- `prop_*`、`layout_*` 静态资产节点必须在 `clip_to_shots` 之前完成；shot 规划会引用这些结构和资产。
- `shot_background_image_generation` 必须先于 `shot_keyframe_prompt` 和 `shot_keyframe_image_generation`；关键帧以背景图作为第一张参考图。
- `shot_manifest_generation` 是默认 pregen 终点；完成后才能进入 `shot_dialogue_audio_generation`、`shot_video_generation` 等 Generation 节点。
- 每个 shot 必须有唯一、非空的 `narrative_angle`，并且只能映射到一个背景。背景可被兼容 shot 复用；背景图与关键帧图分别写入 `assets/images/shot_backgrounds/` 和 `assets/images/shot_keyframes/`。

`--episodes` 可用于支持 episode scope 的单节点重跑。`--shots` 仅用于背景、关键帧和 manifest 节点；选择共享背景的任一 shot 都会解析到该背景。强制重建背景会使其关联关键帧和 manifest 失效。

完整运行示例：

```powershell
run\start.cmd --config config.yaml --project <project_id> --until shot_manifest_generation
```

局部重跑示例：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only layout_to_background_prompt --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only shot_background_image_generation --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only shot_keyframe_image_generation --episodes 1 --shots 1-3 --force
```
