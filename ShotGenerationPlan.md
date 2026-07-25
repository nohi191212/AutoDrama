# Shot-first 生成链路重构实施文档

## 1. 文档目标

本文档用于把当前以 `clip` 为视频生成单位、依赖十二宫格 storyboard 的链路，重构为：

- `clip` 只表示一段约 1 到 2 分钟的连续剧情；
- `shot` 表示一次 3 到 15 秒的视频生成任务；
- `shot` 拥有独立首帧关键帧；
- 关键帧通过场景三视图、角色板和道具图进行两阶段图生图；
- 视频模型主要接收已经融合好空间和人物信息的首帧关键帧；
- 可灵默认启用 `multi_shot=true`；
- 旧 clip storyboard 链路退出默认流程，但保留手动执行能力。

本文档只描述实施方案，不包含本次代码实现。

## 2. 核心设计结论

### 2.1 新术语合同

| 名称 | 新含义 |
| --- | --- |
| `episode` | 一集完整剧本文本 |
| `clip_segment` | 从 episode 中切出的长剧情段落，建议 60 到 120 秒 |
| `clip` | 剧情组织容器，不直接对应一次生图或视频任务 |
| `shot` | 一次 3 到 15 秒的视频生成任务，拥有一张首帧关键帧 |
| 单镜头 `video_prompt` | 一个连续摄影镜头的运动和表演描述 |
| 多镜头 `video_prompt` | 一个视频任务内包含多个编号镜头，由可灵 `multi_shot` 执行 |
| 场景三视图 | 同一场景、同一空间结构下的三个一致视角，是场景母版 |
| 当前镜头背景图 | 从场景三视图投射出的单画面、无人背景 |
| shot 关键帧 | 当前镜头背景图与角色板、道具图融合得到的 shot 首帧 |

新的 `shot` 是 provider 任务单位，不严格等同于传统电影术语中的“一镜到底”。一个 `shot.video_prompt` 可以是单镜头，也可以包含多个内部镜头。

### 2.2 目标默认链路

默认 pregen 链路调整为：

```text
script_import
script_detail_expand
script_novel_extract
key_vision_prompt
key_vision_image_generation
role_extract_primary
role_extract_functional
role_finalize
roleboard_prompt
roleboard_image_generation
role_subject_frontal_image_generation
prop_extract
prop_finalize
layout_extract
layout_finalize
layout_prop_boundary_review
prop_prompt
layout_prompt
prop_image_generation
layout_image_generation
role_kling_voice_generation
role_subject_element_generation
clip_segment
clip_to_shots
shot_keyframe_prompt
shot_keyframe_image_generation
shot_manifest_generation
```

默认 generation 链路调整为：

```text
shot_dialogue_audio_generation
shot_video_generation
dynamic_asset_solidification
```

以下旧节点保留，但从默认 pregen 链路移除：

```text
clip_prompt
clip_storyboard_prompt
clip_storyboard_prompt_audit
clip_storyboard_image_generation
clip_storyboard_keyframe_generation
clip_manifest_generation
```

旧节点仍可通过 `pregen --only <node>` 手动执行。

### 2.3 目标数据流

```text
episode 文本
  -> clip_segment：60-120 秒剧情段落
  -> clip_to_shots：每段生成多个 3-15 秒 shot
  -> shot_keyframe_prompt：拼接风格和负面词
  -> shot_keyframe_image_generation：
       1. 场景三视图 -> 当前 shot 无人背景
       2. 当前背景 + 角色板 + 道具图 -> shot 首帧
  -> shot_manifest_generation
  -> shot_video_generation
```

## 3. 数据契约

### 3.1 `clip_segment`

保持现有 clip 数据结构：

```json
{
  "1": {
    "text": "该长剧情段落的完整剧本文本",
    "role_names": ["角色甲", "角色乙"],
    "prop_names": ["雪地摩托"],
    "layout_names": ["雪原"]
  }
}
```

调整内容：

- `MIN_CLIP_SECONDS` 从 `8` 改为 `60`；
- `MAX_CLIP_SECONDS` 从 `15` 改为 `120`；
- episode 总时长仍然只作为节奏参考；
- 不根据 episode 时长硬算 clip 数量；
- 不在半句对白、未完成动作或未完成情绪阶段中切断；
- 时间跳跃、重大空间变化、叙事阶段变化时优先切 clip；
- clip 可以略短或略长，但必须保持完整叙事阶段。

结构校验继续保留：

- 每集至少一个 clip；
- key 必须从 `"1"` 连续递增；
- `text` 非空；
- 角色、道具和场景名称必须是字符串数组；
- 不允许额外字段。

文本长度只记 warning，不作为硬错误。原来的短文本和长文本 warning 阈值需要按 60 到 120 秒重新调整。

### 3.2 `clip_to_shots` 模型输出

模型只输出与内容规划直接有关的字段：

```json
{
  "clip_1_shot_1": {
    "keyframe_prompt": "Low-angle medium-wide opening frame from behind the rider. The rider is centered in the foreground, facing the distant forest. The snowmobile points toward the center background...",
    "ref_ids": [
      "layout_snowfield_base",
      "role_rider_appearance_base_roleboard",
      "prop_snowmobile_asset_base"
    ],
    "video_prompt": "镜头1-后方低角度跟拍，骑手驾驶雪地摩托向前驶去\n镜头2-侧方低角度近景，特写高速转动的车轮和飞溅的雪",
    "duration_seconds": 12,
    "dialogue": []
  }
}
```

推荐 schema：

```python
class ClipToShotsModelItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyframe_prompt: str
    ref_ids: list[str] = Field(default_factory=list)
    video_prompt: str
    duration_seconds: int = Field(
        ge=3,
        le=15,
        validation_alias=AliasChoices("duration_seconds", "duration"),
    )
    dialogue: list[str] = Field(default_factory=list)


class ClipToShotsModelOutput(RootModel[dict[str, ClipToShotsModelItem]]):
    pass
```

`dialogue` 是在用户给出的四个字段之外保留的唯一建议字段，原因是现有 `shot_dialogue_audio_generation` 和可灵角色音色绑定需要可靠的逐字对白及说话人。不要从 `video_prompt` 反向解析对白。

约束：

- JSON 中时长使用整数秒，例如 `3`，不使用非法值 `3s`；
- `duration_seconds` 必须在 3 到 15 之间；
- `keyframe_prompt` 使用纯英文；
- `keyframe_prompt` 只描述动作开始前的首帧状态；
- `keyframe_prompt` 必须明确摄影机角度、景别、人物站位、前中后景、朝向、视线和遮挡；
- `keyframe_prompt` 不写完整动作过程，不写视频运镜，不写风格词和负面词；
- `video_prompt` 可以是单镜头，也可以是连续编号的多镜头；
- 多镜头格式固定为每行 `镜头N-内容`；
- `ref_ids` 只能引用输入资产索引里提供的真实 ID；
- `ref_ids` 不包含路径、URL、文件名或 provider 信息；
- 有实体场景的 shot 必须且只能选一个主 layout；
- 场景变化必须拆成不同 shot；
- 同一 shot 里出现的所有重要人物和可见关键道具都必须进入 `ref_ids`；
- `dialogue` 必须逐字来自剧本，格式为 `角色名：对白`。

#### ID 生成

模型可以按当前 clip 输出局部 key，例如：

```text
clip_1_shot_1
clip_1_shot_2
```

节点代码负责生成全局稳定 ID：

```text
episode_001_clip_001_shot_001
episode_001_clip_001_shot_002
```

不要让模型生成 episode、项目或路径信息。代码根据当前 episode 和 clip 位置完成 canonical ID。

### 3.3 节点级 shot 输出

推荐增加持久化结构：

```python
class ShotPlanItem(BaseModel):
    shot_id: str
    clip_id: str
    clip_index: int
    shot_index_in_clip: int
    episode_shot_index: int
    keyframe_prompt: str
    ref_ids: list[str]
    video_prompt: str
    duration_seconds: int
    dialogue: list[str] = Field(default_factory=list)


class ClipShotPlan(BaseModel):
    clip_id: str
    clip_index: int
    shots: list[ShotPlanItem]


class ClipToShotsEpisodeOutput(BaseModel):
    episode_key: str
    clips: list[ClipShotPlan]


class ClipToShotsOutput(BaseModel):
    episodes: list[ClipToShotsEpisodeOutput]
```

LLM 输出和持久化输出分开：

- LLM 输出保持最小；
- 代码生成 ID、顺序和 episode/clip 关系；
- 后续节点只读取规范化后的 `ClipToShotsOutput`。

### 3.4 `shot_keyframe_prompt` 输出

该节点不调用 LLM，只做确定性渲染：

```python
class ShotKeyframePromptItem(BaseModel):
    episode_key: str
    clip_id: str
    shot_id: str
    ref_ids: list[str]
    raw_keyframe_prompt: str
    final_keyframe_prompt: str
    negative_prompt: str | None = None


class ShotKeyframePromptOutput(BaseModel):
    prompts: list[ShotKeyframePromptItem]
```

最终 prompt 只包含三部分：

```text
<raw_keyframe_prompt>

<visual style>

Avoid: <negative constraints>
```

不添加：

- episode key；
- clip/shot ID；
- 项目名；
- 节点名；
- 路径或 URL；
- provider/model 名称；
- 工作流说明；
- JSON 字段解释。

### 3.5 `shot_keyframe_image_generation` 输出

每个 shot 最多产生两个图像资产：

```python
class ShotKeyframeImageGenerationItem(BaseModel):
    episode_key: str
    clip_id: str
    shot_id: str
    ref_ids: list[str]
    background_prompt: str
    background_asset_id: str
    background_asset_path: str | None = None
    background_asset_url: str | None = None
    keyframe_prompt: str
    keyframe_asset_id: str
    keyframe_asset_path: str | None = None
    keyframe_asset_url: str | None = None
    provider: str
    model: str
    request_id: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    raw_response: dict[str, Any] = Field(default_factory=dict)


class ShotKeyframeImageGenerationOutput(BaseModel):
    generated_keyframes: list[ShotKeyframeImageGenerationItem]
```

建议落盘路径：

```text
assets/images/shot_backgrounds/<shot_id>.png
assets/images/shot_keyframes/<shot_id>.png
```

节点结果按 episode 独立保存：

```text
assets/json/nodes/shot_keyframe_image_generation/<episode_key>.json
```

### 3.6 `shot_manifest_generation` 输出

新的动态生成 manifest 继续使用稳定路径：

```text
shots/<episode_key>.json
```

同时保存节点级 provenance：

```text
assets/json/nodes/shot_manifest_generation/<episode_key>.json
```

推荐顶层结构：

```json
{
  "schema_version": 2,
  "manifest_kind": "shot",
  "episode_key": "episode_001",
  "shots": [
    {
      "shot_id": "episode_001_clip_001_shot_001",
      "index": 1,
      "clip_id": "episode_001_clip_001",
      "clip_index": 1,
      "shot_index_in_clip": 1,
      "duration_seconds": 12,
      "ref_ids": [
        "layout_snowfield_base",
        "role_rider_appearance_base_roleboard",
        "prop_snowmobile_asset_base"
      ],
      "role_ids": ["role_rider"],
      "role_appearance_ids": ["role_rider_appearance_base"],
      "layout_ids": ["layout_snowfield_base"],
      "prop_ids": ["prop_snowmobile"],
      "dialogue": [],
      "keyframe_prompt": "final prompt",
      "video_prompt": "镜头1-...",
      "final_video_prompt": "镜头1-...",
      "background_asset_id": "episode_001_clip_001_shot_001_background",
      "background_asset_path": "assets/images/shot_backgrounds/episode_001_clip_001_shot_001.png",
      "start_frame_asset_id": "episode_001_clip_001_shot_001_keyframe",
      "start_frame_asset_path": "assets/images/shot_keyframes/episode_001_clip_001_shot_001.png",
      "video_inputs": [
        {
          "slot": "image_1",
          "asset_type": "shot_start_frame",
          "source_node": "shot_keyframe_image_generation",
          "required": true
        }
      ]
    }
  ]
}
```

视频输入默认只包含最终首帧关键帧。角色主体 element 仍由 generation 阶段按 `role_ids` 动态追加，不把角色板、场景三视图或道具图直接重复提交给视频模型。

## 4. 角色板锚点实现

### 4.1 现状

当前代码支持：

- `generation.roleboard_style_reference_dir`；
- 手工风格参考图；
- 主视觉图作为世界观和风格参考；
- 同一角色 variant 参考自己的 base 角色板。

当前代码不支持：

- 自动生成项目级锚点角色板；
- 后续所有角色板自动参考该锚点；
- 锚点先生成、其他 base 后生成的依赖顺序。

### 4.2 锚点选择规则

新增可选节点参数：

```yaml
nodes:
  roleboard_image_generation:
    params:
      anchor_role: ""
      anchor_appearance: "base"
```

选择顺序：

1. 如果配置了 `anchor_role`，按 role ID、角色名或别名匹配；
2. 在该角色中优先选择配置的 `anchor_appearance`；
3. 未配置时，选择 `role_tier="primary"` 的第一个 base appearance；
4. 如果没有 primary，选择第一个拥有 base appearance 的角色；
5. 无可用 base appearance 时直接报错。

选择必须基于 `role_finalize` 的稳定顺序，不能依赖文件系统枚举顺序。

### 4.3 参考图顺序

锚点角色板：

```text
[手工风格参考图，可选] + [主视觉图]
```

其他 base 角色板：

```text
[锚点角色板]
```

同角色 variant：

```text
[锚点角色板] + [该角色 base 角色板]
```

后续角色板默认不再同时提交锚点和主视觉，避免两个强风格参考竞争。锚点已经吸收主视觉的世界观和摄影风格。

### 4.4 生成顺序

`roleboard_image_generation` 分成三个 stage：

1. `anchor`：严格串行，只生成或加载锚点；
2. `base`：除锚点外的 base，可按配置并发；
3. `variant`：等所有依赖 base 完成后并发。

生成锚点后写入 state metadata：

```json
{
  "roleboard_anchor": {
    "role_id": "role_xxx",
    "appearance_id": "role_xxx_appearance_base",
    "asset_id": "role_xxx_appearance_base_roleboard",
    "asset_path": "assets/images/roles/...",
    "asset_url": null
  }
}
```

### 4.5 缓存与 `--force`

- 非 force：已有有效锚点直接复用；
- 全量 force：先重生成锚点，再重生成所有后续角色板；
- episode 局部 force：默认复用全局锚点，只重生成目标 episode 涉及的角色；
- 如果局部 force 恰好重生成锚点，需要记录 warning，提示其他角色板仍基于旧锚点；
- 推荐给生成结果记录锚点 asset fingerprint，后续可以判断角色板是否 stale。

### 4.6 角色板 prompt 精简

`roleboard_prompt` 的 LLM 任务只负责输出：

- 角色稳定外貌；
- base/variant 的变化；
- 极简角色板画面要求。

生图 prompt 只保留：

```text
Photorealistic cinematic character reference sheet.
<角色稳定外貌和服装>
Full-body front, side and back views, plus one face close-up.
Neutral standing pose, clean light background.
Match the supplied anchor image's rendering style, lighting and color treatment.
Avoid identity drift, duplicate bodies, extra limbs, text, logos and watermarks.
```

不再让模型生成复杂艺术书布局、角色 ID 块、中文标签和大段工作流约束。若产品确实需要角色名和视图标签，由代码在图片生成后叠加。

需要同步精简：

```text
autodrama/src/autodrama/prompts/roleboard_prompt.md
autodrama/src/autodrama/prompts/roleboard_prompt/default.md
autodrama/src/autodrama/prompts/roleboard_prompt/*.md
```

## 5. 场景三视图与真实感

### 5.1 场景资产目标

每个 base layout 生成一张真实摄影质感的场景三视图：

- 三个视角属于同一空间；
- 入口、墙面、地面、固定陈设和可行动线一致；
- 不出现人物；
- 不出现字幕、水印、logo 或可读招牌；
- 材质和光线符合物理逻辑；
- 不是概念草图、蓝图、线稿或 storyboard。

建议场景 prompt 输出为纯英文：

```text
Photorealistic cinematic location reference sheet showing three consistent views of the same empty snowfield outpost. Preserve identical terrain, building placement, entrances and fixed objects across all views. Natural winter daylight, physically plausible snow, wood and metal materials, realistic atmospheric depth. No people, no text, no logo, no watermark.
```

### 5.2 `layout_prompt`

保持 `LayoutPromptItem` 的结构不变：

- base：`prompt_type="text_to_image"`；
- variant：`prompt_type="image_edit"`。

新增硬校验：

- `prompt` 非空；
- image prompt 必须主要为英文；
- base prompt 必须表达 three consistent views；
- prompt 不得包含路径、ID、provider、model 或项目名；
- variant 必须引用自己的 base；
- variant 只描述 `state_delta`，不重建空间。

需要同步修改所有 provider-specific layout prompt：

```text
autodrama/src/autodrama/prompts/layout_prompt.md
autodrama/src/autodrama/prompts/layout_prompt_toapi_gpt_image_2.md
autodrama/src/autodrama/prompts/layout_prompt_rightcode_gpt_image_2.md
autodrama/src/autodrama/prompts/layout_prompt_volcengine_seedream.md
```

### 5.3 `layout_image_generation`

保留现有两阶段依赖：

1. base layout text-to-image；
2. variant layout 参考 base image-edit。

在 `Layout` schema 中增加兼容字段：

```python
reference_image_kind: Literal["single_view", "three_view"] = "single_view"
prompt_language: str | None = None
```

新生成结果写入：

```json
{
  "reference_image_kind": "three_view",
  "prompt_language": "en"
}
```

旧项目读取时默认 `reference_image_kind="single_view"`，不自动冒充三视图。进入新 shot 链路前，如果不是 `three_view`，应提示重跑 `layout_prompt` 和 `layout_image_generation`。

## 6. `clip_to_shots` 节点

### 6.1 文件组织

不要继续把新逻辑追加到现有大型 `storyboard_asset_nodes.py`。新增：

```text
autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py
autodrama/src/autodrama/prompts/clip_to_shots.md
```

在 `director_service.py` 中新增最小调用方法：

```python
async def clip_to_shots(
    state,
    provider,
    *,
    clip_id,
    clip_text,
    asset_index,
    previous_context,
    next_context,
) -> ClipToShotsModelOutput:
    ...
```

### 6.2 Prompt 输入

只传入完成任务必须的信息：

- 当前局部 `clip_id`；
- 当前 clip 完整文本；
- 上一个 clip 结尾的一小段连续性上下文；
- 下一个 clip 开头的一小段连续性上下文；
- 当前 clip 可用视觉资产索引。

资产索引格式：

```text
layout_snowfield_base: 空旷雪原，远处针叶林和低矮木屋
role_rider_appearance_base_roleboard: 黑色头盔、深色冬季骑行服的成年骑手
prop_snowmobile_asset_base: 黑红色雪地摩托
```

不要传：

- 项目标题；
- 项目 ID；
- episode 总数；
- 源文件路径；
- 输出路径；
- provider/model 名称；
- 节点名；
- 与当前 clip 无关的角色和场景全集。

### 6.3 Prompt 任务

`clip_to_shots.md` 保持简短，核心规则：

- 把当前 clip 完整切成有序 shot；
- 每个 shot 3 到 15 秒；
- 不遗漏剧情，不重复动作；
- 对白不跨 shot 生硬切断；
- 空间变化必须切 shot；
- 每个 shot 选择真实存在的 `ref_ids`；
- keyframe prompt 是英文首帧构图；
- video prompt 是中文动作和摄影描述；
- 多镜头使用 `镜头N-` 连续编号；
- 只输出 JSON。

### 6.4 调用粒度

- 每个 clip 一次 LLM 调用；
- 同一 episode 内按 clip 顺序执行，便于传递边界连续性；
- 不同 episode 可以并发；
- 60 到 120 秒 clip 通常会产生约 6 到 20 个 shot；
- 若模型输出超限，不自动截断，应按剧情段落在节点内部拆成两个子请求，再合并和重新编号。

### 6.5 结构校验

硬校验：

- 至少一个 shot；
- key 从 `clip_N_shot_1` 连续递增；
- canonical shot ID 不重复；
- `duration_seconds` 在 3 到 15；
- `keyframe_prompt` 和 `video_prompt` 非空；
- `ref_ids` 全部可以解析；
- 每个有实体空间的 shot 恰好一个 layout；
- `dialogue` 的说话人必须可解析为当前角色；
- 多镜头编号必须从 1 连续递增；
- 不能出现 storyboard、P01-P12、路径、URL 或模型参数。

warning：

- 3 到 5 秒 shot 使用多个内部镜头；
- 15 秒 shot 内部镜头数过多；
- keyframe prompt 包含明显的连续动作描述；
- keyframe prompt 缺少摄影角度或人物站位；
- clip 总 shot 时长明显异常；
- clip 中的重要角色或道具从所有 shot 中完全消失。

### 6.6 局部重跑

输出按 episode 保存：

```text
assets/json/nodes/clip_to_shots/<episode_key>.json
```

支持：

```text
--episodes 1
--clips 1-3
--force
```

只重跑选中 clip 时：

- 保留其他 clip 的 shot；
- 删除该 clip 旧 shot 的规范化记录；
- 重新生成 shot 后重新计算 episode 级连续 index；
- 旧 shot 对应的 keyframe 和视频不立即物理删除，但必须从 manifest 中移除并标记为 stale。

## 7. `shot_keyframe_prompt` 节点

### 7.1 节点性质

这是纯本地节点，不调用 LLM，不增加 text call budget。

输入：

- `clip_to_shots` 的 raw `keyframe_prompt`；
- `generation.visual_style_prompt`；
- director `visual_tone`；
- 配置中的 shot keyframe negative prompt。

新增可选配置：

```yaml
nodes:
  shot_keyframe_prompt:
    params:
      negative_prompt: >-
        extra people, duplicate bodies, identity drift, malformed hands,
        inconsistent scale, floating objects, text, subtitles, logo, watermark
```

风格拼接优先级：

1. `generation.visual_style_prompt`；
2. director `visual_tone`；
3. 节点 negative prompt。

相同内容去重，空值不输出。

### 7.2 输出与局部重跑

输出：

```text
assets/json/nodes/shot_keyframe_prompt/<episode_key>.json
```

支持：

```text
--episodes
--shots
--force
```

局部重跑时保留未选中的 shot prompt。

## 8. `shot_keyframe_image_generation` 节点

### 8.1 两阶段图生图

#### 阶段 A：生成当前 shot 背景图

输入参考：

```text
当前 shot 的 layout 三视图
```

本地生成极简英文 prompt：

```text
Create one photorealistic empty background plate from the supplied location reference.
Use the camera position, angle, framing and lighting described below.
Return one single cinematic frame, not a triptych or reference sheet.
No people, vehicles, movable props, text, logo or watermark.

<raw keyframe prompt>
```

虽然 raw keyframe prompt 中包含人物站位，前面的 empty background 指令要求本阶段只继承摄影角度、景别和空间构图。

#### 阶段 B：生成最终首帧

输入参考顺序：

```text
1. 当前 shot 背景图
2. 角色 appearance roleboard，按画面重要性排序
3. 关键道具图，按画面重要性排序
```

prompt 使用 `shot_keyframe_prompt.final_keyframe_prompt`，不再追加节点说明。

对于纯空镜：

- 阶段 A 的输出可直接作为最终关键帧；
- 不做无意义的第二次 image-edit；
- manifest 中 background 和 keyframe 可以引用同一个文件，但使用不同语义字段。

### 8.2 参考图限制

- image provider 必须支持 reference images；
- 背景图永远是阶段 B 的第一张参考；
- 不允许静默丢弃前景角色；
- 如果参考图超过 provider 限制，优先级为背景、前景角色、说话角色、关键道具、背景角色；
- 仍超限时直接报错，并要求 `clip_to_shots` 拆镜头，不默默省略人物；
- 不把场景三视图再次放进阶段 B，避免与已经投射好的背景竞争。

### 8.3 并发和恢复

- 同一 shot 的 A、B 两阶段严格串行；
- 不同 shot 可以按 image provider 并发限制并发；
- 阶段 A 成功、阶段 B 失败时，重跑应复用已有背景；
- 非 force 且目标文件有效时复用；
- prompt 或 refs fingerprint 变化时，缓存标记 stale；
- `--force --shots ...` 只重生成指定 shot。

### 8.4 Provider 路由

各 image provider 增加对以下 node name 的识别：

```text
shot_keyframe_image_generation
```

模型选择优先级：

```text
models.shot_keyframe
models.storyboard
models.layout
provider 默认图片模型
```

阶段通过 metadata 区分：

```json
{"image_role": "shot_background"}
{"image_role": "shot_keyframe"}
```

## 9. `shot_manifest_generation` 节点

### 9.1 节点性质

纯本地节点，不调用 LLM。

输入：

- `clip_to_shots`；
- `shot_keyframe_prompt`；
- `shot_keyframe_image_generation`；
- `state.roles`；
- `state.props`；
- `state.layouts`。

职责：

- 解析 `ref_ids`；
- 分离 role、appearance、prop、layout ID；
- 验证每个引用资产真实存在；
- 绑定 shot 首帧；
- 保存 dialogue；
- 保存原始和最终 video prompt；
- 生成 provider 无关的视频输入列表；
- 写出 episode runtime manifest。

### 9.2 视频输入合同

默认图片输入只有：

```text
image_1: shot_start_frame
```

Kling 主体 element 不进入静态 manifest 的图片列表，由 generation 阶段根据 `role_ids` 动态追加。

不再默认提交：

- 十二宫格 storyboard；
- 尾帧；
- 角色板；
- 场景三视图；
- 道具图。

这些视觉信息已经在 shot 首帧中完成融合。

### 9.3 完整性校验

硬校验：

- episode 中 shot index 连续；
- shot ID 唯一；
- manifest shot 数等于 `clip_to_shots` shot 总数；
- 每个 shot 有有效关键帧；
- 每个 `ref_id` 可解析；
- 每个角色 appearance 有有效 roleboard；
- 每个 layout 有有效三视图；
- `duration_seconds` 在 3 到 15；
- `video_prompt` 非空；
- 有对白角色可以映射到 `role_id`；
- manifest 中不出现 storyboard 资产。

## 10. Shot 视频生成

### 10.1 节点命名

新增：

```text
autodrama/src/autodrama/workflows/nodes/shot_video_node.py
```

正式节点名：

```text
shot_video_generation
```

旧 `clip_video_generation` 保留为 deferred legacy 节点，不进入默认 generation 链。

公共内部方法逐步从 `_clip_video_*` 改名为 `_shot_video_*`。为减少一次性风险，可以先保留兼容 wrapper：

```python
_clip_video_inputs = _shot_video_inputs
_clip_video_refs = _shot_video_refs
```

### 10.2 可灵请求

当前可灵提交链路显式传入 `multi_shot: False`，需要改为：

```python
{
    "audio": "native",
    "multi_shot": True,
}
```

同时将 Kling Omni provider 的 fallback 默认值由 `False` 改为 `True`：

```python
metadata.get("multi_shot", self.settings.options.get("multi_shot", True))
```

显式 metadata 仍拥有最高优先级，以便单次调试覆盖。

### 10.3 视频 prompt

单镜头：

```text
后方低角度稳定跟拍，骑手驾驶雪地摩托持续向前，车轮扬起细雪，人物身份和服装保持首帧一致。
```

多镜头：

```text
镜头1-后远景低角度跟拍，骑手向前驶去
镜头2-侧方低角度近景，特写摩托车车轮
镜头3-骑手第一人称主观视角，前方是摩托车车把和表盘
镜头4-正面中景迎向摩托跟拍，骑手头盔正对镜头
镜头5-侧方平拍跟拍，轻微跟移
镜头6-高空微俯远景，镜头拉高，雪地摩托驶向雪原深处
```

不要在视频 prompt 中加入：

- 项目名、episode/clip/shot ID；
- provider/model 名称；
- 路径或 URL；
- 工作流解释；
- storyboard/P01-P12 说明；
- 与画面无关的结构化字段名。

角色 element、音色和对白绑定属于实际生成内容约束，可以由 `_kling_native_prompt()` 以最短形式前置。

## 11. 默认链路和旧链路共存

### 11.1 新节点模块

在 `autodrama/src/autodrama/workflows/nodes/__init__.py` 中新增：

```python
SHOT_ASSET_NODE_NAMES = [
    "clip_to_shots",
    "shot_keyframe_prompt",
    "shot_keyframe_image_generation",
    "shot_manifest_generation",
]
```

旧的 `STORYBOARD_ASSET_NODE_NAMES` 不删除，但移动到 deferred pregen 集合。

默认 `PREGEN_NODE_NAMES` 只加入 `SHOT_ASSET_NODE_NAMES`。

### 11.2 CLI

新增或调整作用域：

`--episodes` 支持：

```text
clip_segment
clip_to_shots
shot_keyframe_prompt
shot_keyframe_image_generation
shot_manifest_generation
```

`--clips` 支持：

```text
clip_to_shots
旧 clip storyboard 节点
```

在 pregen 增加 `--shots`：

```text
shot_keyframe_prompt
shot_keyframe_image_generation
shot_manifest_generation
```

generation 现有 `--shots` 继续使用。

示例：

```powershell
run\start.cmd --config config.yaml --project huyao --only clip_segment --episodes 1 --force
run\start.cmd --config config.yaml --project huyao --only clip_to_shots --episodes 1 --clips 1 --force
run\start.cmd --config config.yaml --project huyao --only shot_keyframe_prompt --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project huyao --only shot_keyframe_image_generation --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project huyao --only shot_manifest_generation --episodes 1 --shots 1-3 --force
run\start.cmd --config config.yaml --project huyao --only clip_storyboard_prompt --episodes 1 --clips 1 --force
```

### 11.3 Manifest 共存

新旧 manifest 都可能写入：

```text
shots/<episode_key>.json
```

因此必须用：

```json
{
  "schema_version": 2,
  "manifest_kind": "shot"
}
```

区分新链路。

建议：

- `shot_video_generation` 只接受 `manifest_kind="shot"`；
- legacy `clip_video_generation` 接受旧 manifest；
- 最后一次运行哪个 manifest 节点，哪个链路就成为当前 runtime manifest；
- 节点级 JSON 始终保留各自来源，可用于重新发布 runtime manifest。

### 11.4 旧项目迁移

不自动把十二宫格转换成新 shot。

旧项目切换新链路时需要：

1. 重跑 `roleboard_prompt` 和 `roleboard_image_generation`，建立锚点；
2. 重跑 `layout_prompt` 和 `layout_image_generation`，生成三视图；
3. 重跑 `clip_segment`，生成 60 到 120 秒 clip；
4. 从 `clip_to_shots` 开始执行新链路。

旧 storyboard、旧关键帧和旧 clip manifest 文件不主动删除。

## 12. Prompt 模板原则

所有 prompt 模板都直接发给裸模型，不按 Agent/Skill 风格编写。

模板只包含：

- 模型需要完成的内容任务；
- 必要输入；
- 必要输出结构；
- 会直接影响内容正确性的约束。

模板不包含：

- 项目目录结构；
- 节点依赖解释；
- 调试方式；
- CLI 命令；
- 代码类名；
- 保存路径；
- 重跑策略；
- 项目 ID、文件名和无关 episode 元数据。

代码负责：

- ID 生成；
- 路径和 URL；
- 节点依赖；
- provider/model 选择；
- prompt 风格和负面词拼接；
- 资产解析；
- 缓存和 fingerprint；
- 作用域合并；
- 结构校验；
- 错误信息。

## 13. 配置和模型路由

### 13.1 建议配置

```yaml
generation:
  visual_style_prompt: >-
    Photorealistic cinematic live-action image, natural skin texture,
    physically plausible materials, realistic lighting and atmospheric depth.
  roleboard_style_prompt: >-
    Photorealistic cinematic character reference photography.

nodes:
  roleboard_image_generation:
    params:
      anchor_role: ""
      anchor_appearance: "base"
      roleboard_image_generation_concurrency: 2

  shot_keyframe_prompt:
    params:
      negative_prompt: >-
        extra people, duplicate bodies, identity drift, malformed hands,
        inconsistent scale, floating objects, text, subtitles, logo, watermark

  shot_keyframe_image_generation:
    params:
      concurrency: 2
```

### 13.2 Model catalog

增加节点绑定：

```text
clip_to_shots -> text model
shot_keyframe_image_generation -> reference-capable image model
shot_video_generation -> Kling/video model
```

以下节点不需要模型绑定：

```text
shot_keyframe_prompt
shot_manifest_generation
```

fake provider 增加 `ClipToShotsModelOutput` 的稳定 fixture，便于本地 smoke 验证。

## 14. 文件修改清单

| 文件 | 修改 |
| --- | --- |
| `autodrama/src/autodrama/core/schemas.py` | 新增 shot plan、keyframe prompt/image、shot manifest schema |
| `autodrama/src/autodrama/prompts/clip_segment.md` | 8-15 秒改为 60-120 秒，移除 storyboard 语义 |
| `autodrama/src/autodrama/prompts/clip_to_shots.md` | 新增最小 shot 规划模板 |
| `autodrama/src/autodrama/prompts/roleboard_prompt.md` | 精简角色板任务 |
| `autodrama/src/autodrama/prompts/roleboard_prompt/*.md` | 同步各图片模型版本 |
| `autodrama/src/autodrama/prompts/layout_prompt*.md` | 改为纯英文、真实场景三视图 prompt |
| `autodrama/src/autodrama/services/script_service.py` | 传入 60/120 秒切分参数 |
| `autodrama/src/autodrama/services/director_service.py` | 新增 `clip_to_shots()` |
| `autodrama/src/autodrama/workflows/nodes/script_nodes.py` | 修改 clip 时长常量和 warning |
| `autodrama/src/autodrama/workflows/nodes/role_nodes.py` | 精简 prompt 拼接和锚点上下文 |
| `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py` | 锚点三阶段生成；layout 三视图 metadata |
| `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py` | 新增四个 shot pregen 节点 |
| `autodrama/src/autodrama/workflows/nodes/shot_video_node.py` | 新增正式 shot 视频节点 |
| `autodrama/src/autodrama/workflows/nodes/__init__.py` | 新默认链、旧 storyboard deferred |
| `autodrama/src/autodrama/workflows/pregen.py` | 新节点 runner、episode/clip/shot scope |
| `autodrama/src/autodrama/workflows/generation.py` | 读取 shot manifest 和 shot 视频输入 |
| `autodrama/src/autodrama/workflows/dynamic_assets.py` | shot 命名、首帧输入、`multi_shot=true` |
| `autodrama/src/autodrama/workflows/selection.py` | pregen shot selector |
| `autodrama/src/autodrama/repositories/project_layout.py` | 新 node 目录和两类 shot 图片目录 |
| `autodrama/src/autodrama/providers/kling/video/omni.py` | multi_shot 默认 true |
| 各 image provider | 识别 `shot_keyframe_image_generation` purpose |
| `autodrama/src/autodrama/providers/local/mock/fake.py` | 新 schema fixture |
| `autodrama/src/autodrama/cli.py` | 新节点、`--shots` 和帮助文本 |
| `config.yaml.example` | 新节点配置示例 |
| `model_catalog.yaml.example` | 新节点模型绑定 |
| `README.md`、`autodrama/README.md`、`PREGEN_NODES.md` | 新默认链、命令和迁移说明 |

## 15. 实施顺序

### 阶段 1：数据合同和 fake provider

1. 新增 schema；
2. 新增 prompt 模板；
3. 新增 fake provider 输出；
4. 新增 project layout 路径；
5. 先完成纯结构 smoke。

### 阶段 2：角色板锚点

1. 实现锚点选择；
2. 把角色板生成拆成 anchor/base/variant；
3. 调整 refs；
4. 精简角色板 prompts；
5. 验证 `--episodes` 和 `--force`。

### 阶段 3：场景三视图

1. 修改 layout prompt；
2. 同步 provider-specific 模板；
3. 增加三视图 metadata；
4. 验证 variant image-edit 仍参考 base。

### 阶段 4：长 clip 和 `clip_to_shots`

1. 修改 `clip_segment`；
2. 新增 `clip_to_shots` service 和 node；
3. 实现 ref index；
4. 实现 ID 规范化和结构校验；
5. 实现 episode/clip 局部合并。

### 阶段 5：shot 关键帧

1. 实现本地 `shot_keyframe_prompt`；
2. 实现背景图阶段；
3. 实现人物和道具融合阶段；
4. 实现缓存、fingerprint 和局部重跑。

### 阶段 6：manifest 和视频

1. 实现 `shot_manifest_generation`；
2. 更新 dynamic loader；
3. 新增 `shot_video_generation`；
4. 可灵默认 `multi_shot=true`；
5. 验证角色 element、音色和对白链路。

### 阶段 7：切换默认链和文档

1. 新 shot 链加入默认 pregen；
2. 旧 storyboard 链移到 deferred；
3. 更新 CLI；
4. 更新 config/model catalog；
5. 更新 README；
6. 跑完整非 pytest 验证。

## 16. 非 pytest 验证

按仓库规则，不运行 pytest。新增或调整以下 smoke：

```text
scripts/smoke/roleboard_anchor_chain_smoke.py
scripts/smoke/layout_three_view_prompt_smoke.py
scripts/smoke/clip_segment_contract_smoke.py
scripts/smoke/clip_to_shots_contract_smoke.py
scripts/smoke/shot_keyframe_prompt_contract_smoke.py
scripts/smoke/shot_keyframe_image_generation_contract_smoke.py
scripts/smoke/shot_manifest_generation_contract_smoke.py
scripts/smoke/shot_selector_preservation_smoke.py
scripts/smoke/shot_default_chain_contract_smoke.py
scripts/smoke/kling_multi_shot_default_smoke.py
scripts/smoke/legacy_clip_nodes_deferred_smoke.py
scripts/smoke/shot_pipeline_fake_e2e_smoke.py
```

建议验证命令：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/roleboard_anchor_chain_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/layout_three_view_prompt_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/clip_segment_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/clip_to_shots_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_keyframe_prompt_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_keyframe_image_generation_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_manifest_generation_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_preservation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_default_chain_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/kling_multi_shot_default_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/legacy_clip_nodes_deferred_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_pipeline_fake_e2e_smoke.py
```

所有 smoke 临时输出写入仓库 `.tmp/`。

## 17. 验收标准

### 角色板

- 一个项目只产生一个明确锚点角色板；
- 锚点一定先于其他角色板生成；
- 所有后续 base 角色板都包含锚点 ref；
- variant 同时包含锚点和自己的 base ref；
- 角色板 prompt 明显简化；
- 图中不再依赖模型生成复杂中文标签。

### 场景

- layout image prompt 为纯英文；
- base layout 是真实摄影质感的三个一致视角；
- variant 保持 base 空间结构；
- 新 shot 链拒绝把 `reference_image_kind!="three_view"` 的旧场景当成新场景母版。

### Clip 和 shot

- `clip_segment` prompt 明确 60 到 120 秒；
- clip 数量不再与 episode 时长硬绑定；
- 每个 shot 时长为 3 到 15 秒；
- shot ID 全局稳定且顺序连续；
- keyframe prompt 明确空间关系和摄影角度；
- `ref_ids` 全部可解析；
- 多镜头 prompt 编号连续。

### 关键帧

- 不生成十二宫格；
- 每个非空镜 shot 先生成无人背景，再生成人物首帧；
- 最终视频只依赖融合后的首帧作为主要图片输入；
- 局部失败可复用已成功背景；
- `--shots` 不覆盖未选中 shot。

### Manifest 和视频

- `shot_manifest_generation` 是默认 pregen 终点；
- manifest shot 数和 `clip_to_shots` 一致；
- runtime manifest 明确 `manifest_kind="shot"`；
- 默认 generation 使用 `shot_video_generation`；
- 可灵请求中 `multi_shot=true`；
- 旧 clip storyboard 节点仍可通过 `--only` 调用；
- 默认链不会生成 storyboard sheet 或 storyboard keyframe。

## 18. 风险与处理

### 18.1 锚点把人物身份带到其他角色

处理：

- 后续 base prompt 明确锚点只控制风格、光线、材质和画面布局；
- 不要求复制锚点人物的脸、服装和体型；
- 后续角色板只使用一个锚点，避免多风格 refs 竞争；
- 真实项目抽样检查至少三个差异明显的角色。

### 18.2 场景三视图被错误复制成最终三联画

处理：

- 背景阶段明确 `one single cinematic frame, not a triptych`；
- 场景三视图只进入背景阶段；
- 最终关键帧只参考已经投射好的单画面背景。

### 18.3 参考图过多

处理：

- shot 切分时限制同屏核心人物数量；
- 背景优先；
- 不把 layout 三视图重复提交到最终关键帧；
- 超过 provider 限制时硬报错并要求拆 shot。

### 18.4 多镜头内容与 3 到 15 秒不匹配

处理：

- 3 到 5 秒通常只允许一个镜头；
- 较多内部镜头只用于较长 shot；
- validator 对镜头数和时长不匹配记 warning 或按阈值报错；
- 不让模型在 3 秒内规划六个镜头。

### 18.5 旧 manifest 覆盖新 manifest

处理：

- runtime manifest 写入 `schema_version` 和 `manifest_kind`；
- 新旧视频节点分别校验 manifest kind；
- 节点 provenance 分目录保存；
- 文档明确最后运行的 manifest 节点决定当前 active runtime manifest。

### 18.6 现有工作树改动较多

实施时：

- 不重置或覆盖已有用户修改；
- 新 shot 节点优先放在新文件；
- 对 `static_asset_nodes.py`、`generation.py`、`storyboard_asset_nodes.py` 等已有改动文件做小范围补丁；
- 每阶段完成后运行对应 smoke，再进入下一阶段。
