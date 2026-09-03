# 预生成节点

## 标记说明

- `【必须】`：完整运行默认 pregen 到 `shot_manifest_generation` 时必须经过的节点。图像审核节点也是交付门禁，不能当作普通检查跳过。
- `【可选】`：不属于默认必跑路径，只有在启用对应能力、采用替代路径或需要单独补资产时才运行。

## 默认预生成链路

默认链路在 `shot_manifest_generation` 结束。以下顺序与当前 `workflows/nodes/__init__.py` 中的 `PREGEN_NODES` 一致：

1. `【必须】` `script_import`：导入剧本并建立集级脚本结构。
2. `【必须】` `script_cinematic_adapt`：把每集原文导演化为可直接想象成画面、声音、动作与表演的连续文本，不以扩写篇幅为目标。
3. `【必须】` `script_novel_extract`：从每集剧情中提取后续节点使用的结构化事实。
4. `【必须】` `script_worldview_extract`：根据完整剧本提取纯英文类型与世界观简报，写入 `script_type`。
5. `【必须】` `key_vision_prompt`：根据 `script_type` 生成项目主视觉图提示词。
6. `【必须】` `key_vision_image_generation`：生成主视觉图，作为后续视觉资产的风格参考。
7. `【必须】` `key_vision_image_audit`：审核主视觉图是否通过视觉交付门禁。
8. `【必须】` `role_extract_primary`：提取主要角色及其出场信息。
9. `【必须】` `role_extract_functional`：提取功能性角色及其出场信息。
10. `【必须】` `role_finalize`：合并、去重并固化角色和造型结构。
11. `【必须】` `roleboard_prompt`：为角色板及各造型生成提示词。
12. `【必须】` `roleboard_image_generation`：生成角色板图。
13. `【必须】` `roleboard_image_audit`：审核角色板图。
14. `【必须】` `prop_extract`：提取剧情中需要固定视觉身份的道具，并为每个道具建立至少一个 `base` 逻辑资产定义；此处不是生成图片。
15. `【必须】` `prop_finalize`：合并、去重并固化道具结构。
16. `【必须】` `layout_extract`：提取剧情中的场景和空间布局。
17. `【必须】` `layout_finalize`：合并、去重并固化场景结构。
18. `【可选】` `layout_prop_boundary_review`：审核场景与道具边界；仅在 `app.enable_llm_audit=true` 时随默认整链执行。
19. `【必须】` `prop_prompt`：生成道具图提示词。
20. `【必须】` `layout_prompt`：生成场景母版图提示词。
21. `【必须】` `prop_image_generation`：生成道具图。
22. `【必须】` `prop_image_audit`：审核道具图。
23. `【必须】` `layout_image_generation`：以 Banana Pro 4K 生成 2:3 场景空间母版图，固定场景拓扑、尺度、入口、动线与陈设锚点。
24. `【必须】` `layout_image_audit`：审核场景母版图。
25. `【必须】` `clip_segment`：将每集剧情切分为完整的 clip 段落；每个 clip 必须且只能绑定一个场景，同一场景可被多个 clip 使用。
26. `【必须】` `clip_to_shots`：结合该 clip 唯一的场景空间母版规划 shot，并显式输出人物站位、相机位置、拍摄角度、景别、俯仰和视场角。
27. `【必须】` `layout_to_background_prompt`：根据场景母版和 shot 规划一对一的无人背景提示词。
28. `【必须】` `shot_background_shot_reference`：以场景空间母版为参考图，通过 Banana Pro 4K 图像编辑，为每个 shot 生成 2:3 机位、视场角、相机高度和人物地面站位示意图。
29. `【必须】` `shot_background_image_generation`：同时参考场景空间母版和当前 shot 机位示意图，以 Banana Pro 4K 生成 9:16 无人背景图。
30. `【必须】` `shot_background_image_audit`：审核 shot 背景图。
31. `【必须】` `shot_keyframe_prompt`：根据背景、角色和道具引用生成 shot 首帧提示词。
32. `【必须】` `shot_keyframe_image_generation`：生成 shot 关键帧图。
33. `【必须】` `shot_keyframe_image_audit`：审核 shot 关键帧图。
34. `【必须】` `shot_manifest_generation`：生成最终 shot manifest，作为 Generation 的输入。

### 默认链路中的条件节点

`layout_prop_boundary_review` 保留在默认节点注册顺序中，但默认整链运行时会根据配置决定是否执行：

- `app.enable_llm_audit=true`：在 `layout_finalize` 后执行；
- `app.enable_llm_audit=false`：默认整链跳过；需要时可通过 `pregen --only layout_prop_boundary_review` 显式运行。

其余图像审核节点当前属于必须的交付门禁，即使配置中存在历史上的图像审核开关，也不应从默认链路中删除。

## Node group

`--node-group` 会按表中顺序连续运行一个预生成节点组。组内节点全部完成后，才认为该组完成；显式运行组时，即使 `app.enable_llm_audit=false`，也会运行组中声明的 `layout_prop_boundary_review`。

| Node group | 包含节点 | 前置要求 |
| --- | --- | --- |
| `key_vision` | `script_worldview_extract` → `key_vision_prompt` → `key_vision_image_generation` → `key_vision_image_audit` | 无额外组前置 |
| `key_vision_edit` | `key_vision_edit` → `key_vision_image_audit` | `key_vision` 完成 |
| `role_extract` | `role_extract_primary` → `role_extract_functional` → `role_finalize` | `script_novel_extract` 输出已完成 |
| `prop_layout_extract` | `prop_extract` → `prop_finalize` → `layout_extract` → `layout_finalize` → `layout_prop_boundary_review` | `script_novel_extract` 输出已完成 |
| `roleboard_gen` | `roleboard_prompt` → `roleboard_image_generation` → `roleboard_image_audit` | `role_extract` 完成；图像生成还需要已生成的 `key_vision` |
| `prop_gen` | `prop_prompt` → `prop_image_generation` → `prop_image_audit` | `prop_layout_extract` 完成 |
| `layout_gen` | `layout_prompt` → `layout_image_generation` → `layout_image_audit` | `prop_layout_extract` 完成 |

对应的命令形式如下：

```powershell
run\start.cmd --pregen --config saodi.yaml --node-group role_extract --force
run\start.cmd --pregen --config saodi.yaml --node-group prop_layout_extract --force
run\start.cmd --pregen --config saodi.yaml --node-group roleboard_gen --force
run\start.cmd --pregen --config saodi.yaml --node-group prop_gen --force
run\start.cmd --pregen --config saodi.yaml --node-group layout_gen --force
```

`role_extract` 与 `prop_layout_extract` 只共享剧本抽取结果，前置满足后可以并行启动；`roleboard_gen`、`prop_gen` 与 `layout_gen` 分别依赖对应前置组，满足各自前置后可以并行启动。单独运行这些组内任一节点时，使用同样的组前置校验。

### 图像审计修复

`roleboard_gen`、`prop_gen` 和 `layout_gen` 的审计节点在不通过时，会把本轮 `issues`/审计说明整理为下一轮图像提示词中的明确修复约束，并只重生成失败资产。每个资产最多重试 2 次（初次生成 + 2 次修复尝试）；达到上限仍不通过时任务失败，不强制接收不合格图片。

## 默认链路之外的可选节点

这些节点仍然可以通过 `pregen --only <node>` 执行，但不应插入默认链路：

- `script_outline`：`script_import` 的替代脚本入口。
- `script_novel`：从手动大纲生成连续剧情文本；不会替代或自动跳过 `script_cinematic_adapt`。
- `role_subject_frontal_image_generation`：生成角色正面主体参考图。
- `role_subject_frontal_image_audit`：审核角色正面主体参考图，应在对应生成节点之后执行。
- `role_kling_voice_generation`：为角色生成 Kling 相关声音资产。
- `role_subject_video_generation`：生成角色主体视频参考资产。
- `role_subject_element_generation`：生成角色主体元素/可复用主体资产。
- `role_voice_select`：从声音目录中显式选择角色音色。
- `bgm_design`：规划背景音乐。
- `bgm_generation`：生成背景音乐；需要先有 `bgm_design` 输出。

可选节点的使用场景：

- 使用 `script_outline`/`script_novel` 时，是有意采用手动的“大纲 → 剧情文本”创作路径；需要进入默认链路时，仍应继续运行 `script_cinematic_adapt`；
- 角色正面图、主体视频、主体元素和角色音色节点只在下游 provider 或项目确实需要这些参考资产时运行；
- `bgm_design` 与 `bgm_generation` 只在项目需要生成 BGM 时运行。

## 依赖和局部重跑规则

- `roleboard_image_generation` 依赖 `key_vision_image_generation`；角色板图生成前必须先完成主视觉图及其审核。
- `roleboard_prompt`、`roleboard_image_generation`、`roleboard_image_audit` 需要 `role_extract` 组完成；
- `prop_prompt`、`prop_image_generation`、`prop_image_audit`、`layout_prompt`、`layout_image_generation`、`layout_image_audit` 需要 `prop_layout_extract` 组完成。
- `prop_*`、`layout_*` 静态资产节点必须在 `clip_to_shots` 之前完成；shot 规划会引用这些结构和资产。
- `clip_segment` 的场景关系是 clip 到 scene 的多对一关系：每个 clip 只有一个 `scene_id`，一个 scene 可以服务多个 clip。
- `shot_background_shot_reference` 必须先于 `shot_background_image_generation`；后者同时以场景空间母版和当前 shot 的机位示意图作为参考图。
- `shot_background_image_generation` 必须先于 `shot_keyframe_prompt` 和 `shot_keyframe_image_generation`；关键帧以背景图作为第一张参考图。
- `shot_manifest_generation` 是默认 pregen 终点；完成后才能进入 `shot_dialogue_audio_generation`、`shot_video_generation` 等 Generation 节点。
- 每个 shot 必须有唯一、非空的 `narrative_angle`、结构化人物站位和结构化相机合同，并且与一张机位示意图和一张背景图一一对应。机位示意图、背景图与关键帧图分别写入 `assets/images/shot_background_references/`、`assets/images/shot_backgrounds/` 和 `assets/images/shot_keyframes/`。

`--episodes` 可用于支持 episode scope 的单节点重跑。`--shots` 仅用于背景提示词、机位示意图、背景、关键帧和 manifest 节点。强制重建场景母版或机位示意图会使对应下游背景、关键帧和 manifest 失效。

完整运行示例：

```powershell
run\start.cmd --config config.yaml --project <project_id> --until shot_manifest_generation
```

局部重跑示例：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only layout_to_background_prompt --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only shot_background_shot_reference --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only shot_background_image_generation --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only shot_keyframe_image_generation --episodes 1 --shots 1-3 --force
```
