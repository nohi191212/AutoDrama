# AutoDrama 二期深化重构计划

## 1. 当前起点

本计划承接 `schedule.md` 的第一轮兼容式重构成果。

第一轮已经完成：

- 固定非 pytest 验证基线。
- 抽出 `WorkflowRunContext`、`ProjectLayout`、`MediaStore`、`StoryboardRepository`、`workflows/selection.py`。
- `PregenWorkflow.run()` 已接入 `WorkflowRunner`。
- `workflows/nodes/*` 已建立 pregen 和 generation 的节点注册边界。
- `GenerationWorkflow`、`EditingWorkflow` 已不再直接继承 `PregenWorkflow`，改用 `PregenWorkflowDelegateMixin` 过渡式组合复用。
- `autodrama.editing` 已拆出 `planner.py`、`subtitles.py`、`ffmpeg.py`。
- `ProviderRouter` 已接入 `ProviderRegistry`。
- `PromptStore` 已启用 strict 变量检查。
- `core/domain.py`、`core/state.py`、`core/node_outputs.py`、`core/media.py`、`core/metadata.py` 已作为兼容分层入口建立。

第一轮的关键取舍是：优先建立边界和兼容委托，不一次性搬空大文件中的业务方法体。因此二期的目标不是重新设计功能，而是把第一轮留下的过渡层逐步收束掉。

## 2. 二期目标

二期目标是把“兼容式边界”推进为“真实模块所有权”：

1. 把 `PregenWorkflow` 中的节点方法体逐步迁入对应 `workflows/nodes/*.py`。
2. 把 `PregenWorkflowDelegateMixin` 依赖的 shared helper 拆成独立 service/repository/helper，最终移除 delegate。
3. 把 `core/schemas.py` 中的真实 schema 定义迁移到分包，让 `schemas.py` 只做 re-export。
4. 继续整理 provider 公共 HTTP/media 逻辑，但每个 provider 的 payload 必须先有 smoke 覆盖。
5. 保持 CLI、节点名、JSON 结构、输出路径、旧项目 resume 兼容。

## 3. 二期非目标

以下事项不纳入二期，避免把结构重构和功能变更混在一起：

- 不改 CLI 参数和命令行为。
- 不改 prompt 内容和 prompt 文件名。
- 不改现有 JSON 项目布局。
- 不做状态 JSON 迁移。
- 不改变 provider 默认路由和模型名。
- 不把 workflow 并发化。
- 不引入数据库。
- 不引入 pytest。
- 不把 fake provider 行为改成更复杂的模拟器。

## 4. 执行原则

1. 每次只迁移一个节点组或一个 provider 族。
2. 先补 smoke，再迁移实现。
3. 旧 import 路径必须保留 re-export。
4. 旧 wrapper 方法可以保留一轮，用于降低调用点迁移风险。
5. 每个阶段完成后至少运行 `compileall` 和相关 smoke。
6. 如发现行为漂移，优先回退本阶段改动，而不是扩大修补范围。
7. 不用 pytest；新增验证脚本放在 `scripts/smoke/`，临时输出放 `.tmp/`。

## 5. 阶段 A：Pregen 节点真实迁移

目标：把 `PregenWorkflow` 中的节点方法体迁入 `workflows/nodes/*.py`，让 `PregenWorkflow` 逐步变成 orchestration facade。

### A0：迁移前依赖盘点

工作项：

- 为 `PregenWorkflow` 当前所有 `_run_*` 节点方法列出依赖：
  - 使用的 service。
  - 使用的 repo/layout/media helper。
  - 使用的私有工具方法。
  - 修改的 state 字段。
  - 写出的 node output JSON。
- 生成一份节点依赖表，放到 `schedule2.md` 或独立 `docs/refactor/node_dependencies.md`。
- 标记不能马上迁出的 shared helper，例如：
  - `_expected_episode_keys`
  - `_load_script_contents`
  - `_role_lookup`
  - `_write_generated_*`
  - `_shot_ref_asset_refs`
  - `_generate_shot_dialogue_audio`

验收标准：

- 每个 pregen 节点都有明确 owner 模块。
- 能区分“节点业务逻辑”和“共享 helper”。
- 没有开始大规模搬代码前就能看清依赖风险。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
```

### A1：迁移 script 节点

目标文件：

- `autodrama/src/autodrama/workflows/nodes/script_nodes.py`

迁移对象：

- `_run_script_outline`
- `_run_script_novel`
- `_run_script_novel_extract`
- 脚本内容读写相关 helper，必要时拆到新的 repository：
  - `repositories/script_content_repo.py`

推荐结构：

```text
workflows/nodes/script_nodes.py
  ScriptOutlineNode
  ScriptNovelNode
  ScriptNovelExtractNode
  build_script_nodes()

repositories/script_content_repo.py
  ScriptContentRepository
```

迁移策略：

- 先新增 node class，构造函数接收 `repo`、`layout`、`script_service`、`logger`。
- `PregenWorkflow._run_script_outline()` 先保留 wrapper，内部调用 node class。
- `build_script_nodes()` 改为返回 node class 的 `WorkflowNode` 包装。
- 旧 JSON 路径保持：
  - `assets/json/scripts/outlines/{episode_key}.json`
  - `assets/json/scripts/novel_full/{episode_key}.json`
  - `assets/json/scripts/novel_extract/{episode_key}.json`
  - `assets/json/nodes/script_*.json`

风险点：

- `script_novel` 的 legacy path fallback。
- `script_novel_extract` batch 顺序。
- `state.script.*` 中 `False` 与路径字符串的兼容。

验收标准：

- `PregenWorkflow` 中 script 节点方法体只剩 wrapper 或删除。
- `script_nodes.py` 拥有 script 节点业务逻辑。
- 旧项目的 script JSON 能被继续读取。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/script_novel_serial_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/script_novel_extract_batch_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/script_chapter_pairing_prompt_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_design_scoping_smoke.py
```

### A2：迁移 role 节点

目标文件：

- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`

可新增：

- `repositories/role_design_repo.py`
- `services/role_design_merge.py`

迁移对象：

- `_run_role_extract`
- `_run_role_design`
- role design 文件读写。
- role design merge/hydrate 逻辑。
- episode-scoped role rerun 逻辑。

迁移策略：

- 先拆文件读写：
  - `_role_design_json_path`
  - `_role_design_relative_path`
  - `_save_role_design_item`
  - `_load_role_design_item`
  - `_load_role_design_item_for_role`
- 再拆 role_design merge 逻辑：
  - `_select_role_design_item`
  - `_merge_role_extract_into_design`
  - `_ordered_role_design_items`
  - `_apply_role_design_item`
  - `_hydrate_roles_from_design_files`
- 最后迁移 `_run_role_extract` 和 `_run_role_design`。

风险点：

- `run pregen --only role_design --episodes ...` 的选择语义。
- 旧 `assets/json/nodes/role_design.json` 与 per-role design file 的 merge。
- 未选中角色不能被清空。

验收标准：

- episode-scoped role rerun 行为不变。
- `state.roles` 顺序和内容不漂移。
- `assets/json/roles/{role_id}.json` 路径保持不变。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_design_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
```

### A3：迁移 voice 节点

目标文件：

- `autodrama/src/autodrama/workflows/nodes/voice_nodes.py`

可新增：

- `services/voice_design_service.py`
- `services/voice_generation_service.py`
- `services/voice_catalog.py`

迁移对象：

- `_run_role_voice_design`
- `_run_role_voice_generation`
- `_run_role_voice_synthesis_generation`
- `_run_role_voice_design_clone_generation`
- `_generate_designed_voice`
- `_generate_cloned_voice`
- `_generate_reused_voice`
- `_generate_synthesized_voice`
- speaker catalog 和 voice binding helper。

迁移策略：

- 先把 pure helper 搬出：
  - `_available_speakers`
  - `_available_speakers_for_prompt`
  - `_speaker_lookup`
  - `_clean_optional_text`
  - `_voice_candidate_priority`
  - `_default_voice_sample_text`
- 再把写 preview audio 的路径和 media 写入委托到 `MediaStore`。
- 最后迁移 node run 方法。

风险点：

- 火山 Seed TTS 的 direct synthesis 策略。
- Qwen/CosyVoice 的 design/clone/reuse 策略。
- `RoleAudio` 中 voice 字段和 asset 字段的兼容。
- provider 能力差异：`available_speakers`、`synthesize_speech`、`generate_voice`。

验收标准：

- fake provider voice smoke 通过。
- 火山 voice catalog smoke 通过。
- role audio 的 asset path 保持旧结构。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/workflow_synthesis_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/volcengine_voice_catalog_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/volcengine_voice_syn_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
```

### A4：迁移 static asset 节点

目标文件：

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`

可新增：

- `services/static_asset_generation.py`
- `services/asset_reference_resolver.py`
- `repositories/prop_design_repo.py`

迁移对象：

- `_run_role_appearance_design`
- `_run_role_appearance_generation`
- `_run_prop_extract`
- `_run_prop_design`
- `_run_prop_generation`
- `_run_script_compress`
- `_run_layout_design`
- `_run_layout_dedupe_review`
- `_run_layout_image_generation`
- prop variant/reference helper。

迁移策略：

- 先把 prop design 文件读写拆到 repository。
- 再把 image/video asset 写入统一通过 `MediaStore`。
- 然后迁移 role appearance、prop、layout 三组节点。
- 每迁一组就跑对应 smoke。

风险点：

- role-bound prop 引用。
- prop variant base/normal 状态排序。
- ref image 传递给 image provider 的顺序。
- role appearance intro video 生成。

验收标准：

- role、prop、layout 图片路径不变。
- role intro video 路径不变。
- provider payload smoke 不变。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_appearance_resume_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/prop_design_state_shape_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/prop_variant_reference_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/rightcode_image_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
```

### A5：迁移 BGM 节点

目标文件：

- `autodrama/src/autodrama/workflows/nodes/bgm_nodes.py`

可新增：

- `services/bgm_generation_service.py`

迁移对象：

- `_run_bgm_design`
- `_run_bgm_generation`

风险点：

- BGM 数量校验。
- MiniMax/Bailian/ElevenLabs provider 差异。
- `state.bgms` 与 `StaticAssetGenerationOutput` 的兼容。

验收标准：

- `assets/audios/bgms/*` 路径不变。
- `assets/json/nodes/bgm_design.json` 和 `bgm_generation.json` 结构不变。
- BGM 计数 smoke 通过。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/bgm_count_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/elevenlabs_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
```

## 6. 阶段 B：移除 PregenWorkflowDelegateMixin

目标：让 generation/editing 组合真正的共享 service，而不是组合一个 `PregenWorkflow` delegate。

### B1：抽 WorkflowSharedServices

新增建议：

- `workflows/shared.py`

建议内容：

```text
WorkflowSharedServices
  repo
  layout
  router
  prompts
  script_service
  role_service
  asset_service
  storyboard_service
  media_store
  storyboards
  runner
```

迁移策略：

- `PregenWorkflow`、`GenerationWorkflow`、`EditingWorkflow` 都接收或构建同一个 `WorkflowSharedServices`。
- 保留构造函数原签名：
  - `repo`
  - `router`
  - `prompts=None`
- 外部调用不变。

验收标准：

- 三个 workflow 不再各自重复初始化 service。
- `PregenWorkflowDelegateMixin` 中的初始化逻辑可以缩小。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
```

### B2：抽 shared helper 到 service/repository

优先拆出：

- `repositories/script_content_repo.py`
- `repositories/role_design_repo.py`
- `repositories/prop_design_repo.py`
- `services/storyboard_assets.py`
- `services/shot_reference_service.py`
- `services/dialogue_audio_service.py`

迁移范围：

- `GenerationWorkflow` 需要的 helper：
  - `_episode_stories`
  - `_load_storyboard_episode`
  - `_save_storyboard_episode`
  - `_shot_ref_asset_refs`
  - `_shot_video_refs`
  - `_generate_shot_dialogue_audio`
  - `_write_generated_*`
- `EditingWorkflow` 需要的 helper：
  - `_iter_storyboard_episodes`
  - `_load_storyboard_episode`
  - `_project_relative`
  - `_expected_episode_keys`

验收标准：

- generation/editing 访问 shared helper 不经过 `PregenWorkflow`。
- shared helper 有明确模块名和 owner。
- `__getattr__` 委托可删除或只剩临时兼容。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
```

### B3：删除 PregenWorkflowDelegateMixin

删除条件：

- `rg "PregenWorkflowDelegateMixin|_pregen|__getattr__" autodrama/src/autodrama` 没有业务依赖。
- `GenerationWorkflow` 和 `EditingWorkflow` 的构造函数仍兼容。
- `refactor_boundaries_smoke.py` 更新为检查：
  - generation/editing 不继承 `PregenWorkflow`。
  - generation/editing 不包含 `_pregen` delegate。
  - shared services 存在且类型符合预期。

验收标准：

- 删除 `workflows/delegation.py`。
- 所有 smoke 通过。
- `GenerationWorkflow`、`EditingWorkflow` 的依赖关系可从构造函数和 attributes 直接看出来。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
```

## 7. 阶段 C：schema 真实分包

目标：把 `core/schemas.py` 从“所有定义所在地”变成兼容 re-export 文件。

### C1：schema 依赖盘点

工作项：

- 列出 `schemas.py` 中所有类。
- 标记所属目标模块：
  - `domain.py`
  - `state.py`
  - `node_outputs.py`
  - `media.py`
  - `metadata.py`
  - 如 editing schema 需要，可新增 `editing/schemas.py` 或 `core/editing.py`
- 检查类之间引用方向，避免循环 import。

验收标准：

- 有明确迁移顺序。
- 不开始迁移前就知道循环依赖风险。

### C2：迁移 domain schema

目标模块：

- `core/domain.py`

候选类：

- `Role`
- `RoleAudio`
- `RoleAppearance`
- `Prop`
- `Layout`
- `BGM`
- `StoryboardShot`
- `StoryboardEpisodeOutput`
- `ShotBGMAsset`
- `ShotDialogueAudioAsset`

策略：

- 迁移定义到 `domain.py`。
- `schemas.py` 从 `domain.py` import 并 re-export。
- 业务代码暂时不必大范围改 import。

验收标准：

- `ProjectState.model_validate_json()` 行为不变。
- `model_dump(mode="json")` 输出不变。

### C3：迁移 state schema

目标模块：

- `core/state.py`

候选类：

- `ScriptBundle`
- `BudgetState`
- `ProjectState`

风险点：

- `ProjectState` 引用大量 domain 类。
- `metadata` 仍是 `dict[str, Any]`，不能强制改成 `ProjectMetadata`。

验收标准：

- 旧 state JSON 可读。
- `completed_nodes`、`current_node`、`metadata` 行为不变。

### C4：迁移 node output schema

目标模块：

- `core/node_outputs.py`

候选类：

- `ScriptOutlineOutput`
- `ScriptNovelOutput`
- `ScriptNovelEpisodeOutput`
- `ScriptNovelExtractOutput`
- `ScriptNovelExtractBatchOutput`
- `RoleExtractOutput`
- `RoleDesignOutput`
- `RoleVoiceDesignOutput`
- `RoleVoiceGenerationOutput`
- `RoleAppearanceDesignOutput`
- `PropDesignOutput`
- `ScriptCompressOutput`
- `LayoutDesignOutput`
- `LayoutDedupeReviewOutput`
- `BGMDesignOutput`
- `StoryboardGenerationOutput`
- `ShotBGMGenerationOutput`
- `RefFrameGenerationOutput`
- `ShotVideoGenerationOutput`
- `DynamicAssetSolidificationOutput`
- `StaticAssetGenerationOutput`

验收标准：

- node output JSON 结构不变。
- fake workflow smoke 全部通过。

### C5：让 schemas.py 只做 re-export

完成条件：

- `schemas.py` 中不再定义实际模型类。
- `schemas.py` 只包含 import 和 `__all__`。
- 老 import 路径 `from autodrama.core.schemas import ProjectState` 继续工作。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
```

## 8. 阶段 D：provider 公共化深化

目标：减少 provider 重复代码，同时保证请求 payload 不漂移。

### D0：补 provider payload/golden smoke

优先补齐：

- Wanxiang image payload smoke。
- Wanxiang video payload smoke。
- Bailian music payload smoke。
- Qwen TTS payload smoke。
- Volcengine Seed TTS payload smoke。
- Volcengine Seed ICL payload smoke。
- DeepSeek text payload smoke，如当前没有直接 payload 构造入口，可先验证 request metadata 和 endpoint。

原则：

- 每个 provider 的 HTTP 公共化前必须有 payload smoke。
- smoke 输出写 `.tmp/smoke/{provider_name}_payload/`。
- smoke 只检查本地 payload、endpoint、headers 脱敏，不发真实网络请求。

### D1：统一 HTTP JSON 调用

目标模块：

- `providers/http.py`

可扩展函数：

- `get_json()`
- `post_json()`
- `post_stream()`
- `parse_json_response()`
- `raise_for_provider_status()`
- `provider_timeout()`

迁移顺序：

1. RightCode image。
2. Seedream image。
3. Seedance video。
4. MiniMax music。
5. ElevenLabs music。
6. Wanxiang image/video。
7. Bailian music。
8. Qwen/DeepSeek text。

风险控制：

- 不统一 payload 构造，只统一 HTTP 收发。
- 不改变 provider-specific error message 中的重要字段。
- 每迁一个 provider 运行其 payload smoke。

### D2：统一 media reference 处理

目标模块：

- `providers/media_refs.py`

可扩展函数：

- `refs_to_urls()`
- `refs_to_data_urls()`
- `limit_refs()`
- `image_refs()`
- `audio_refs()`
- `video_refs()`
- `resolve_asset_uri()`

迁移对象：

- RightCode image refs。
- Seedream image refs。
- Seedance video first/last frame refs。
- Wanxiang image/video refs。
- Volcengine audio local sample refs。

验收标准：

- reference 顺序不变。
- 超出 provider 限制时的裁剪行为不变。
- 本地文件 mime 判断不变。

### D3：ProviderRouter 注册表增强

目标：

- `ProviderRegistry` 支持 alias、capability、purpose-aware factory。
- 注册逻辑可以拆出 `providers/register.py` 或 provider package 本地注册函数。

建议结构：

```text
providers/
  registry.py
  router.py
  registrations.py
```

验收标准：

- `ProviderRouter` 中 if/else 进一步减少。
- provider alias 仍兼容：
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

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/config_apikeys_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedream_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/rightcode_image_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/elevenlabs_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
```

## 9. 阶段 E：验证体系深化

目标：让二期大规模迁移有更强的非 pytest 回归保护。

### E1：新增边界 smoke

建议新增：

- `scripts/smoke/pregen_node_boundary_smoke.py`
  - 检查 pregen 节点由 node module 注册。
  - 检查 `PregenWorkflow` 中不再拥有已迁移节点的大段方法体，或 wrapper 指向 node class。
- `scripts/smoke/shared_services_boundary_smoke.py`
  - 检查 generation/editing 不依赖 `_pregen` delegate。
- `scripts/smoke/schema_reexport_smoke.py`
  - 检查 `core.schemas` 旧 import 可用。
  - 检查新分包 import 可用。
- `scripts/smoke/project_layout_contract_smoke.py`
  - 检查关键路径仍为旧目录结构。

### E2：新增旧项目兼容 smoke

建议：

- 在 `.tmp/smoke/fixtures/` 生成或维护一个最小旧格式项目。
- smoke 读取旧 `state.json`、旧 node output、旧 storyboard，确认新代码能 load。
- 不把大型媒体文件纳入 fixture。

候选脚本：

- `scripts/smoke/legacy_state_load_smoke.py`
- `scripts/smoke/legacy_storyboard_load_smoke.py`

### E3：验证命令分层

基础验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
```

Pregen 迁移验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_design_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
```

Generation 迁移验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
```

Editing 迁移验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
```

Provider 迁移验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/config_apikeys_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedream_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/rightcode_image_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/elevenlabs_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
```

## 10. 推荐执行顺序

推荐按以下顺序推进，避免高耦合阶段互相影响：

1. A0：完成 pregen 节点依赖盘点。
2. E1：补边界 smoke，先让迁移目标可被检查。
3. A1：迁移 script 节点。
4. A2：迁移 role 节点。
5. A5：迁移 BGM 节点。
6. A4：迁移 static asset 节点。
7. A3：迁移 voice 节点。
8. B1：抽 `WorkflowSharedServices`。
9. B2：迁移 shared helper。
10. B3：删除 `PregenWorkflowDelegateMixin`。
11. C1-C5：schema 真实分包。
12. D0-D3：provider 公共化深化。
13. E2-E3：旧项目兼容 smoke 和验证命令分层收尾。

说明：

- voice 节点放在 static asset 后面，是因为 provider 能力差异更多，风险高于 BGM 和 script。
- schema 真实分包放在 delegate 删除之后，是为了减少迁移期间循环 import 的变量。
- provider 深化放后面，是因为 payload 漂移风险需要更多 smoke 前置。

## 11. 每阶段完成标准

每个二期阶段都必须满足：

- `compileall` 通过。
- 相关 smoke 通过。
- CLI 调用方式不变。
- 节点名不变。
- node output JSON 路径不变。
- 项目输出媒体路径不变。
- 旧 import 路径继续工作。
- 未迁移模块仍有兼容 wrapper。
- `git diff` 中没有无关格式化和大范围机械 churn。

## 12. 风险清单

### 12.1 节点迁移导致状态写入漂移

风险：

- `state.current_node`、`completed_nodes`、`budget`、`metadata` 更新时机变化。

控制：

- 继续让 `WorkflowRunner` 统一 mark/save。
- 节点内部只负责业务 state 修改和 node output 写出。

### 12.2 文件路径漂移

风险：

- 迁移 repository/helper 后路径细节变化。

控制：

- 所有路径必须通过 `ProjectLayout`。
- 增加 `project_layout_contract_smoke.py`。

### 12.3 delegate 移除过早

风险：

- generation/editing 还隐式依赖 pregen helper。

控制：

- `rg "_pregen|PregenWorkflowDelegateMixin|__getattr__"` 必须作为 B3 删除前检查。
- 先抽 shared service，再删 delegate。

### 12.4 schema 循环 import

风险：

- `ProjectState`、node output、domain model 互相引用，拆包后形成循环。

控制：

- 先迁 domain，再迁 state，再迁 node outputs。
- 必要时使用 `from __future__ import annotations` 和局部 import。
- `schemas.py` 最后才变成纯 re-export。

### 12.5 provider payload 漂移

风险：

- 公共 HTTP/media helper 改变请求 body、headers、reference 顺序。

控制：

- 先补 payload smoke。
- 每次只迁一个 provider。
- smoke 比较关键字段，不调用真实网络。

## 13. 二期最终目标结构

目标结构示意：

```text
autodrama/src/autodrama/
  core/
    domain.py
    state.py
    node_outputs.py
    media.py
    metadata.py
    schemas.py          # only re-export
  repositories/
    project_layout.py
    project_repo.py
    script_content_repo.py
    role_design_repo.py
    prop_design_repo.py
    storyboard_repo.py
  services/
    media_store.py
    script_service.py
    role_service.py
    role_design_merge.py
    voice_design_service.py
    voice_generation_service.py
    asset_reference_resolver.py
    static_asset_generation.py
    bgm_generation_service.py
    storyboard_assets.py
    shot_reference_service.py
    dialogue_audio_service.py
  workflows/
    context.py
    runner.py
    selection.py
    shared.py
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
  providers/
    http.py
    media_refs.py
    registry.py
    registrations.py
    router.py
```

## 14. 二期完成定义

二期完成时应满足：

- `PregenWorkflow` 文件显著缩小，只保留 workflow facade 和少量兼容 wrapper。
- `GenerationWorkflow`、`EditingWorkflow` 不依赖 `_pregen` delegate。
- `PregenWorkflowDelegateMixin` 被删除。
- `core/schemas.py` 只做 re-export。
- provider 公共 HTTP/media helper 覆盖主要生产 provider。
- 新增边界 smoke 和旧项目兼容 smoke。
- 基础、pregen、generation、editing、provider 五类验证命令全部通过。

最终推荐验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
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
