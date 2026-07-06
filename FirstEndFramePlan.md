# 首尾帧与 Clip 视频生成代码规划

## 目标

本规划用于调整当前故事板和视频生成流程，使系统从“过于频繁切镜”的 1 秒 1 镜头倾向，转为以 `clip` 为基本视频生成单元，并通过首尾帧稳定相邻 clip 的衔接。

核心目标：

- `clip_segment` 负责从 episode 文本中切出剧情片段，每个 clip 建议 8 到 15 秒。
- episode 的总时长只作为文本切分的节奏参考，不再作为 clip 数量的硬性校验依据。
- `clip_storyboard_image_generation` 之后新增首尾帧生成节点。
- 每个 clip 仍然生成十二宫格 storyboard，但十二宫格不是 1 秒 1 格。
- clip 内部可以包含多个真实镜头切换，统一称为 `Camera Shot`。
- 首个 clip 使用自己的首帧和尾帧生成视频。
- 非首个 clip 使用上一个 clip 的尾帧作为起始帧，使用自己的尾帧作为结束帧，并在开头立刻硬切到当前 clip 的第一宫格内容。

## 一、术语合同

需要先统一代码、schema、prompt 和文档中的概念。

| 名称 | 含义 |
| --- | --- |
| `episode` | 一集文本内容 |
| `clip_segment` | 从 episode 文本切出来的视频片段，建议 8 到 15 秒 |
| `clip` | storyboard 和 video 的基本生成单元，对应一个 `clip_segment` |
| `Camera Shot` | clip 内部真实镜头切换，一个 clip 内可包含 1 到 4 个 Camera Shot |
| `P01` 到 `P12` | 每个 clip 的十二宫格 storyboard 面板，不等于 12 秒 |
| `start_frame` | clip 的起始关键帧 |
| `end_frame` | clip 的结尾关键帧 |
| `hard cut` | 非首个 clip 开头从上一个 clip 的尾帧立刻硬切到当前 clip 的第一宫格内容 |

建议继续保留旧字段兼容：

- 旧输入中的 `shots` 仍可读入为 `clips`。
- 旧输入中的 `shot_id` 仍可读入为 `clip_id`。
- 新生成的数据统一输出 `clips` 和 `clip_id`。
- 当前 CLI 和配置中历史节点名 `shot_video_generation` 可以暂时保留，内部语义改为 clip video generation。

## 二、调整 `clip_segment`

涉及文件：

- `autodrama/src/autodrama/workflows/nodes/script_nodes.py`
- `autodrama/src/autodrama/services/script_service.py`
- `autodrama/src/autodrama/prompts/clip_segment.md`

### 代码调整

`ClipSegmentNode` 当前会根据 episode 秒数和 12 到 15 秒的范围计算理论 clip 数量，并在数量不匹配时抛出异常。这个逻辑需要改掉。

调整方向：

- `MIN_CLIP_SECONDS` 改为 `8`。
- `MAX_CLIP_SECONDS` 保持或明确为 `15`。
- 删除基于 `episode_duration_seconds` 推导 clip 数量并强制报错的逻辑。
- `validate_clip_segments()` 只做结构校验，不做整集时长约束。

保留的结构校验：

- episode key 存在。
- clip key 可排序。
- 每个 clip 文本非空。
- episode 下至少有一个 clip。
- clip id 不重复。

可以保留 warning 级别的提示，但不要中断流程：

- clip 数量明显过多。
- 单个 clip 文本明显过短。
- 单个 clip 文本明显过长。

### Prompt 调整

`clip_segment.md` 需要改成：

- 每个 clip 建议 8 到 15 秒。
- `episode_duration_seconds` 是节奏参考，不是硬性总时长。
- 不要为了满足总秒数机械计算 clip 数量。
- 以剧情动作、情绪变化、空间变化和叙事转折作为切分依据。
- 后续 storyboard 会为每个 clip 生成十二宫格，clip 内部真实镜头切换称为 `Camera Shot`。

`script_service.clip_segment()` 建议向 prompt 传入：

- `episode_duration_seconds`
- `min_clip_seconds`
- `max_clip_seconds`
- `duration_reference_note`

## 三、调整 `clip_storyboard_prompt`

涉及文件：

- `autodrama/src/autodrama/prompts/clip_storyboard_prompt.md`
- `autodrama/src/autodrama/workflows/nodes/storyboard_asset_nodes.py`
- `autodrama/src/autodrama/core/schemas.py`

### 输入输出关系

`clip_storyboard_prompt` 应严格跟随 `clip_segment`。

规则：

- 输入有多少个 `clip_segment`，输出就有多少个 storyboard `clip`。
- 不在 storyboard 阶段重新根据 episode 秒数推导目标 clip 数。
- 不再因为 clip 总数超出某个按秒数计算的范围而报错。

每个 storyboard clip 建议包含：

- `clip_id`
- `clip_title`
- `clip_duration_hint`
- `clip_text`
- `camera_shots`
- `panel_plan`
- `video_prompt`
- `negative_prompt`

### 十二宫格规则

每个 clip 固定规划 P01 到 P12：

- P01 到 P12 是视觉节奏帧，不是每秒一帧。
- 一个 `Camera Shot` 应覆盖多个宫格。
- clip 内部如果存在 Camera Shot 边界，需要在对应宫格之后或两个宫格之间设计明显红色斜杠。
- 红色斜杠只出现在 storyboard 十二宫格中，用来表达真实切镜痕迹。
- 红色斜杠不应该进入后续生成的首尾关键帧。

### 校验调整

`validate_clip_storyboard_prompt_output()` 建议校验：

- 输出 clip 数量等于输入 `clip_segments` 数量。
- 每个 `clip_id` 能对应输入 segment。
- 每个 clip 都有完整 P01 到 P12 规划。
- 每个 clip 至少有一个 `Camera Shot`。
- 推荐每个 clip 包含 2 到 4 个 `Camera Shot`，但不要用硬错误阻断所有异常情况。

## 四、新增 `clip_storyboard_keyframe_generation` 节点

推荐新增节点名：

```text
clip_storyboard_keyframe_generation
```

原因：

- 它紧跟 `clip_storyboard_image_generation`。
- 它的输入依据是十二宫格 storyboard。
- 它的职责是从 storyboard 中提取视频首尾关键帧。

新流程顺序：

```text
clip_storyboard_prompt
-> clip_storyboard_image_generation
-> clip_storyboard_keyframe_generation
-> shot_manifest_generation
-> shot_video_generation
```

涉及文件：

- `autodrama/src/autodrama/workflows/nodes/storyboard_asset_nodes.py`
- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/prompts/storyboard_keyframe/toapi_gpt_image_2.md`
- `config.yaml.example`
- 项目实际配置文件，例如 `huyao.yaml`
- `model_catalog.yaml.example`

### 生成规则

按 episode 内 clip 顺序处理：

- 第一个 clip 生成两张图：
  - `start_frame`，来源为 P01。
  - `end_frame`，来源为 P12。
- 非第一个 clip 只生成一张图：
  - `end_frame`，来源为 P12。

所有图片生成任务需要并行执行，并发数从配置文件读取。

建议配置：

```yaml
clip_storyboard_keyframe_generation:
  provider: ...
  model: ...
  prompt_template: toapi_gpt_image_2
  concurrency: 4
  output_dir: assets/storyboard_keyframes
```

### Schema 设计

建议新增：

- `StoryboardKeyframeGenerationOutput`
- `StoryboardKeyframeGenerationItem`

`StoryboardKeyframeGenerationItem` 建议字段：

| 字段 | 含义 |
| --- | --- |
| `episode_key` | 所属 episode |
| `clip_id` | 所属 clip |
| `frame_role` | `start` 或 `end` |
| `panel_ref` | `P01` 或 `P12` |
| `source_storyboard_asset_id` | 来源 storyboard 图 |
| `prompt` | 实际生图 prompt |
| `asset_id` | 关键帧资产 ID |
| `asset_path` | 本地图片路径 |
| `asset_url` | 远端图片 URL |
| `provider` | 生图 provider |
| `model` | 生图模型 |
| `request` | 请求信息 |
| `response` | 原始响应 |
| `usage` | token 或计费信息 |

### Prompt 设计

关键帧生成 prompt 应强调：

- 这是视频首尾关键帧，不是角色板，不是拼贴图，不是 storyboard 图。
- `start_frame` 必须忠实于当前 clip 的 P01。
- `end_frame` 必须忠实于当前 clip 的 P12。
- 画面应是单张干净电影画面。
- 保持角色身份、服装、空间、光线、镜头焦段和 storyboard 一致。
- 禁止水印、字幕、分镜格、红色斜杠、UI 标记。

## 五、调整 `shot_manifest_generation`

涉及文件：

- `autodrama/src/autodrama/workflows/nodes/storyboard_asset_nodes.py`
- `autodrama/src/autodrama/core/schemas.py`

`shot_manifest_generation` 需要依赖新增的 `clip_storyboard_keyframe_generation` 输出。

### 首个 clip 的输入

首个 clip 使用自己的首尾帧：

```text
clip_start_frame = 当前 clip 的 start_frame
clip_end_frame = 当前 clip 的 end_frame
storyboard = 当前 clip 的十二宫格 storyboard
roleboard / layout / prop = 其他参考资产
```

### 非首个 clip 的输入

非首个 clip 使用上一个 clip 的尾帧作为起点：

```text
clip_start_frame = 上一个 clip 的 end_frame
clip_end_frame = 当前 clip 的 end_frame
storyboard = 当前 clip 的十二宫格 storyboard
roleboard / layout / prop = 其他参考资产
```

### `ShotVideoInput` 调整

建议扩展 `ShotVideoInput.asset_type`：

```text
clip_start_frame
clip_end_frame
storyboard
roleboard
layout
prop
```

视频参考图顺序建议固定为：

```text
image_1 = clip_start_frame
image_2 = clip_end_frame
image_3 = storyboard
image_4+ = roleboard / layout / prop
```

首尾帧优先级最高，不能被 storyboard、roleboard、layout 或 prop 挤掉。

### Manifest 字段建议

建议在 manifest 或 clip 记录中显式保存：

| 字段 | 含义 |
| --- | --- |
| `start_frame_asset_id` | 当前 clip 实际使用的首帧 |
| `start_frame_source_clip_id` | 首帧来源 clip，首个 clip 为自己，后续 clip 为上一 clip |
| `end_frame_asset_id` | 当前 clip 自己的尾帧 |
| `is_first_clip` | 是否 episode 首个 clip |
| `requires_initial_hard_cut` | 非首个 clip 为 true |

这样方便排查视频衔接、资源引用和 prompt 行为。

## 六、调整 `shot_video_generation` prompt

涉及文件：

- `autodrama/src/autodrama/prompts/shot_video/default.md`
- `autodrama/src/autodrama/prompts/shot_video/kling.md`
- `autodrama/src/autodrama/prompts/shot_video/volcengine.md`
- `autodrama/src/autodrama/workflows/nodes/storyboard_asset_nodes.py`
- `autodrama/src/autodrama/workflows/dynamic_assets.py`
- `autodrama/src/autodrama/workflows/generation.py`

### 首个 clip

首个 clip 的视频 prompt 需要表达：

```text
使用 image_1 作为当前 clip 的起始画面。
使用 image_2 作为当前 clip 的结束画面。
按照 image_3 的十二宫格 storyboard 执行 clip 内部运动和 Camera Shot 切换。
视频应自然从首帧发展到尾帧。
```

### 非首个 clip

非首个 clip 的视频 prompt 需要表达：

```text
image_1 是上一条 clip 的尾帧，只用于当前视频开头的连续性。
视频必须从 image_1 开始。
开头必须立刻硬切到当前 clip 的 P01 / 第一宫格内容。
硬切后，严格按照当前 clip 的十二宫格 storyboard 和 Camera Shot 计划推进。
最终收束到 image_2 当前 clip 尾帧。
```

这里的关键点是：

- 非首个 clip 不是从上一 clip 尾帧丝滑过渡到当前内容。
- 它需要先用上一 clip 尾帧保证拼接点稳定。
- 然后立刻硬切到当前 clip 的第一宫格内容。
- 后续再按照当前 clip 的 storyboard 推进。

## 七、视频 Provider 适配

涉及文件需要根据现有 provider 实现确认，大概率包括：

- `autodrama/src/autodrama/workflows/generation.py`
- `autodrama/src/autodrama/workflows/dynamic_assets.py`
- 视频 provider 相关 service 文件

适配原则：

- 如果 provider 支持原生首帧和尾帧字段：
  - `clip_start_frame` 映射到 first frame 或 start image。
  - `clip_end_frame` 映射到 last frame 或 end image。
  - storyboard 和其他资产作为 reference images。
- 如果 provider 不支持原生尾帧：
  - 仍将 `clip_start_frame` 和 `clip_end_frame` 作为前两张 reference images。
  - 通过 prompt 强化首尾帧约束。
  - manifest 中保留首尾帧元数据，便于后续 provider 升级后直接映射。

需要更新当前视频输入顺序校验。

旧顺序大概率是：

```text
storyboard -> roleboard -> layout -> prop
```

新顺序应该是：

```text
clip_start_frame -> clip_end_frame -> storyboard -> roleboard -> layout -> prop
```

如果 provider 有 `max_reference_images` 限制，裁剪优先级建议为：

```text
start_frame
end_frame
storyboard
roleboard
layout
prop
```

不能因为参考图数量限制丢掉首尾帧。

## 八、配置和文档

需要更新：

- `config.yaml.example`
- 当前项目使用的配置文件，例如 `huyao.yaml`
- `model_catalog.yaml.example`
- README 或 workflow 文档

文档中应说明：

- `clip_segment` 的 8 到 15 秒是建议范围。
- episode 时长只用于文本切分参考。
- storyboard 十二宫格不是 1 秒 1 格。
- 红色斜杠表示 clip 内部真实切镜。
- `clip_storyboard_keyframe_generation` 必须在 `shot_manifest_generation` 前运行。
- 非首个 clip 的首帧来自上一 clip 的尾帧。

## 九、兼容和失败策略

### 数据兼容

保留旧字段读取：

- `shots` alias 到 `clips`。
- `shot_id` alias 到 `clip_id`。
- `shot_count` alias 到 `clip_count`。
- `shot_path` alias 到 `clip_path`。

新输出统一写：

- `clips`
- `clip_id`
- `clip_count`
- `clip_path`

### 流程失败策略

新流程中，`shot_manifest_generation` 应要求存在 `clip_storyboard_keyframe_generation` 输出。

如果缺失，应明确报错：

```text
clip_storyboard_keyframe_generation output is missing; run pregen through clip_storyboard_keyframe_generation first
```

不建议静默退回旧逻辑，否则很难判断视频为什么没有首尾帧连续性。

## 十、验证规划

根据仓库规则，不使用 pytest。

### 编译检查

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
```

### Smoke 检查

建议新增或更新以下 smoke 文件：

| 文件 | 验证内容 |
| --- | --- |
| `scripts/smoke/clip_segment_contract_smoke.py` | 8 到 15 秒提示生效，且不再按 episode 秒数强制 clip 数 |
| `scripts/smoke/storyboard_clip_schema_smoke.py` | `clips` / `clip_id` schema 兼容旧 `shots` |
| `scripts/smoke/clip_clip_storyboard_keyframe_generation_contract_smoke.py` | 首 clip 生成 start 和 end，后续 clip 只生成 end |
| `scripts/smoke/shot_video_manifest_contract_smoke.py` | 视频输入顺序为 start、end、storyboard、roleboard 等 |
| `scripts/smoke/non_first_clip_hard_cut_prompt_smoke.py` | 非首 clip prompt 包含从上一尾帧开始并立刻硬切到当前 P01 的约束 |

### 端到端 dry run

建议用一个小 episode 先跑到 manifest，不必第一步就真实跑视频：

```text
clip_segment
-> clip_storyboard_prompt
-> clip_storyboard_image_generation
-> clip_storyboard_keyframe_generation
-> shot_manifest_generation
```

检查重点：

- clip 数量是否跟随 `clip_segment`。
- 每个 clip 是否有十二宫格 storyboard。
- 首个 clip 是否有 start 和 end 两张关键帧。
- 非首个 clip 是否只有自己的 end 关键帧。
- manifest 中非首个 clip 的 start frame 是否来自上一 clip 的 end frame。
- 非首个 clip 的视频 prompt 是否包含硬切要求。

## 十一、推荐实施顺序

1. 修改 `clip_segment` 校验，先解决当前 clip 数量报错。
2. 修改 `clip_segment` prompt，把 12 到 15 秒改为 8 到 15 秒，并明确 episode 时长只是参考。
3. 修改 `clip_storyboard_prompt`，让 storyboard 严格跟随 `clip_segment`，不再重新计算 clip 数。
4. 新增 `clip_storyboard_keyframe_generation` schema、prompt、node 和配置。
5. 修改 `shot_manifest_generation`，把首尾帧接入 `shot_video_inputs`。
6. 修改 `shot_video_generation` prompt，区分首个 clip 和非首个 clip。
7. 修改 provider 输入映射和 reference image 顺序校验。
8. 补充 smoke 验证和文档。

这个实施顺序可以把风险拆开：先解决文本切分和 clip 数量，再稳定 storyboard 合同，然后引入首尾帧，最后接入视频生成和 provider 适配。
