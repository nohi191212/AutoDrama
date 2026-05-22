# AutoDrama 二期 B 阶段重构计划

## 1. 当前起点

本计划承接 `schedule2.md` 的 A 阶段节点迁移进展。

当前已经完成的相关前置工作：

- `PregenWorkflow` 不再直接承载已迁移 pregen 节点的大段方法体。
- `script_*` 节点已迁入 `workflows/nodes/script_nodes.py`。
- `role_extract`、`role_design` 已迁入 `workflows/nodes/role_nodes.py`。
- `role_voice_generation` 已迁入 `workflows/nodes/voice_nodes.py`，`role_voice_design` 保留兼容 runner。
- static asset 注册节点已迁入 `workflows/nodes/static_asset_nodes.py`，`role_appearance_design` 保留兼容 runner。
- `bgm_design`、`bgm_generation` 已迁入 `workflows/nodes/bgm_nodes.py`。
- 新增 repository：
  - `repositories/script_content_repo.py`
  - `repositories/role_design_repo.py`
  - `repositories/prop_design_repo.py`
- `PregenWorkflow` 仍保留大量 shared helper wrapper，供 generation/editing 通过 `PregenWorkflowDelegateMixin` 访问。

当前 B 阶段的核心遗留问题：

- `GenerationWorkflow` 仍继承 `PregenWorkflowDelegateMixin`，构造时创建 `_pregen` delegate。
- `EditingWorkflow` 仍继承 `PregenWorkflowDelegateMixin`，构造时创建 `_pregen` delegate。
- `PregenWorkflowDelegateMixin.__getattr__()` 会把 generation/editing 未定义的方法透明转发到 `PregenWorkflow`，共享依赖不够显式。
- `DynamicAssetNodeMixin` 依赖多组来自 pregen delegate 的 helper：
  - `_episode_stories`
  - `_active_episode_keys_in_order`
  - `_load_storyboard_episode`
  - `_save_storyboard_episode`
  - `_shot_ref_asset_refs`
  - `_shot_video_refs`
  - `_generate_shot_dialogue_audio`
  - `_write_generated_music`
  - `_write_generated_audio`
  - `_write_generated_video`
  - `_project_relative`
- `EditingWorkflow` 依赖多组来自 pregen delegate 的 helper：
  - `_iter_storyboard_episodes`
  - `_active_episode_keys_in_order`
  - `_expected_episode_keys`
  - `_load_storyboard_episode`
  - `_project_relative`

## 2. B 阶段目标

B 阶段目标是移除 `PregenWorkflowDelegateMixin`，让 generation/editing 组合明确的 shared service/repository/helper，而不是组合一个完整的 `PregenWorkflow`。

最终状态：

- `GenerationWorkflow` 不继承 `PregenWorkflowDelegateMixin`。
- `EditingWorkflow` 不继承 `PregenWorkflowDelegateMixin`。
- generation/editing 不再有 `_pregen` 属性。
- `workflows/delegation.py` 可删除。
- shared helper 有清晰模块归属。
- 构造函数外部兼容：
  - `GenerationWorkflow(repo=..., router=..., prompts=None)`
  - `EditingWorkflow(repo=..., router=..., prompts=None)`
- CLI 调用方式、节点名、JSON 输出结构、项目目录结构不变。

## 3. B 阶段非目标

以下事项不在 B 阶段中处理：

- 不重构 pregen 节点业务逻辑。
- 不做 schema 分包，schema 分包属于 C 阶段。
- 不改 provider HTTP/media 公共化，provider 深化属于 D 阶段。
- 不改 CLI 参数和命令行为。
- 不改 state JSON 格式。
- 不改 storyboard JSON 格式。
- 不引入数据库。
- 不引入 pytest。
- 不把 generation 并发模型改掉。
- 不重新设计 editing composer/ffmpeg 行为。

## 4. 执行原则

1. 每次只替换一组 helper 依赖。
2. 先新增 shared service，再迁移调用点，最后删 delegate。
3. 旧 helper wrapper 可以在 `PregenWorkflow` 里暂留一轮，但 generation/editing 不再通过 delegate 访问。
4. shared service 不能反向 import `PregenWorkflow`、`GenerationWorkflow`、`EditingWorkflow`。
5. `DynamicAssetNodeMixin` 要么通过 workflow 上的显式 attributes 访问 shared service，要么改为调用自身小 wrapper；不能依赖 `__getattr__`。
6. 所有新增 smoke 放在 `scripts/smoke/`。
7. agent 写的临时输出放 `.tmp/`。
8. 不运行 pytest。

## 5. 阶段 B0：依赖基线和边界 smoke

目标：在改动前固定 delegate 依赖清单和删除目标。

工作项：

- 新增或扩展 `scripts/smoke/shared_services_boundary_smoke.py`。
- 初始版本先检查：
  - `GenerationWorkflow` 不继承 `PregenWorkflow`。
  - `EditingWorkflow` 不继承 `PregenWorkflow`。
  - 当前仍存在 `_pregen` delegate，标记为待删除。
  - `WorkflowSharedServices` 尚未存在时允许失败项以明确提示，或在 B1 后启用强检查。
- 新增依赖清单文档：
  - 可写入 `docs/refactor/shared_helper_dependencies.md`。
  - 列出 generation/editing/dynamic_assets 的 helper 调用点、目标 owner、迁移阶段。

当前已知调用点：

| 文件 | 当前 helper | 目标 owner |
| --- | --- | --- |
| `workflows/generation.py` | `_expected_episode_keys` | `workflows/shared.py` 或 `services/episode_selection.py` |
| `workflows/generation.py` | `_load_storyboard_episode` | `repositories/storyboard_repo.py` 经 shared facade 暴露 |
| `workflows/editing.py` | `_iter_storyboard_episodes` | `services/storyboard_assets.py` 或 shared facade |
| `workflows/editing.py` | `_project_relative` | `ProjectLayout` 经 shared facade 暴露 |
| `workflows/editing.py` | `_expected_episode_keys` | `workflows/shared.py` |
| `workflows/dynamic_assets.py` | `_episode_stories` | `ScriptContentRepository` 经 shared facade 暴露 |
| `workflows/dynamic_assets.py` | `_load_storyboard_episode` / `_save_storyboard_episode` | `StoryboardRepository` 经 shared facade 暴露 |
| `workflows/dynamic_assets.py` | `_shot_ref_asset_refs` / `_shot_video_refs` | `services/shot_reference_service.py` |
| `workflows/dynamic_assets.py` | `_generate_shot_dialogue_audio` | `services/dialogue_audio_service.py` |
| `workflows/dynamic_assets.py` | `_write_generated_*` | `MediaStore` |

验收标准：

- 有文档能说明所有 delegate helper 的目标 owner。
- 有 smoke 能在 B3 变成强约束。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/pregen_node_boundary_smoke.py
```

## 6. 阶段 B1：抽 WorkflowSharedServices

目标：把三个 workflow 的 shared 初始化从 delegate 中拆出来。

新增文件：

- `autodrama/src/autodrama/workflows/shared.py`

建议结构：

```text
WorkflowSharedServices
  repo
  settings
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
  script_contents
  role_designs
  prop_designs
```

建议 API：

```python
class WorkflowSharedServices:
    @classmethod
    def build(
        cls,
        *,
        repo: ProjectRepository,
        router: ProviderRouter,
        prompts: PromptStore | None = None,
    ) -> WorkflowSharedServices:
        ...

    def bind_to(self, workflow: object) -> None:
        ...
```

迁移策略：

1. 新增 `WorkflowSharedServices.build()`，复用当前 `PregenWorkflow.__init__()` 的初始化逻辑。
2. `PregenWorkflow.__init__()` 改为构建 shared services，并把 attributes 绑定到 self。
3. `GenerationWorkflow.__init__()` 仍可以临时调用 delegate，但先允许接收/构建 shared services。
4. `EditingWorkflow.__init__()` 同上。
5. 暂时保留 `PregenWorkflowDelegateMixin`，但让它内部也使用 `WorkflowSharedServices`，避免初始化逻辑继续复制。

风险点：

- `PregenWorkflow` 当前支持轻量 fake repo，如果 repo 没有 `layout/settings`，会用默认 `Settings + ProjectLayout` 补齐；B1 需要保留这个兼容。
- `PromptStore` 必须保持默认 strict 行为不漂移。
- `WorkflowRunner` logger 使用保持一致。

验收标准：

- `PregenWorkflow`、`GenerationWorkflow`、`EditingWorkflow` 共享初始化逻辑。
- 外部构造函数签名不变。
- `refactor_boundaries_smoke.py` 仍通过。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
```

## 7. 阶段 B2：抽 episode/storyboard/layout shared helper

目标：先迁出低风险、纯路径和 repository helper，让 generation/editing 不再通过 delegate 获得基础项目 helper。

新增建议：

- `autodrama/src/autodrama/services/workflow_episode_service.py`
- `autodrama/src/autodrama/services/storyboard_assets.py`

也可以先放在 `workflows/shared.py` 内，待稳定后再拆服务。推荐先以最少文件落地，避免阶段内过度抽象。

候选 API：

```python
class WorkflowEpisodeService:
    def expected_episode_keys(state: ProjectState) -> list[str]
    def active_episode_keys_in_order(state: ProjectState, context: WorkflowRunContext | None, active_episode_keys: set[str] | None) -> list[str]
    def episode_stories(project_dir: Path, state: ProjectState) -> dict[str, str]
    def novel_full_contents(project_dir: Path, state: ProjectState, episode_keys: list[str] | None = None, allow_missing: bool = False) -> dict[str, str]

class StoryboardAssetService:
    def load_episode(project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput
    def save_episode(project_dir: Path, episode: StoryboardEpisodeOutput) -> None
    def iter_episodes(project_dir: Path, state: ProjectState, episode_keys: list[str]) -> list[StoryboardEpisodeOutput]
    def project_relative(project_dir: Path, path: Path) -> str
```

迁移策略：

1. 在 `PregenWorkflow` 中保留 wrapper：
   - `_expected_episode_keys`
   - `_active_episode_keys_in_order`
   - `_episode_stories`
   - `_novel_full_contents`
   - `_load_storyboard_episode`
   - `_save_storyboard_episode`
   - `_iter_storyboard_episodes`
   - `_project_relative`
2. wrapper 内改为调用 shared service。
3. `GenerationWorkflow` 增加显式 wrapper 或直接调用 service，避免 fallback 到 `__getattr__`。
4. `EditingWorkflow` 增加显式 wrapper 或直接调用 service，避免 fallback 到 `__getattr__`。
5. `DynamicAssetNodeMixin` 保持方法名不变，但这些方法要在 `GenerationWorkflow` 上显式存在。

风险点：

- `selected_episode_keys` 的顺序必须保持脚本顺序。
- generation/editing 对 `WorkflowRunContext.selected_episode_keys` 的处理不能漂移。
- storyboard 读写路径必须保持 `shots/{episode_key}.json`。

验收标准：

- `rg "_expected_episode_keys|_load_storyboard_episode|_save_storyboard_episode|_iter_storyboard_episodes|_project_relative" workflows/generation.py workflows/editing.py workflows/dynamic_assets.py` 的调用仍可存在，但解析到 generation/editing 自身或 shared service，不依赖 delegate。
- old project storyboard JSON 能继续 load。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
```

## 8. 阶段 B3：抽 shot reference service

目标：把镜头参考素材解析从 `PregenWorkflow` 移到独立 service。

新增文件：

- `autodrama/src/autodrama/services/shot_reference_service.py`

迁移对象：

- `_shot_ref_asset_refs`
- `_shot_video_refs`
- `_video_reference_mode`
- `_previous_shot`

建议 API：

```python
class ShotReferenceService:
    def ref_asset_refs(project_dir: Path, state: ProjectState, shot: StoryboardShot) -> list[AssetRef]
    def video_refs(
        project_dir: Path,
        state: ProjectState,
        shot: StoryboardShot,
        *,
        provider=None,
        episode: StoryboardEpisodeOutput | None = None,
    ) -> list[AssetRef]
```

迁移策略：

1. 先新增 service，复制现有行为。
2. `PregenWorkflow._shot_ref_asset_refs()` / `_shot_video_refs()` 改为 wrapper。
3. `GenerationWorkflow` 显式持有 `shot_references`。
4. `DynamicAssetNodeMixin` 调用点可保持 `_shot_ref_asset_refs()` / `_shot_video_refs()`，但方法应在 `GenerationWorkflow` 上显式定义，转到 service。

风险点：

- `start_frame_source=previous_shot_last_frame` 的错误信息和检查顺序不能漂移。
- `video_reference_mode` 的 provider options 兼容键不能少：
  - `video_reference_mode`
  - `seedance_video_reference_mode`
  - `video_refs_mode`
- AssetRef 顺序不能变：
  - ref frame
  - layout/role/prop refs
  - dialogue audio refs
  - previous shot last frame 特殊路径提前返回

验收标准：

- shot ref 相关 smoke 通过。
- provider payload smoke 中 reference 顺序不变。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_video_inheritance_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/ref_frame_image_provider_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
```

## 9. 阶段 B4：抽 dialogue audio service

目标：把镜头对白音频生成从 `PregenWorkflow` 移到独立 service，供 dynamic asset workflow 使用。

新增文件：

- `autodrama/src/autodrama/services/dialogue_audio_service.py`

迁移对象：

- `_role_for_dialogue_line`
- `_shot_dialogue_emotion`
- `_shot_dialogue_role_audio`
- `_generate_shot_dialogue_audio`
- 需要复用的 voice synthesis helper：
  - `_role_synthesis_voice`
  - `_role_synthesis_resource_id`
  - `_role_emotion_synthesis_plan`

注意：

- A3 已把 role voice generation 的核心 helper 放入 `RoleVoiceGenerationNode`。
- B4 不应让 `dialogue_audio_service.py` import node class 作为业务依赖。
- 如果复用 synthesis 逻辑，推荐抽到 `services/voice_generation_service.py` 或 `services/voice_synthesis.py`，再让 node 和 dialogue service 同时使用。

建议结构：

```text
services/voice_synthesis.py
  role_synthesis_voice()
  role_synthesis_resource_id()
  role_emotion_synthesis_plan()

services/dialogue_audio_service.py
  DialogueAudioService
    generate_shot_dialogue_audio()
```

迁移策略：

1. 先抽 pure helper 到 `voice_synthesis.py`。
2. 更新 `RoleVoiceGenerationNode` 使用 `voice_synthesis.py`。
3. 新增 `DialogueAudioService`，复制 `_generate_shot_dialogue_audio` 行为。
4. `PregenWorkflow._generate_shot_dialogue_audio()` 改为 wrapper。
5. `GenerationWorkflow` 显式持有 `dialogue_audio`，并显式定义 `_generate_shot_dialogue_audio()` wrapper。

风险点：

- speaker 解析中文冒号/英文冒号逻辑不能漂移。
- 单角色镜头 fallback 不能漂移。
- 情绪关键词匹配顺序不能漂移。
- shot dialogue audio asset path 保持：
  - `assets/audios/shot_dialogues/{asset_id}.{ext}`
- provider metadata 字段不能漂移，尤其：
  - `node_name`
  - `project_id`
  - `episode_key`
  - `shot_id`
  - `line_index`
  - `role_id`
  - `audio_id`
  - `asset_id`
  - `emotion`
  - `resource_id`
  - `target_model`

验收标准：

- shot dialogue audio smoke 或 dynamic fake smoke 通过。
- `DynamicAssetNodeMixin` 不再通过 delegate 访问 dialogue audio helper。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/workflow_synthesis_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/volcengine_voice_syn_smoke.py
```

说明：`volcengine_voice_syn_smoke.py` 会访问真实网络；如果沙箱网络受限，需要按权限流程请求联网运行。

## 10. 阶段 B5：抽 media write wrappers 并替换 DynamicAssetNodeMixin 依赖

目标：让 dynamic asset 生成直接使用 `MediaStore` 或 shared facade，不再通过 pregen wrapper。

迁移对象：

- `_write_generated_music`
- `_write_generated_audio`
- `_write_generated_video`
- `_image_asset_path`
- `_music_asset_path`
- `_audio_asset_path`
- `_video_asset_path`
- `_existing_project_file`

策略：

1. `GenerationWorkflow` 明确暴露这些 wrapper，内部调用 `layout` / `media_store`。
2. 或者改 `DynamicAssetNodeMixin` 内部直接调用：
   - `self.layout.*`
   - `self.media_store.*`
3. 若改 mixin，控制 diff，只替换 helper 调用，不改生成逻辑。

风险点：

- 生成路径不能漂移：
  - `assets/audios/shot_bgms/*`
  - `assets/images/ref_frames/*`
  - `assets/videos/shots/*`
  - `assets/audios/shot_dialogues/*`
- `existing_project_file()` 的空文件检查不能丢。

验收标准：

- `DynamicAssetNodeMixin` 不再需要 pregen delegate。
- generation 全链路 smoke 通过。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
```

## 11. 阶段 B6：替换 EditingWorkflow delegate 依赖

目标：`EditingWorkflow` 不再继承 delegate，通过 shared services 直接访问项目布局、storyboard、episode selection。

迁移对象：

- `_iter_storyboard_episodes`
- `_active_episode_keys_in_order`
- `_expected_episode_keys`
- `_load_storyboard_episode`
- `_project_relative`
- editing path helper：
  - `_edit_plan_path`
  - `_episode_output_path`
  - `_subtitle_srt_path`
  - `_subtitle_ass_path`
  - `_editing_tmp_dir`

建议：

- editing path helper 可以直接留在 `EditingWorkflow`，内部调用 `ProjectLayout`：
  - `layout.edit_plan_path`
  - `layout.episode_output_path`
  - `layout.subtitle_srt_path`
  - `layout.subtitle_ass_path`
  - `layout.editing_tmp_dir`
- storyboard iteration 调用 shared storyboard service。

风险点：

- `edit_plan_generation` 的输出路径字段不能漂移。
- final composition 的字幕和 episode output 路径不能漂移。
- active episode 过滤顺序不能漂移。

验收标准：

- `EditingWorkflow` 不继承 delegate 后，editing smoke 通过。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
```

## 12. 阶段 B7：删除 PregenWorkflowDelegateMixin

目标：彻底删除 delegate。

删除条件：

```powershell
rg "PregenWorkflowDelegateMixin|_pregen|__getattr__" autodrama/src/autodrama
```

应只剩无业务依赖，或完全无结果。

工作项：

1. `GenerationWorkflow` 移除 `PregenWorkflowDelegateMixin` 继承。
2. `EditingWorkflow` 移除 `PregenWorkflowDelegateMixin` 继承。
3. 删除 `workflows/delegation.py`。
4. 更新 `refactor_boundaries_smoke.py`：
   - generation/editing 不继承 `PregenWorkflow`。
   - generation/editing 不继承 `PregenWorkflowDelegateMixin`。
   - generation/editing 没有 `_pregen`。
   - `WorkflowSharedServices` 存在。
5. 更新 `scripts/smoke/shared_services_boundary_smoke.py` 为强检查。

验收标准：

- `rg "PregenWorkflowDelegateMixin|_pregen|__getattr__" autodrama/src/autodrama` 无业务结果。
- `GenerationWorkflow` 和 `EditingWorkflow` 的依赖从构造函数和 attributes 可直接读出。
- 所有基础/generation/editing smoke 通过。

建议验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shared_services_boundary_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
```

## 13. 推荐执行顺序

推荐顺序：

1. B0：补 shared helper 依赖文档和边界 smoke。
2. B1：新增 `WorkflowSharedServices`，统一初始化。
3. B2：迁移 episode/storyboard/layout 基础 helper。
4. B3：迁移 shot reference service。
5. B4：迁移 dialogue audio service 和 voice synthesis pure helper。
6. B5：替换 media write/path wrapper 依赖。
7. B6：替换 EditingWorkflow delegate 依赖。
8. B7：删除 `PregenWorkflowDelegateMixin`。

原因：

- B2 是低风险基础设施，先做能降低后续工作耦合。
- B3/B4 影响 generation 视频和音频 reference，风险高于路径 helper，放在基础 helper 之后。
- EditingWorkflow 依赖面较小，但 final composition 验证成本高，适合在 shared helper 稳定后处理。
- delegate 删除必须最后做。

## 14. 每阶段完成标准

每个阶段完成时至少满足：

- `compileall` 通过。
- 相关 smoke 通过。
- 不运行 pytest。
- CLI 行为不变。
- 节点名不变。
- state JSON 结构不变。
- node output JSON 结构不变。
- storyboard JSON 路径和内容结构不变。
- 媒体输出路径不变。
- `git diff` 没有无关格式化和大范围机械 churn。

## 15. B 阶段最终验证命令

推荐在 B7 后运行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shared_services_boundary_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/pregen_node_boundary_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/project_layout_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_video_inheritance_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/edit_plan_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/final_video_composition_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/workflow_synthesis_smoke.py
```

可选 provider payload 验证：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/ref_frame_image_provider_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/rightcode_image_payload_smoke.py
```

真实网络 provider 验证按需运行：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/volcengine_voice_syn_smoke.py
```

## 16. B 阶段完成定义

B 阶段完成时应满足：

- `workflows/delegation.py` 被删除。
- `GenerationWorkflow` 不继承 `PregenWorkflowDelegateMixin`。
- `EditingWorkflow` 不继承 `PregenWorkflowDelegateMixin`。
- 代码中无 `_pregen` delegate 业务依赖。
- shared initialization 位于 `workflows/shared.py` 或等价清晰 owner。
- generation/editing 所需 helper 由明确 service/repository 提供。
- dynamic asset 相关 reference、dialogue audio、media write helper 不再通过 pregen delegate。
- editing 相关 storyboard/path helper 不再通过 pregen delegate。
- B 阶段最终验证命令通过。
