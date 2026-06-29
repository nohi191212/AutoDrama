# 🐙 AutoDrama

AutoDrama 是一个短剧自动生成工作流项目。它把输入故事大纲拆成剧集，生成脚本、角色、道具、场景、BGM 和 12 宫格故事板等可复用静态资产，再按剧集生成镜头视频和动态资产固化记录。

本仓库当前重点支持两段式流程：

1. `pregen`: 预生成脚本、角色、道具、场景、全局 BGM 和 12 宫格故事板等项目级资产。
2. `generation`: 按剧集生成动态镜头资产，目前包含 shot video 和动态资产固化记录。

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
- `generation.roleboard_style_prompt`: 角色身份板统一风格 prompt；身份板允许指定的小号角色名和视图标签，不允许其他文字、水印或 logo。
- `nodes.roleboard_generation.params.roleboard_prompt_template`: 可选的角色板 prompt 模板覆盖；未配置时按生图模型自动尝试 `roleboard_prompt/<provider>_<model>`、`roleboard_prompt/<provider>`、`roleboard_prompt/default`。
- `nodes.roleboard_generation.params.roleboard_generation_concurrency`: 角色身份板图片生成并发数，默认 1，最大 5。
- `generation.prop_design_style_prompt`: 道具设计统一画风 prompt。
- `generation.layout_design_style_prompt`: 旧场景设计风格字段；新 `layout_prompt` 不再读取它，场景画面调性来自导演前期的 `visual_tone`。
- `output.root_dir`: 输出目录，默认 `./outputs`。
- `providers`: 各 Provider 的 base URL、模型名和选项。
- `routing`: 不同能力和用途的 Provider 路由。

主视觉、角色和故事板静态图像现在分开配置：

- `key_vision`: 项目主视觉原图，由 `design_key_vision_image` 生成，默认建议竖版比例，例如 `key_vision_size: "9:16"`。
- `roleboard`: 角色身份板图，由 `roleboard_generation` 基于主视觉原图和角色身份板 prompt 生成，包含正面、侧面、背面、表情、动作和服装细节，并在边缘保留小号“角色：<角色名> | <形象名>”及可选视图标签，默认走支持参考图的图像 provider，建议 16:9 横幅比例，例如 `roleboard_size: "16:9"`。
- `storyboard_sheet_generation`: pregen 内部用于 `storyboard_generation` 的图像绑定，按输入 clip 输出黑白线稿 12 宫格故事板整图到 `assets/images/storyboards/`。`storyboard_keyframe_generation` 会在每集首个 clip 生成 start/end 两张关键帧、后续 clip 只生成自己的 end 关键帧到 `assets/images/storyboard_keyframes/`。`clip_manifest_generation` 再把 clip 脚本、12 宫格故事板和首尾关键帧整理成 `shots/episode_XXX.json`，供 generation 阶段逐 clip 读取。generation 阶段不再提供同名 `storyboard_generation` 节点；保留的 `storyboard_generation` 仅指 pregen 的 12 宫格故事板图像节点。

旧的分散式角色视觉链路已移除；当前角色视觉资产以 roleboard 身份板为准。

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
- 主视觉、角色身份板、道具、场景图像: `toapi` / GPT-Image-2
- 角色语音: `volcengine`
- 全局 BGM: `minimax`
- 镜头视频: `volcengine`

所有图片生成默认通过 ToAPI GPT-Image-2：主视觉原图、角色身份板、12 宫格故事板、storyboard 首尾关键帧、道具图和场景图都会本地保存图片文件，并尽量保存 provider 返回的图片 URL。`clip_manifest_generation` 会为每个 storyboard clip 准备固定 `shot_video_inputs` 和模型专属 `final_video_prompt`：`image_1` 是实际使用的 clip_start_frame，`image_2` 是 clip_end_frame，`image_3` 是当前 clip 的 12 宫格故事板整图，后面依次是一个或多个 roleboard、layout 和 prop。首个 clip 的 start/end 都来自自己；非首个 clip 的 start 来自上一 clip 的 end，并要求视频开头立刻 hard cut 到当前 clip 的 P01。`clip_video_generation` 不再渲染提示词或推断素材，只读取 `shots/<episode_key>.json` 里已经准备好的 `final_video_prompt + shot_video_inputs` 并提交给视频 provider。每个模型的负向规则通过 `nodes.clip_video_generation.params.negative_rules` 绑定到具体模型。

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

### 2. 导入成熟分场剧本

如果输入文件已经是成熟分场剧本，不想让 `script_outline` / `script_novel` 再重写剧情，可以先把剧本导入为锁定的 `novel_full`：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli import-script --config config.yaml --project huyao --script inputs/狐妖.md --force
```

需要在导入前只补少量动作、空间、光影、材质、声音等可拍摄细节时，加 `--detail-expand`：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli import-script --config config.yaml --project huyao --script inputs/狐妖.md --detail-expand --expanded-script-out inputs/狐妖_细化.md --force
```

如果项目已经生成了角色身份板、语音样例、道具、场景、故事板或镜头视频等资产，只想替换剧本文本并保留已有资产，不要使用 `--force`，改用 `--preserve-assets`：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli import-script --config config.yaml --project huyao --script inputs/狐妖.md --detail-expand --expanded-script-out inputs/狐妖_细化.md --preserve-assets
```

Windows 下也可以直接用快捷脚本。它默认会保留已有资产、执行保守细化，并自动刷新 `director_prep` 和 `script_novel_extract`：

```powershell
run\refresh_script.cmd --config config.yaml --project huyao --script inputs/狐妖.md --expanded-script-out inputs/狐妖_细化.md
```

需要同时刷新 pregen 故事板时加 `--storyboard`；默认不会生成镜头视频：

```powershell
run\refresh_script.cmd --config config.yaml --project huyao --script inputs/狐妖.md --expanded-script-out inputs/狐妖_细化.md --storyboard --episodes 1
```

`--preserve-assets` 会保留角色、道具、场景、图片、音频、视频状态，包括已生成的 roleboard 身份板，只把 `director_prep`、`script_novel_extract` 和可选的 pregen 故事板节点标记为需要重跑。导入后先刷新导演前期和剧情摘要：

```powershell
run\start.cmd --config config.yaml --project huyao --until script_novel_extract
```

再按需要重新生成 pregen 故事板：

```powershell
run\start.cmd --config config.yaml --project huyao --only storyboard_generation --episodes 1
run\start.cmd --config config.yaml --project huyao --only storyboard_keyframe_generation --episodes 1
run\start.cmd --config config.yaml --project huyao --only clip_manifest_generation --episodes 1
```

`import-script` 会写入 `assets/json/scripts/novel_full/episode_001.json`，并把 `script_outline`、`script_novel` 标记为已完成。后续普通预生成会跳过重写型脚本节点，先生成 `director_prep`，再从 `script_novel_extract` 继续：

```powershell
run\start.cmd --config config.yaml --project huyao
```

成熟剧本模式建议先跑到设计审查节点，确认角色、道具、场景没有被过度抽取：

```powershell
run\start.cmd --config config.yaml --project huyao --only layout_extract --force
run\start.cmd --config config.yaml --project huyao --only layout_dedupe_review --force
```

如果暂时不需要 BGM 预资产，可以把 `project.bgm_count` 设为 `0`。

### 3. 运行预生成流程

使用 Windows 快捷脚本：

```powershell
run\start.cmd --config config.yaml --project <project_id>
```

等价的直接 CLI：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id>
```

`pregen` 默认运行到 `role_voice_select`。`prop_*`、`layout_*` 和 `bgm_*` 节点暂时从默认 pregen 流程中屏蔽，代码仍保留，需要时可用 `--only` 手动运行。

```text
script_outline
script_novel
director_prep
design_key_vision_prompt
design_key_vision_image
script_novel_extract
clip_segment
role_extract_primary
role_extract_functional
role_extract
role_episode_key_audit
role_duplicate_audit
ambient_entity_extract
roleboard_prompt
roleboard_generation
storyboard_prompt
storyboard_generation
storyboard_keyframe_generation
clip_manifest_generation
role_voice_select
```

如果只需要跑到主视觉原图生成，可把流程停在 `design_key_vision_image`：

```powershell
run\start.cmd --config config.yaml --project <project_id> --until design_key_vision_image
```

角色链的关键依赖顺序：

```text
clip_segment
role_extract
role_episode_key_audit
role_duplicate_audit
roleboard_prompt
roleboard_generation
storyboard_prompt
storyboard_generation
storyboard_keyframe_generation
clip_manifest_generation
role_voice_select
```

`roleboard_prompt` 按角色递归运行，只读取该角色 `episode_keys` 对应的完整正文，并结合主视觉原图信息输出 prompt-only 的角色身份板提示词；代码负责生成 `role_id`、`appearance_id` 等内部 ID。`roleboard_prompt` 的模板按后续 `roleboard_generation` 绑定的生图 provider/model 自动选择，也可通过 `nodes.roleboard_generation.params.roleboard_prompt_template` 显式指定，便于为 GPT-Image、Seedream 等不同模型维护不同调性的角色板提示词。`roleboard_generation` 使用身份板提示词和 `design_key_vision_image` 产出的主视觉原图作为参考图，生成正面、侧面、背面、表情、动作、服装细节等角色身份板，并要求图片边缘带小号“角色：<角色名> | <形象名>”以及可选的“正面/侧面/背面/头部/表情/动作/服装细节/配饰细节”视图标签，方便后续把图片单独作为参考图时直接识别角色；除这些指定标签外仍禁止字幕、水印、logo、编号、ID、文件名、项目名、剧情台词或乱码文字。`roleboard_generation` 支持按 `nodes.roleboard_generation.params.roleboard_generation_concurrency` 并发生成多张角色板。`role_voice_select` 读取全局 `.assets/voice_catalog/<provider>/<model>/manifest.json`，参考角色身份板并按手工覆盖、有效缓存、DeepSeek Flash 文本 top 3 初筛、Qwen3.5-Omni 音频 judge 终选、catalog 启发式和 provider fallback 的优先级给角色绑定官方 `voice_type`；后续 `shot_dialogue_audio_generation` 会直接使用该 `voice_type` 生成逐镜头对白音频。功能角色如果 `has_dialogue=false` 不选择声音。

`clip_segment` 在剧本正文和摘要之后运行，把每集文本切成建议 8-15 秒的 clip，并为每个 clip 提取 `role_names`、`prop_names`、`layout_names`；episode 时长只作为文本节奏参考，不再用来硬性校验 clip 数量。`storyboard_prompt` 在 `roleboard_generation` 之后运行，严格跟随 `clip_segment` 的 clip 数和顺序，把导演前期、完整正文、剧情摘要、clip 片段和角色身份板摘要整理成 storyboard clip；每个 clip 的 `video_prompt` 内部再拆 1-4 个真实 `Camera Shot`，推荐 2-4 个，并写出 P01-P12 十二宫格面板规划。P01-P12 是视觉节奏帧，不是 1 秒 1 格；真实切镜边界会要求在故事板宫格之间用醒目的红色斜杠标出。pregen 的 `storyboard_generation` 会为每个 storyboard clip 生成一张黑白线稿 12 宫格故事板整图，保存到 `assets/images/storyboards/`。`storyboard_keyframe_generation` 紧随其后，从每集首个 clip 的 P01/P12 生成 start/end 关键帧，并从后续 clip 的 P12 生成 end 关键帧。`clip_manifest_generation` 是本地整理节点，会把故事板 prompt、12 宫格故事板整图、首尾关键帧、角色/道具/场景 ID 和融合后的 `video_prompt` 写入 `shots/<episode_key>.json`，同时保留已有视频/音频动态资产字段。这里的 `storyboard_generation` 只属于 pregen；generation 阶段不再提供同名 storyboard 节点。

`role_episode_key_audit` 在 `role_extract` 之后运行，会以 30 并发逐个检查角色 JSON 的 `episode_keys` 覆盖情况；如果发现遗漏，只向 `role_extract*`、已有 `roleboard_prompt`、角色 JSON 和 state 角色记录追加缺失的 `episode_keys/source_chapters`，不会删除或重排原有条目。

### 4. 运行动态资产生成流程

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
shot_dialogue_audio_generation
clip_video_generation
dynamic_asset_solidification
```

`generation` 阶段不再生成 storyboard JSON 或单独参考图资产。运行 `run\start.cmd --generation ...` 时进入动态流程；不带 `--generation` 时进入 pregen 流程。`shot_dialogue_audio_generation` 先读取 `shots/<episode_key>.json` 中的 `dialogue`，用 `role_voice_select` 绑定的官方 `voice_type` 生成对白音频并写回 `dialogue_audio_assets`。`clip_video_generation` 随后读取同一个 clip 清单中的 `final_video_prompt` 和 `shot_video_inputs`，只负责提交视频任务、轮询和落盘；`shot_video_inputs` 前三张固定是 `clip_start_frame`、`clip_end_frame`、`storyboard`，人物输入必须是 roleboard 图片，不是 Kling subject element 或其它 element 引用。对白文本、Camera Shot 段落、P01-P12 面板规划、首尾帧连续性和非首 clip hard cut 约束必须已经包含在 `video_prompt` 以及由 `clip_manifest_generation` 渲染出的 `final_video_prompt` 中。

### 5. 一次性运行预生成和动态资产

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
run\start.cmd --config config.yaml --project <project_id> --until roleboard_generation
run\start.cmd --config config.yaml --project <project_id> --until storyboard_generation
run\start.cmd --config config.yaml --project <project_id> --until storyboard_keyframe_generation
run\start.cmd --config config.yaml --project <project_id> --until clip_manifest_generation
run\start.cmd --generation --config config.yaml --project <project_id> --until clip_video_generation
```

只运行一个节点：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only roleboard_prompt
run\start.cmd --config config.yaml --project <project_id> --only roleboard_generation
run\start.cmd --config config.yaml --project <project_id> --only clip_segment
run\start.cmd --config config.yaml --project <project_id> --only storyboard_prompt
run\start.cmd --config config.yaml --project <project_id> --only storyboard_generation
run\start.cmd --config config.yaml --project <project_id> --only storyboard_keyframe_generation
run\start.cmd --config config.yaml --project <project_id> --only clip_manifest_generation
run\start.cmd --config config.yaml --project <project_id> --only role_voice_select
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_dialogue_audio_generation --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1
```

按剧集选择：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only clip_segment --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only roleboard_prompt --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only roleboard_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only storyboard_prompt --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only storyboard_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only storyboard_keyframe_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_manifest_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only role_voice_select --episodes 1 --force
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1,3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes episode_001,episode_003
```

`pregen --episodes` 只支持配合 `--only` 使用，当前支持 `clip_segment`、`roleboard_prompt`、`roleboard_generation`、`storyboard_prompt`、`storyboard_generation`、`storyboard_keyframe_generation`、`clip_manifest_generation`、`role_voice_select`、`prop_prompt`、`prop_image_generation` 和 `layout_image_generation`。clip、角色、故事板、关键帧、道具和场景图相关节点会按各自的 `episode_keys` 或目标集过滤；如果角色缺少 `episode_keys`，会直接报错，不会退回加载全文。`prop_design`、`prop_generation` 是旧别名，会分别转到 `prop_prompt`、`prop_image_generation`。

全局音色 catalog 命令：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples --sample-emotion all
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-profiles
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --miss-profiles
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog inspect --config config.yaml --provider volcengine
```

`voice-catalog build` 会刷新 provider speaker manifest；`--force-samples` 默认只为目标音色生成 `normal` 样例，避免全量 catalog 触发过多 TTS 请求；如果确实需要五情绪样例，可以加 `--sample-emotion all` 生成 `normal/angry/sad/happy/low`。`--force-profiles` 会调用 `routing.judge.voice_catalog_profile` 配置的 audio judge 重新生成自然语言听感画像，每个音色会单独落盘到 `.assets/voice_catalog/<provider>/<model>/profiles/<voice_type>.json`，同时回写 manifest；`--miss-profiles` 只补 manifest 中缺失或 `profile_hash` 过期的 profile，已有匹配画像会复用并跳过 judge；两种 profile 模式都会以 5 并发调用 judge；如需临时覆盖 judge，可加 `--judge-provider fake` 或其他已注册 judge。调试时可以加 `--voice-type <voice_type>` 或 `--limit 5` 控制范围。项目内 `role_voice_select` 会先把候选过滤到中文、角色同性别、豆包语音合成模型 2.0（Volcengine catalog）后，为每个候选生成内部 `candidate_id`，并用 `deepseek-v4-flash`、关闭 thinking 的单次文本调用直接筛 top 3；多个角色会并行执行，每个角色确定后立即落盘并输出 `<role> generated` 日志。样例齐全时再调用 `routing.judge.role_voice_select`，默认 Qwen3.5-Omni-Plus，听 top 3 音频终选。`voice_label` 只作为人类可读展示字段，落盘和合成 API 始终使用官方 `voice_type`。

按镜头选择：

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes episode_001 --shots episode_001_shot_001
```

`--shots` 只能用于支持镜头选择的动态节点，例如 `shot_dialogue_audio_generation`、`clip_video_generation` 和 `dynamic_asset_solidification`。

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

如果视频任务已提交但轮询超时，重新运行 `clip_video_generation` 可以根据任务记录继续查询或补写结果。

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
│   │   └── role_voices/
│   ├── images/
│   │   ├── roles/
│   │   ├── props/
│   │   ├── layouts/
│   │   ├── storyboards/
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
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/roleboard_pregen_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/config_roleboard_load_smoke.py
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
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1
```

## 开发约定

- 从仓库根目录运行命令。
- 不要把真实 `apikeys.yaml` 提交到仓库。
- 不使用 pytest 作为本仓库验证方式。
- 新增 smoke 脚本放在 `scripts/smoke/`。
- Smoke 临时输出放在 `.tmp/`。
- 优先复用现有 Provider、Repository、Service、Workflow Node 模式。
