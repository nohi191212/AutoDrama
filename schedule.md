# AutoDrama 项目背景与重构规划

## 1. 项目背景

AutoDrama 是一个面向短剧自动生成的 AIGC 流水线项目。当前主体代码位于 `autodrama/src/autodrama`，采用 Python `src` layout，包名为 `autodrama`。仓库根目录下没有 `src/autodrama`，实际源码路径多了一层 `autodrama/`。

项目目标是把输入故事大纲逐步转换为可复用的角色、道具、场景、音乐、分镜、镜头视频和最终合成结果。当前流程已经覆盖从文本规划到多媒体生成的完整 MVP：

- `pregen`：脚本、角色、静态资产、BGM 等预生成流程。
- `generation`：按集和镜头生成动态资产，包括 storyboard、shot BGM、ref frame、shot video、dynamic asset solidification。
- `editing`：基于已生成镜头、音频、字幕和 ffmpeg 进行最终视频合成。

核心入口是 CLI：

- `autodrama/src/autodrama/cli.py`
- 命令形态包括 `init`、`run pregen`、`run generation`、`inspect state`、`inspect nodes`。

核心目录职责如下：

- `core/`：Pydantic schema、ID 规范、错误类型、视觉风格定义。
- `repositories/`：项目目录和 JSON 状态读写。
- `services/`：面向 LLM 的 prompt 编排服务，例如 script、role、asset、storyboard。
- `providers/`：外部供应商适配，包括阿里云、DeepSeek、火山、MiniMax、ElevenLabs、RightCode、fake provider。
- `workflows/`：流程编排层，当前承载了大部分业务控制逻辑。
- `prompts/`：各节点 prompt 模板。

现有设计已经具备较好的工程基础：

- 使用 Pydantic 描述主要状态和节点输出。
- 有 `ProviderRouter` 做能力路由。
- 有 fake provider 和 smoke 脚本支持本地流程验证。
- 项目产物以 JSON 和媒体文件形式落盘，便于 resume 和人工检查。
- `generation_checklist.json` 和 `generation_tasks.json` 已经开始支持分集、异步任务和恢复执行。

主要风险不在功能缺失，而在结构继续扩展后的维护成本。当前编排层过重、文件契约隐式、状态模型过宽、provider 层重复较多。后续如果继续增加节点、供应商、重跑策略和最终合成能力，应该先做边界收敛，再继续堆功能。

## 2. 当前主要设计问题

### 2.1 `PregenWorkflow` 过度膨胀

`autodrama/src/autodrama/workflows/pregen.py` 当前约 3000 多行，承担了大量不同层次的职责：

- 节点调度。
- 状态迁移。
- 脚本内容读写。
- 角色设计合并。
- 语音生成策略。
- 图片、音频、视频下载与落盘。
- 项目相对路径转换。
- storyboard 文件读写。
- 静态资产生成。

这使 `PregenWorkflow` 不再只是 pre-generation workflow，而是半个应用内核。任何小改动都可能影响多个流程，单元级验证困难，也不利于继续新增节点。

### 2.2 `GenerationWorkflow` 和 `EditingWorkflow` 通过继承复用 `PregenWorkflow`

`GenerationWorkflow` 继承 `DynamicAssetNodeMixin, PregenWorkflow`，`EditingWorkflow` 也继承 `PregenWorkflow`。实际原因主要是复用路径、状态、媒体写入、分镜读写等 helper，但结果是动态资产流程和剪辑流程被迫依赖整条 pregen 业务。

这类复用方式不合理。更好的方式是把公共能力抽到独立 service 或 utility，例如：

- `ProjectLayout`
- `MediaStore`
- `WorkflowRunContext`
- `StoryboardRepository`
- `WorkflowRunner`

然后让不同 workflow 显式依赖这些组件，而不是继承一个庞大的业务 workflow。

### 2.3 运行上下文依赖动态属性

当前 workflow 中存在若干运行时动态属性：

- `_active_episode_keys`
- `_active_shot_selectors`
- `_force_pregen`
- `_force_generation`
- `_burn_subtitles`

这些属性通过 `getattr` 和 `setattr` 在运行过程中隐式传递。短期可用，但长期不利于并发、嵌套调用、实例复用和测试。应改为显式 `WorkflowRunContext`。

### 2.4 状态模型层次混杂

`ProjectState.metadata` 当前承载了很多业务字段，例如：

- `episode_count`
- `episode_duration_seconds`
- `visual_style`
- `simple_script`
- `global_script`
- `dynamic_assets`
- `role_design_generation_mode`

同时 `StoryboardShot` 同时承载分镜设计字段和实际生成字段，例如：

- `ref_frame_prompt`
- `video_prompt`
- `ref_frame_asset_path`
- `video_asset_path`
- `video_task_id`
- `video_raw_response`

这让领域输入、节点输出、provider 响应和执行状态混在一起。重构时不能破坏旧 JSON，但需要逐步引入更明确的 typed wrapper。

### 2.5 文件路径契约分散

项目目录结构由多个位置共同决定：

- `ProjectRepository._create_project_dirs()`
- `PregenWorkflow._script_content_path()`
- `PregenWorkflow._image_asset_path()`
- `PregenWorkflow._audio_asset_path()`
- `PregenWorkflow._video_asset_path()`
- `EditingWorkflow._edit_plan_path()`
- `EditingWorkflow._episode_output_path()`

这些路径是项目持久化契约，应该集中在 `ProjectLayout` 或类似组件里管理，避免路径规则散落在多个 workflow 中。

### 2.6 Provider 层重复逻辑较多

`providers/base.py` 已有协议定义，这是正确方向。但具体 provider 中存在较多重复：

- `httpx.AsyncClient` 创建。
- HTTP 状态码检查。
- JSON 响应解析。
- request id 提取。
- base64 数据处理。
- 本地文件转 data URL。
- header 脱敏。

典型重复出现在 RightCode、Seedream、Seedance、Wanxiang、MiniMax、ElevenLabs、Volcengine audio provider 中。建议提取公共 HTTP 和 media ref 工具。

### 2.7 Prompt 渲染缺少安全检查

`PromptStore.render()` 当前只做简单字符串替换：

```python
rendered = rendered.replace("{{" + key + "}}", str(value))
```

问题：

- 模板中变量漏传不会报错。
- 传入无用变量不会提示。
- 残留 `{{...}}` 可能被直接发给模型。

prompt 是 AIGC 流水线的核心接口，应尽早失败，而不是让模型用坏 prompt 生成坏 JSON。

### 2.8 `completed_nodes` 与分集/分镜粒度不完全匹配

`ProjectState.completed_nodes` 是全局节点级完成状态，但 `generation` 已经支持按 episode 和 shot 选择、重跑、恢复异步任务。实际状态粒度已经超过全局节点。

现有 `generation_checklist.json` 和 `generation_tasks.json` 是合理补充。后续应让它们成为真实执行记录，`completed_nodes` 保留为兼容摘要字段。

### 2.9 Editing 工作流同样过重

`editing.py` 同时处理：

- edit plan 生成。
- 缺失资产检查。
- 字幕 cue 构造。
- SRT/ASS 写入。
- 音轨 filter 构造。
- ffmpeg 命令拼接。
- ffmpeg 执行。

如果未来支持更多合成策略、转场、画幅、字幕样式或多版本输出，这个文件会继续膨胀。建议拆为 planner、subtitle writer、composer。

## 3. 重构目标

本次重构规划的目标是“不影响实际功能”的结构性整理，不是重写系统。

必须保持：

- CLI 命令、参数和行为兼容。
- 节点名兼容。
- 现有 JSON 输出路径兼容。
- 现有项目目录可以继续 resume。
- prompt 文件名兼容。
- fake provider 和 smoke 脚本继续可用。
- 不引入 pytest 作为验证方式。

希望达到：

- workflow 只负责编排，不直接承担所有业务细节。
- 公共路径、媒体写入、执行上下文显式化。
- provider 接入成本降低。
- schema 分层更清晰。
- 重跑和 resume 语义更明确。
- 后续新增节点时不需要继续修改超大文件。

## 4. 重构原则

1. 先抽公共能力，再拆业务节点。
2. 每一步保持旧 API 和旧 JSON 兼容。
3. 不在第一轮重构中改变 prompt 内容。
4. 不在第一轮重构中改变 provider 返回模型。
5. 不引入数据库替代当前 JSON 项目目录。
6. 不把流程并发化，除非单独立项。
7. 所有验证使用非 pytest 方式。
8. 对旧 import 路径保留 re-export，避免一次性大范围修改脚本。

## 5. 分阶段重构计划

### 阶段 0：建立行为基线

目的：确保后续每次结构调整都能确认没有破坏实际功能。

工作项：

- 明确推荐验证命令。
- 选择固定 smoke 脚本作为基线。
- 保存关键 JSON 结构样本，作为人工对比参考。

推荐验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
```

注意：

- 不使用 `pytest`。
- 不运行 `python -m pytest`。
- 不运行以内联 Python 为主的验证。
- 新增 smoke 脚本时放在 `scripts/smoke/`。
- 临时输出放在仓库 `.tmp/`。

交付物：

- 一组固定验证命令。
- 当前 fake workflow 的输出结构说明。
- 后续阶段每次改动前后都执行这些验证。

### 阶段 1：抽基础设施，不改业务逻辑

目的：先把 workflow 里最通用、最无业务争议的能力抽出来，降低后续拆分风险。

新增建议：

- `autodrama/src/autodrama/workflows/context.py`
- `autodrama/src/autodrama/repositories/project_layout.py`
- `autodrama/src/autodrama/services/media_store.py`
- `autodrama/src/autodrama/workflows/selection.py`
- `autodrama/src/autodrama/repositories/storyboard_repo.py`

#### 1.1 `WorkflowRunContext`

建议字段：

- `project_dir`
- `force`
- `selected_episode_keys`
- `shot_selectors`
- `until`
- `only`
- `burn_subtitles`
- `workflow_name`

替换目标：

- `_active_episode_keys`
- `_active_shot_selectors`
- `_force_pregen`
- `_force_generation`
- `_burn_subtitles`

兼容策略：

- 第一轮可以保留旧属性，但新增 context 并逐步切换调用点。
- 旧属性全部移除前，每次只替换一个工作流。

#### 1.2 `ProjectLayout`

集中管理路径：

- `state_path`
- `project_json_path`
- `current_project_path`
- `node_output_path(node_name)`
- `script_content_path(category, episode_key)`
- `role_design_path(role_id)`
- `prop_design_path(prop_id)`
- `shot_path(episode_key)`
- `image_asset_path(asset_type, asset_id)`
- `audio_asset_path(asset_type, asset_id, audio_format)`
- `music_asset_path(asset_id, audio_format)`
- `video_asset_path(asset_type, asset_id)`
- `edit_plan_path(episode_key)`
- `episode_output_path(episode_key)`
- `subtitle_srt_path(episode_key)`
- `subtitle_ass_path(episode_key)`

兼容策略：

- 所有返回路径保持当前目录结构不变。
- `ProjectRepository._create_project_dirs()` 可以继续保留，但目录列表应逐步从 `ProjectLayout` 获取。

#### 1.3 `MediaStore`

承接当前 workflow 中的媒体落盘逻辑：

- base64 图片写入。
- URL 图片下载。
- base64 音频写入。
- URL 音频下载。
- base64 视频写入。
- URL 视频下载。
- 文件扩展名规范化。
- 项目相对路径转换。

来源函数：

- `_write_first_generated_image`
- `_write_generated_music`
- `_write_generated_audio`
- `_write_generated_video`
- `_write_preview_audio`
- `_existing_project_file`
- `_project_relative`

兼容策略：

- 初期在 `PregenWorkflow` 中保留同名 wrapper，内部委托给 `MediaStore`，减少一次性修改范围。

#### 1.4 `selection.py`

集中处理：

- episode key 解析。
- episode key 校验。
- story order 排序。
- shot selector 解析和匹配。

来源函数：

- `cli.parse_episode_keys`
- `cli.parse_shot_selectors`
- `PregenWorkflow._select_episode_keys`
- `GenerationWorkflow._sort_episode_keys_in_story_order`
- `DynamicAssetNodeMixin._shot_matches_selectors`
- `EditingWorkflow._select_episode_keys`

兼容策略：

- CLI 保留原函数名，内部调用 `selection.py`。

阶段验收：

- `compileall` 通过。
- fake dynamic smoke 通过。
- episode-only 和 shot selector smoke 通过。
- 生成路径不变化。

### 阶段 2：拆分 `PregenWorkflow`

目的：把 3000 多行 pregen 大文件拆成可维护的节点模块，同时保持 `PregenWorkflow.run()` 外观不变。

新增建议：

- `autodrama/src/autodrama/workflows/runner.py`
- `autodrama/src/autodrama/workflows/nodes/script_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/voice_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/bgm_nodes.py`

#### 2.1 `WorkflowRunner`

统一处理：

- 节点顺序。
- `until` 和 `only`。
- `force` 和 skip。
- 日志上下文。
- 异常记录。
- `state.mark_completed(node_name)`。
- `repo.save_state(project_dir, state)`。
- `repo.save_node_output(...)` 的统一时机。

建议接口：

```python
class WorkflowNode(Protocol):
    name: str

    async def run(self, ctx: WorkflowRunContext, state: ProjectState) -> ProjectState:
        ...
```

#### 2.2 Script 节点

迁移内容：

- `script_outline`
- `script_novel`
- `script_novel_extract`

保留行为：

- per-episode JSON 文件路径不变。
- `state.script.episode_outlines`、`novel_full`、`novel_extract` 存储仍为相对路径或 `false`。
- `assets/json/nodes/script_*.json` 结构保持兼容。

#### 2.3 Role 节点

迁移内容：

- `role_extract`
- `role_design`
- role design file hydrate/merge 逻辑。

保留行为：

- `run pregen --only role_design --episodes ...` 继续支持。
- 角色设计文件路径保持不变。
- 旧 `role_design.json` 可继续被读取。

#### 2.4 Voice 节点

迁移内容：

- voice design 绑定。
- direct emotion synthesis。
- design/clone/reuse/synthesis 策略。
- role voice generation 输出。

保留行为：

- 火山 Seed TTS 直接合成策略不变。
- Qwen/CosyVoice 兼容逻辑不变。
- 语音文件路径不变。

#### 2.5 Static asset 节点

迁移内容：

- `role_appearance_generation`
- `prop_design`
- `prop_image_generation`
- `script_compress`
- `layout_design`
- `layout_dedupe_review`
- `layout_image_generation`

保留行为：

- 角色、道具、场景图片路径不变。
- role-bound prop 引用逻辑不变。
- reference image 传递逻辑不变。

#### 2.6 BGM 节点

迁移内容：

- `bgm_design`
- `bgm_generation`

保留行为：

- BGM 数量校验不变。
- BGM 文件路径不变。
- MiniMax 和 Bailian provider 行为不变。

阶段验收：

- `PregenWorkflow.run()` 调用方式不变。
- `run pregen --provider fake --force` 输出结构不变。
- `dynamic_assets_fake_smoke.py` 通过。
- `role_extract_design_scoping_smoke.py` 通过。

### 阶段 3：让 Generation 和 Editing 脱离 `PregenWorkflow` 继承

目的：移除不合理继承，改成组合。

#### 3.1 Generation workflow

新增或整理：

- `workflows/nodes/storyboard_node.py`
- `workflows/nodes/shot_bgm_node.py`
- `workflows/nodes/ref_frame_node.py`
- `workflows/nodes/shot_video_node.py`
- `workflows/nodes/dynamic_asset_solidification_node.py`

移除目标：

- `GenerationWorkflow(DynamicAssetNodeMixin, PregenWorkflow)`
- `DynamicAssetNodeMixin` 作为大 mixin 的形态。

改为：

- `GenerationWorkflow` 组合 `WorkflowRunner`、`ProjectLayout`、`MediaStore`、`StoryboardRepository`、`ProviderRouter`。

保留行为：

- `generation_checklist.json` 行为不变。
- `generation_tasks.json` 行为不变。
- 按 episode 和 shot 选择不变。
- storyboard history 注入不变。
- shot video 异步任务恢复不变。

#### 3.2 Editing workflow

新增建议：

- `editing/planner.py`
- `editing/subtitles.py`
- `editing/ffmpeg.py`
- `editing/composer.py`

拆分职责：

- `EditPlanner`：从 storyboard 生成 `EpisodeEditPlan`。
- `SubtitleWriter`：写 SRT 和 ASS。
- `FfmpegComposer`：拼接和执行 ffmpeg 命令。
- `EditingWorkflow`：只调度节点并保存状态。

保留行为：

- edit plan JSON 路径不变。
- 输出视频路径不变。
- 字幕路径不变。
- 默认 burn subtitle 行为不变。

阶段验收：

- `only_node_episode_smoke.py` 通过。
- `shot_selector_smoke.py` 通过。
- `edit_plan_smoke.py` 通过。
- `final_video_composition_smoke.py` 通过。

### 阶段 4：状态模型分层和 typed metadata

目的：让 schema 更清晰，但不破坏旧 JSON。

新增建议：

- `core/state.py`
- `core/domain.py`
- `core/node_outputs.py`
- `core/media.py`
- `core/metadata.py`

保留：

- `core/schemas.py` 继续 re-export 所有旧类。
- 旧 import 不改或逐步改。

#### 4.1 拆分类别

`domain.py`：

- `Role`
- `RoleAudio`
- `RoleAppearance`
- `Prop`
- `Layout`
- `BGM`
- `StoryboardShot`
- `StoryboardEpisodeOutput`

`state.py`：

- `ScriptBundle`
- `BudgetState`
- `ProjectState`

`node_outputs.py`：

- `ScriptOutlineOutput`
- `RoleDesignOutput`
- `StaticAssetGenerationOutput`
- `ShotVideoGenerationOutput`
- 其他节点输出类。

`media.py`：

- 生成结果引用。
- 资产记录。
- 动态资产固化记录。

#### 4.2 typed metadata

新增兼容型 wrapper：

- `ProjectMetadata`
- `DynamicAssetRecord`
- `GenerationTaskRegistry`
- `GenerationChecklist`

策略：

- 读取旧 JSON 时允许 `dict[str, Any]`。
- 写出 JSON 时保持字段兼容。
- 内部逻辑逐步从裸 `metadata["key"]` 改为 helper 方法。

阶段验收：

- 所有旧 smoke 通过。
- 随机打开旧项目 `state.json` 可以正常 `ProjectState.model_validate_json()`。

### 阶段 5：整理 Provider 层

目的：减少重复，降低新供应商接入成本。

新增建议：

- `providers/http.py`
- `providers/media_refs.py`
- `providers/registry.py`
- `providers/response_utils.py`

#### 5.1 `providers/http.py`

封装：

- JSON POST。
- JSON GET。
- stream POST。
- HTTP 状态码错误。
- 非 JSON 响应错误。
- 连接错误。
- 超时错误。
- request id 提取。

#### 5.2 `providers/media_refs.py`

封装：

- `AssetRef` 转 URL。
- `AssetRef` 转 data URL。
- mime type 判断。
- 本地图片/audio/video base64 编码。
- 最大 reference 数量裁剪。

替换目标：

- RightCode `_data_url`
- Seedream `_data_url`
- Seedance `_data_url`
- Volcengine audio 的本地音频编码部分

#### 5.3 `ProviderRouter` 改注册表

当前 router 是硬编码分支。建议改成 provider registry，但保留原别名。

别名兼容：

- `aliyun`
- `qwen`
- `bailian`
- `wanxiang`
- `deepseek`
- `volcengine`
- `seedream`
- `seedance`
- `rightcode`
- `minimax`
- `elevenlabs`
- `fake`

阶段验收：

- `config_apikeys_smoke.py` 通过。
- `seedance_router_smoke.py` 通过。
- `seedream_payload_smoke.py` 通过。
- `rightcode_image_payload_smoke.py` 通过。
- `minimax_music_payload_smoke.py` 通过。
- `elevenlabs_music_payload_smoke.py` 通过。

### 阶段 6：增强 PromptStore

目的：让 prompt 渲染从静默失败变成早失败。

建议改动：

- 检查模板文件是否存在，错误信息包含 prompt 名。
- 检查模板残留 `{{...}}`。
- 对未使用变量记录 debug 或 warning。
- 可选：增加 `strict=True` 默认开启。

建议行为：

```python
prompt = store.render("script_outline", title="Demo")
```

如果模板中还有 `{{raw_script}}` 未替换，应抛出明确错误。

阶段验收：

- 所有 prompt 相关 smoke 通过。
- fake pregen 通过。
- 不改变 prompt 内容，只改变错误检查。

### 阶段 7：文档和工程清理

目的：减少新开发者误解和工具配置冲突。

工作项：

- README 明确源码路径是 `autodrama/src/autodrama`。
- 文档中统一使用非 pytest 验证。
- 解释 `autodrama/tests` 目录存在但 repository agent 不运行 pytest 的原因，或后续改名为非 pytest smoke。
- 确认 `__pycache__` 未被 git 跟踪。
- 统一 `.tmp/` 作为临时输出目录。

## 6. 建议优先级

### P0：先做

- 阶段 0：行为基线。
- 阶段 1：`WorkflowRunContext`、`ProjectLayout`、`MediaStore`、`selection.py`。

原因：

- 风险低。
- 收益直接。
- 能为后续拆大文件铺路。
- 不改变业务行为。

### P1：随后做

- 阶段 2：拆 `PregenWorkflow`。
- 阶段 3：`GenerationWorkflow` 和 `EditingWorkflow` 脱离 `PregenWorkflow` 继承。

原因：

- 这是主要架构问题。
- 改动面大，需要前置基础设施和 smoke 基线。

### P2：稳定后做

- 阶段 4：schema 分层和 typed metadata。
- 阶段 5：provider 公共化。

原因：

- 影响范围较大。
- 需要在 workflow 边界更清晰后执行。

### P3：持续优化

- 阶段 6：PromptStore 严格检查。
- 阶段 7：文档和工程清理。

原因：

- 单项风险低，但要避免和大规模移动文件混在一起。

## 7. 不建议现在做的事

以下事项不适合作为第一轮重构内容：

- 不重写整条 workflow。
- 不改变项目目录 JSON 布局。
- 不把 JSON 状态迁移到数据库。
- 不把生成流程并发化。
- 不重写 prompt 内容。
- 不同时更换多个 provider 接口。
- 不删除旧字段或旧 import 路径。
- 不引入 pytest 作为验证。

## 8. 推荐执行顺序

1. 创建阶段 0 验证基线，并记录当前 smoke 输出。
2. 新增 `ProjectLayout`，但保留旧路径函数 wrapper。
3. 新增 `MediaStore`，让旧 `_write_generated_*` 委托过去。
4. 新增 `WorkflowRunContext`，先替换 generation 中的动态属性。
5. 把 CLI 和 workflow 的 episode/shot 选择逻辑迁到 `selection.py`。
6. 拆 `PregenWorkflow` 中 script 节点。
7. 拆 role 节点。
8. 拆 voice 节点。
9. 拆 static asset 和 BGM 节点。
10. 让 `GenerationWorkflow` 改为组合式依赖。
11. 让 `EditingWorkflow` 改为组合式依赖。
12. schema 分包并 re-export。
13. provider HTTP/media ref 公共化。
14. PromptStore 开启严格变量检查。
15. 更新 README 和开发文档。

## 9. 每阶段完成标准

每个阶段必须满足：

- `compileall` 通过。
- 相关 smoke 脚本通过。
- CLI 调用方式不变。
- 旧项目目录能继续读取。
- 节点输出 JSON 路径不变。
- 新增代码有明确职责边界。
- 没有把业务逻辑迁到更隐蔽的位置。

基础验证命令：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
```

核心 smoke：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
```

按阶段追加：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_design_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedream_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/ref_frame_image_provider_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/elevenlabs_music_payload_smoke.py
```

## 10. 风险控制

### JSON 兼容风险

风险：旧项目状态和节点输出无法读取。

控制：

- Pydantic schema 改动只加字段，不删字段。
- 旧字段保留 alias。
- `schemas.py` 保持 re-export。
- 阶段 4 前不改 JSON 写出结构。

### Workflow 行为漂移风险

风险：拆分后 skip、force、only、until、episodes、shots 语义变化。

控制：

- 先抽 `WorkflowRunner`，再逐个节点迁移。
- 每迁移一组节点，跑对应 smoke。
- 对 `generation_checklist.json` 和 `generation_tasks.json` 做结构对比。

### Provider 请求漂移风险

风险：公共化 HTTP 或 media ref 后 payload 改变。

控制：

- provider 公共化放到后期。
- 先保留每个 provider 的 `build_payload()`。
- payload smoke 必须先覆盖再替换公共逻辑。

### 路径变化风险

风险：已有输出目录或 resume 逻辑失效。

控制：

- `ProjectLayout` 第一阶段只复刻旧路径。
- 所有旧路径 helper 先保留 wrapper。
- 对关键产物路径做 smoke 检查。

### Prompt 变量检查风险

风险：开启严格检查后暴露旧模板变量不一致，导致流程中断。

控制：

- 第一版可增加 `strict=False` 兼容开关。
- 修复所有模板变量后再默认严格。

## 11. 最终目标结构示意

目标不是一次性达到，而是作为中长期方向：

```text
autodrama/src/autodrama/
  cli.py
  config.py
  logging.py
  core/
    domain.py
    state.py
    node_outputs.py
    media.py
    metadata.py
    schemas.py
  repositories/
    project_repo.py
    project_layout.py
    storyboard_repo.py
  services/
    media_store.py
    script_service.py
    role_service.py
    asset_service.py
    storyboard_service.py
  providers/
    base.py
    http.py
    media_refs.py
    registry.py
    router.py
    ...
  workflows/
    context.py
    runner.py
    selection.py
    pregen.py
    generation.py
    editing.py
    nodes/
      script_nodes.py
      role_nodes.py
      voice_nodes.py
      static_asset_nodes.py
      bgm_nodes.py
      storyboard_node.py
      shot_bgm_node.py
      ref_frame_node.py
      shot_video_node.py
      dynamic_asset_solidification_node.py
  editing/
    planner.py
    subtitles.py
    ffmpeg.py
    composer.py
```

## 12. 结论

AutoDrama 当前功能链条已经基本成型，主要技术债集中在编排层和隐式契约上。最稳妥的重构路径是：

1. 固定 fake provider 和 smoke 验证基线。
2. 先抽路径、媒体写入、运行上下文和选择器。
3. 再拆 `PregenWorkflow`。
4. 随后移除 `GenerationWorkflow`、`EditingWorkflow` 对 `PregenWorkflow` 的继承。
5. 最后整理 schema 和 provider 公共层。

这样可以在不影响实际功能的前提下，逐步把项目从“能跑的流水线”推进到“可长期演进的生成系统”。

## 13. 本轮重构执行记录

执行日期：2026-05-22。

本轮已按“不影响实际功能”的原则完成阶段 0 到阶段 7 的兼容式重构落地。核心策略是：先建立独立基础设施和节点注册边界，再让旧 workflow 通过 wrapper 或 delegate 复用这些能力，避免一次性移动大量业务逻辑导致 JSON、路径或 resume 行为漂移。

### 阶段 0：行为基线

已完成：

- 使用 `compileall` 和 smoke 脚本作为唯一验证入口。
- 保持不使用 pytest。
- 固定核心验证脚本：
  - `scripts/smoke/dynamic_assets_fake_smoke.py`
  - `scripts/smoke/only_node_episode_smoke.py`
  - `scripts/smoke/episode_serial_generation_smoke.py`
  - `scripts/smoke/shot_selector_smoke.py`
  - `scripts/smoke/edit_plan_smoke.py`
  - `scripts/smoke/final_video_composition_smoke.py`
  - `scripts/smoke/refactor_boundaries_smoke.py`

### 阶段 1：基础设施抽取

已完成：

- 新增 `WorkflowRunContext`，集中表达 workflow 名称、项目路径、force、only、until、episode 选择、shot 选择和字幕开关。
- 新增 `ProjectLayout`，集中管理项目目录、state、node output、script、shot、asset、editing 输出路径。
- 新增 `MediaStore`，承接图片、音乐、音频、视频、preview audio 的落盘逻辑。
- 新增 `StoryboardRepository`，集中 storyboard 读写。
- 新增 `workflows/selection.py`，集中 episode 解析、episode 选择、shot selector 解析和匹配。
- `PregenWorkflow`、`GenerationWorkflow`、`EditingWorkflow` 已优先从 `WorkflowRunContext` 读取 episode/shot 选择，旧动态属性仍保留为兼容 fallback。
- `ProjectRepository` 已改为使用 `ProjectLayout` 创建目录和解析核心路径。
- CLI 中的 episode/shot parser 保留原函数名，但委托到 `workflows/selection.py`。

### 阶段 2：拆分 PregenWorkflow

已完成兼容式拆分：

- 新增 `WorkflowRunner` 和 `WorkflowNode`，统一处理节点日志、skip、force、异常记录、`state.mark_completed()` 和 `repo.save_state()`。
- `PregenWorkflow.run()` 已改为通过 `WorkflowRunner` 执行节点。
- `workflows/nodes/script_nodes.py`、`role_nodes.py`、`voice_nodes.py`、`static_asset_nodes.py`、`bgm_nodes.py` 已提供真实节点清单和 `WorkflowNode` 构建函数。
- `workflows/nodes/__init__.py` 已统一导出 `PREGEN_NODE_NAMES` 和 `build_pregen_nodes()`。

兼容说明：

- 节点内部业务方法暂时保留在 `PregenWorkflow` 中，节点模块负责注册和边界表达。
- 这样可以先稳定节点执行协议，再逐步把方法体迁入节点模块。
- 现有 CLI、节点名、node output JSON 路径和项目产物路径保持不变。

### 阶段 3：Generation 和 Editing 脱离 PregenWorkflow 继承

已完成：

- 新增 `PregenWorkflowDelegateMixin`，作为过渡期共享 helper 的组合式桥接。
- `GenerationWorkflow` 不再直接继承 `PregenWorkflow`，现在继承 `DynamicAssetNodeMixin, PregenWorkflowDelegateMixin`。
- `EditingWorkflow` 不再直接继承 `PregenWorkflow`，现在继承 `PregenWorkflowDelegateMixin`。
- 新增 generation episode-node 注册边界：
  - `storyboard_node.py`
  - `shot_bgm_node.py`
  - `ref_frame_node.py`
  - `shot_video_node.py`
  - `dynamic_asset_solidification_node.py`
- `GenerationWorkflow` 已通过 `build_generation_episode_nodes()` 查找并执行 episode 级节点。
- 新增 `autodrama.editing` 包：
  - `editing/planner.py`
  - `editing/subtitles.py`
  - `editing/ffmpeg.py`
- `EditingWorkflow` 中字幕时间格式、SRT/ASS 写入、ffmpeg filter、concat 文件、ffmpeg 执行等逻辑已委托到 `autodrama.editing` helper，旧方法名保留为兼容 wrapper。

兼容说明：

- generation/editing 仍组合一个 `PregenWorkflow` delegate 来复用尚未完全迁出的路径、媒体、脚本、角色和 storyboard helper。
- 这已经消除了业务 workflow 之间的直接继承关系，但共享 helper 后续仍可继续迁入更小的 service。

### 阶段 4：状态模型分层和 typed metadata

已完成兼容式分层：

- 新增 schema re-export 分包：
  - `core/domain.py`
  - `core/state.py`
  - `core/node_outputs.py`
  - `core/media.py`
  - `core/metadata.py`
- `core/schemas.py` 仍作为旧 import 主入口保留，避免大范围 import 迁移影响现有脚本。
- `ProjectMetadata`、`DynamicAssetRecord`、`GenerationTaskRegistry`、`GenerationChecklist` 已作为 typed wrapper 引入。

兼容说明：

- 旧 JSON 结构不迁移、不改字段、不删字段。
- 当前分层以 re-export 和 wrapper 为主，后续可按模块逐步把 schema 定义从 `schemas.py` 迁出。

### 阶段 5：Provider 层整理

已完成：

- 新增 `providers/http.py`：
  - `post_json()`
  - `request_id_from_response()`
  - `safe_headers()`
- 新增 `providers/media_refs.py`：
  - 本地文件转 data URL
  - `AssetRef` URL/data/asset URI 解析
- 新增 `providers/registry.py`，提供 `ProviderRegistry` 和 `ProviderRegistration`。
- `ProviderRouter` 已改为通过 `ProviderRegistry` 解析 text/image/video/audio/music provider，保留原有 provider 别名和 fallback 逻辑。
- RightCode、Seedream、Seedance 中部分重复的 data URL 和 request id 处理已委托到公共工具。

兼容说明：

- provider payload 构造逻辑没有重写。
- 供应商类、模型名、路由别名保持兼容。
- HTTP 公共化只先覆盖低风险 helper，避免改变错误消息和响应解析语义。

### 阶段 6：PromptStore 增强

已完成：

- `PromptStore` 支持 `strict=True`，默认开启。
- 模板文件不存在时明确抛出 `FileNotFoundError`。
- 模板渲染后如仍有 `{{variable}}` 未替换，会抛出包含模板名和变量名的 `ValueError`。
- 多传变量不会中断流程，但会写 debug 日志，便于后续排查 prompt-service 漂移。

### 阶段 7：文档和工程清理

已完成：

- `autodrama/README.md` 已明确源码路径为 `autodrama/src/autodrama`，直接命令需使用 `autodrama/src` 作为 `PYTHONPATH`。
- README 验证命令已拆成 `compileall` 和独立 smoke 命令，避免把 smoke 脚本误写进 `compileall` 命令。
- 新增 `scripts/smoke/refactor_boundaries_smoke.py`，验证：
  - generation/editing 不再是 `PregenWorkflow` 实例。
  - pregen/generation 节点注册清单没有漂移。
  - `ProviderRegistry` fake 路由覆盖 text/image/video/audio/music。
  - `PromptStore(strict=True)` 会拒绝未解析变量。

## 14. 本轮验证结果

已通过的验证命令：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_design_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/config_apikeys_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedream_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/rightcode_image_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/elevenlabs_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
```

`final_video_composition_smoke.py` 在当前 ffmpeg 构建中仍然提示 `subtitles` filter 不存在，并按原有逻辑自动降级为不烧录字幕后成功合成。这是既有环境能力限制，不是本次重构引入的行为变化。

## 15. 后续可选深化项

本轮已经完成所有规划阶段的兼容式落地，但仍有可继续深化的内部迁移：

- 把 `PregenWorkflow` 中 script、role、voice、static asset、BGM 的方法体逐步搬入对应 `workflows/nodes/*.py`。
- 把 delegate 中仍复用的 shared helper 迁入独立 service，最终移除 `PregenWorkflowDelegateMixin`。
- 将 `core/schemas.py` 的具体 schema 定义逐步迁移到 `domain.py`、`state.py`、`node_outputs.py`，让 `schemas.py` 只保留 re-export。
- 继续扩大 provider 公共 HTTP helper 的覆盖范围，但每次替换前应先补 payload smoke。
