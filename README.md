# AutoDrama

AutoDrama 是一个短剧自动生成工作流项目。它把输入故事大纲拆成剧集，生成脚本、角色、道具、场景、BGM 等可复用静态资产，再按剧集生成分镜、镜头 BGM、参考帧和镜头视频。

本仓库当前重点支持两段式流程：

1. `pregen`: 预生成脚本、角色、道具、场景、全局 BGM 等项目级资产。
2. `generation`: 按剧集生成动态镜头资产，包括 storyboard、shot BGM、reference frame、shot video 和动态资产固化记录。

Python 包采用嵌套 `src` 布局，源码位于 `autodrama/src/autodrama`。请从仓库根目录运行命令。

## 目录结构

```text
.
├── autodrama/
│   ├── pyproject.toml
│   ├── README.md
│   └── src/autodrama/
│       ├── cli.py
│       ├── config.py
│       ├── core/
│       ├── editing/
│       ├── providers/
│       ├── repositories/
│       ├── services/
│       └── workflows/
├── config.yaml.example
├── apikeys.yaml.example
├── inputs/
├── outputs/
├── run/
│   ├── start.cmd
│   └── dynamic_assets.cmd
├── scripts/
│   └── smoke/
└── docs/
```

主要目录说明：

- `autodrama/src/autodrama`: 主业务代码。
- `autodrama/src/autodrama/providers`: 文本、图像、音频、音乐、视频 Provider 适配器。
- `autodrama/src/autodrama/workflows`: `pregen`、`generation`、`editing` 等工作流。
- `autodrama/src/autodrama/workflows/nodes`: 各工作流节点的边界和实现。
- `inputs`: 输入故事大纲或小说素材。
- `outputs`: 项目状态、生成资产、日志和成片输出。
- `run`: Windows 快捷入口。
- `scripts/smoke`: 非 pytest 的定向验证脚本。
- `provider_docs`: 外部服务接口文档或调试资料。

## 环境要求

推荐使用仓库约定的 Python 环境：

```powershell
D:/miniforge3/envs/autodrama/python.exe
```

直接调用 Python 模块时，需要设置 `PYTHONPATH`：

```powershell
$env:PYTHONPATH="autodrama/src"
```

Windows 快捷脚本 `run\start.cmd` 和 `run\dynamic_assets.cmd` 会自动设置 `PYTHONPATH`，并优先读取 `config.yaml` 中的 `runtime.python.windows`。

项目依赖定义在 `autodrama/pyproject.toml`，核心依赖包括：

- `pydantic`
- `PyYAML`
- `dashscope`
- `httpx`
- `openai`

## 配置

第一次运行前复制示例配置：

```powershell
Copy-Item config.yaml.example config.yaml
Copy-Item apikeys.yaml.example apikeys.yaml
```

然后编辑 `config.yaml`：

- `project.id`: 项目 ID。建议固定，便于断点续跑。
- `project.title`: 项目标题。
- `project.script_outline_file`: 输入故事大纲文件，默认示例为 `./inputs/story_outline.md`。
- `project.episode_count`: 剧集数量。
- `project.episode_duration_seconds`: 单集目标时长。
- `project.bgm_count`: 全局 BGM 数量。
- `project.visual_style`: 画面风格，支持 `live_action`、`anime_2d`、`anime_3d`、`cg_animation`。
- `output.root_dir`: 输出目录，默认 `./outputs`。
- `providers`: 各 Provider 的 base URL、模型名和选项。
- `routing`: 不同能力和用途的 Provider 路由。

角色静态图像现在只使用 `role_full_body` 和 `role_multiview` 两类配置：

- `role_full_body`: 正面全身参考图，建议竖幅比例，例如 ToAPI `role_full_body_size: "1:2"` 或 RightCode `role_full_body_size: 1024x1536`。
- `role_multiview`: 三视图 + 绑定道具设计图，使用 full body 作为参考图，建议 16:9 横幅比例。

旧的 `role_portrait` / `portrait_prompt` 已移除，不再作为角色资产配置项或 prompt 字段。

编辑 `apikeys.yaml` 填入真实密钥。该文件应只保存在本地，不要提交到代码仓库。

支持的主要密钥项：

- `ALIYUN_API_KEY`
- `DEEPSEEK_API_KEY`
- `TOAPI_API_KEY`
- `RIGHTCODE_API_KEY`
- `MINIMAX_API_KEY`
- `MINIMAX_GROUP_ID`
- `ELEVENLABS_API_KEY`
- `VOLCENGINE_API_KEY`
- `VOLCENGINE_APP_ID`
- `VOLCENGINE_ACCESS_KEY`
- `VOLCENGINE_SECRET_KEY`

默认生产路由大致为：

- 文本脚本: `aliyun`
- 角色/道具/场景/分镜文本: `deepseek`
- 静态图像和参考帧: `toapi`
- 角色语音: `volcengine`
- 全局 BGM: `minimax`
- 镜头 BGM: `elevenlabs`
- 镜头视频: `volcengine`

本地验证或演示可以使用 `--fake`，不会调用真实外部服务。

## 快速开始

### 1. 初始化项目

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config config.yaml
```

也可以显式指定标题、输入文件和项目 ID：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config config.yaml --title "测试短片" --script-file inputs/story_outline.md --project-id review_demo
```

初始化后会在 `outputs/<project_id>` 下写入 `state.json`、`project.json` 和基础目录结构。

### 2. 运行预生成流程

使用 Windows 快捷脚本：

```powershell
run\start.cmd --config config.yaml --project <project_id>
```

等价的直接 CLI：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id>
```

`pregen` 默认运行到 `bgm_generation`，包含以下节点：

```text
script_outline
script_novel
script_novel_extract
role_extract_primary
role_extract_functional
role_extract
role_episode_key_audit
ambient_entity_extract
role_design
voice_select
role_voice_generation
role_full_body_generation
role_multiview_generation
role_intro_video_generation
prop_extract
prop_design
prop_generation
layout_design
layout_dedupe_review
layout_image_generation
bgm_design
bgm_generation
```

角色链的关键依赖顺序：

```text
role_extract
role_episode_key_audit
role_design
voice_select
role_voice_generation
role_full_body_generation
role_multiview_generation
role_intro_video_generation
```

`role_design` 按角色递归运行，只读取该角色 `episode_keys` 对应的完整正文，并输出声音需求、`sample_text`、`full_body_prompt`、三视图 + 道具 prompt、介绍视频 prompt 和角色绑定道具。`voice_select` 读取全局 `.assets/voice_catalog/<provider>/<model>/manifest.json`，按手工覆盖、有效缓存、文本 top 5 初筛、音频 judge 终选、catalog 启发式和 provider fallback 的优先级给角色绑定官方 `voice_type`；`role_voice_generation` 再使用该 `voice_type` 合成人物样例音频。`role_full_body_generation` 先生成自然正面全身参考图；`role_multiview_generation` 必须使用 full body 图作为参考，生成三视图 + 道具设计图；`role_intro_video_generation` 再使用 multiview 图作为参考生成角色介绍视频。功能角色如果 `has_dialogue=false` 不生成声音；功能角色默认跳过介绍视频。

`role_episode_key_audit` 在 `role_extract` 之后运行，会以 30 并发逐个检查角色 JSON 的 `episode_keys` 覆盖情况；如果发现遗漏，只向 `role_extract*`、已有 `role_design`、角色 JSON 和 state 角色记录追加缺失的 `episode_keys/source_chapters`，不会删除或重排原有条目。

### 3. 运行动态资产生成流程

只运行动态资产：

```powershell
run\start.cmd --generation --config config.yaml --project <project_id>
```

直接 CLI：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id>
```

动态流程默认包含：

```text
storyboard_generation
shot_bgm_generation
ref_frame_generation
shot_video_generation
dynamic_asset_solidification
```

### 4. 一次性运行预生成和动态资产

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id>
```

本地 fake provider 演示：

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id> --fake --force
```

跳过预生成，只跑动态资产：

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id> --skip-pregen
```

## 常用操作

使用直接 CLI 检查状态和节点输出：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli inspect state --config config.yaml --project <project_id>
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli inspect nodes --config config.yaml --project <project_id>
```

运行到指定节点：

```powershell
run\start.cmd --config config.yaml --project <project_id> --until layout_image_generation
run\start.cmd --generation --config config.yaml --project <project_id> --until ref_frame_generation
```

只运行一个节点：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only voice_select
run\start.cmd --config config.yaml --project <project_id> --only role_voice_generation
run\start.cmd --config config.yaml --project <project_id> --only role_full_body_generation
run\start.cmd --config config.yaml --project <project_id> --only role_multiview_generation
run\start.cmd --config config.yaml --project <project_id> --only role_intro_video_generation
run\start.cmd --generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes 1
```

按剧集选择：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only role_design --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only voice_select --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only role_voice_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only role_full_body_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only role_multiview_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only role_intro_video_generation --episodes 1 --force
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1,3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes episode_001,episode_003
```

`pregen --episodes` 只支持配合 `--only` 使用，当前支持 `role_design`、`voice_select`、`role_voice_generation`、`role_full_body_generation`、`role_multiview_generation`、`role_intro_video_generation`、`prop_design` 和 `prop_generation`。角色相关节点会按角色 `episode_keys` 过滤；如果角色缺少 `episode_keys`，会直接报错，不会退回加载全文。

全局音色 catalog 命令：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples --sample-emotion all
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-profiles
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog inspect --config config.yaml --provider volcengine
```

`voice-catalog build` 会刷新 provider speaker manifest；`--force-samples` 默认只为目标音色生成 `normal` 样例，避免全量 catalog 触发过多 TTS 请求；如果确实需要五情绪样例，可以加 `--sample-emotion all` 生成 `normal/angry/sad/happy/low`。`--force-profiles` 会调用 `routing.judge.voice_catalog_profile` 配置的 audio judge 生成自然语言听感画像，每个音色会单独落盘到 `.assets/voice_catalog/<provider>/<model>/profiles/<voice_type>.json`，同时回写 manifest；如需临时覆盖 judge，可加 `--judge-provider fake` 或其他已注册 judge。调试时可以加 `--voice-type <voice_type>` 或 `--limit 5` 控制范围。项目内 `voice_select` 会先用 `routing.text.voice_select` 根据角色和音色画像筛 top 5（未配置时复用 `routing.text.role`，失败时退回 catalog 启发式），样例齐全时再调用 `routing.judge.voice_select` 听音频终选。`voice_label` 只作为人类可读展示字段，落盘和合成 API 始终使用官方 `voice_type`。

按镜头选择：

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes episode_001 --shots episode_001_shot_001
```

`--shots` 只能用于 `storyboard_generation` 之后的动态节点，例如 `shot_bgm_generation`、`ref_frame_generation`、`shot_video_generation` 和 `dynamic_asset_solidification`。

## 断点续跑

项目状态保存在：

```text
outputs/<project_id>/state.json
outputs/current_project.json
outputs/<project_id>/generation_checklist.json
```

动态资产流程会维护 `generation_checklist.json`。可以把指定剧集的 `generate` 改为 `true` 后重新运行 `run generation`，成功后系统会自动把该剧集改回 `false`。

视频生成任务记录在：

```text
outputs/<project_id>/generation_tasks.json
```

如果视频任务已提交但轮询超时，重新运行 `shot_video_generation` 可以根据任务记录继续查询或补写结果。

## 输出文件

典型项目输出位于 `outputs/<project_id>`：

```text
outputs/<project_id>/
├── state.json
├── project.json
├── generation_checklist.json
├── generation_tasks.json
├── logs/
├── assets/
│   ├── audios/
│   │   ├── bgms/
│   │   ├── role_voices/
│   │   └── shot_bgms/
│   ├── images/
│   │   ├── roles/
│   │   ├── props/
│   │   ├── layouts/
│   │   ├── ref_frames/
│   │   └── video_last_frames/
│   ├── videos/
│   │   ├── roles/
│   │   └── shots/
│   └── json/
│       ├── assets/
│       │   └── dynamic_assets.json
│       ├── nodes/
│       ├── roles/
│       ├── props/
│       └── scripts/
├── shots/
└── outputs/
```

## 验证

本仓库约定不要使用 pytest 做验证。推荐使用 `compileall` 和 `scripts/smoke` 下的定向 smoke 脚本。

基础编译检查：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts/smoke
```

常用 smoke：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/config_apikeys_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/toapi_image_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/prop_episode_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/metadata_convergence_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_iterative_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_partial_persistence_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_design_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_episode_key_audit_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_design_missing_episode_keys_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_catalog_manifest_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_catalog_label_lookup_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_catalog_build_samples_profiles_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_select_cache_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_select_episode_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_select_manual_override_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_select_audio_judge_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_voice_generation_uses_voice_select_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_generation_episode_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/functional_role_asset_policy_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_full_body_multiview_sequence_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
```

Smoke 脚本应把临时输出写到仓库 `.tmp/` 目录下。全局 voice catalog 的本地缓存写到 `.assets/voice_catalog/`，该目录默认不纳入版本控制。

## 常见问题

### 运行时报找不到 `autodrama`

直接 CLI 需要设置 `PYTHONPATH`：

```powershell
$env:PYTHONPATH="autodrama/src"
```

或者使用 `run\start.cmd` / `run\dynamic_assets.cmd`。

### 没有项目 ID

在 `config.yaml` 设置：

```yaml
project:
  id: review_demo
```

也可以运行命令时传入：

```powershell
--project review_demo
```

### 只想本地验证流程

使用 fake provider：

```powershell
run\dynamic_assets.cmd --config config.yaml --project local_smoke --fake --force
```

### 真实 Provider 报鉴权错误

检查：

- `apikeys.yaml` 是否存在。
- `config.yaml` 中 `apikeys_file` 是否指向正确文件。
- 对应 Provider 的 `api_key_env` / `group_id_env` / `access_key_env` / `secret_key_env` 是否能在 `apikeys.yaml` 或环境变量中找到。

### 视频任务超时

查看：

```text
outputs/<project_id>/generation_tasks.json
outputs/<project_id>/logs/
```

然后重跑：

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1
```

## 开发约定

- 从仓库根目录运行命令。
- 不要把真实 `apikeys.yaml` 提交到仓库。
- 不使用 pytest 作为本仓库验证方式。
- 新增 smoke 脚本放在 `scripts/smoke/`。
- Smoke 临时输出放在 `.tmp/`。
- 优先复用现有 Provider、Repository、Service、Workflow Node 模式。
