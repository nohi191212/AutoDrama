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
- `project.episode_count`: 初始化/回退剧集数量；默认 `script_import` 当前按单集成熟剧本导入。需要生成式多集拆分时，可手动运行 `script_outline` / `script_novel`。
- `project.episode_duration_seconds`: 单集目标时长。
- `project.bgm_count`: 全局 BGM 数量。
- `generation.roleboard_style_prompt`: 角色身份板统一风格 prompt；身份板允许指定的小号角色名和视图标签，不允许其他文字、水印或 logo。
- `nodes.roleboard_image_generation.params.roleboard_prompt_template`: 可选的角色板 prompt 模板覆盖；未配置时按生图模型自动尝试 `roleboard_prompt/<provider>_<model>`、`roleboard_prompt/<provider>`、`roleboard_prompt/default`。
- `nodes.roleboard_image_generation.params.roleboard_image_generation_concurrency`: 角色身份板图片生成并发数，默认 1，最大 5。
- `generation.prop_design_style_prompt`: 道具设计统一画风 prompt。
- `generation.layout_design_style_prompt`: 旧场景设计风格字段；新 `layout_prompt` 不再读取它，场景画面调性来自配置中的 `visual_tone`。
- `output.root_dir`: 输出目录，默认 `./outputs`。
- `providers`: 各 Provider 的 base URL、模型名和选项。
- `routing`: 不同能力和用途的 Provider 路由。

主视觉、角色和故事板静态图像现在分开配置：

- `key_vision`: 项目主视觉原图，由 `key_vision_image_generation` 生成，默认建议竖版比例，例如 `key_vision_size: "9:16"`。
- `roleboard`: 角色身份板图，由 `roleboard_image_generation` 基于主视觉原图和角色身份板 prompt 生成，包含正面、侧面、背面、表情、动作和服装细节，并在边缘保留小号“角色：<角色名> | <形象名>”及可选视图标签，默认走支持参考图的图像 provider，建议 16:9 横幅比例，例如 `roleboard_size: "16:9"`。
- `clip_storyboard_image_generation`: pregen 内部用于 `clip_storyboard_image_generation` 的图像绑定，按输入 clip 输出 3840×2160、16:9 的黑白线稿 12 宫格故事板整图到 `assets/images/storyboards/`；4×3 网格中的每格为 4:3，并由代码覆盖黑色宫格号、红色镜头号和红色切镜斜杠。`clip_storyboard_keyframe_generation` 随后生成首尾关键帧。Kling 主体模式提交首帧、尾帧、故事板和 subject element；其他视频 provider 继续使用原有参考图策略。

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

所有图片生成默认通过 ToAPI GPT-Image-2：主视觉原图、角色身份板、12 宫格故事板、首尾关键帧、道具图和场景图都会本地保存图片文件，并尽量保存 provider 返回的图片 URL。Kling 主体模式的 `clip_video_inputs` 固定为 `image_1` 首帧、`image_2` 尾帧、`image_3` 故事板，角色通过 subject element 追加；后续 clip 使用上一 clip 的尾帧作为自己的首帧。可灵文本只包含 Camera Shot 段落，不再重复 P01-P12 或图片输入 JSON。每个模型的负向规则通过 `nodes.clip_video_generation.params.negative_rules` 绑定到具体模型。

参考图片统一以公网 URL 提交。签名 URL 过期时，控制台会以淡红色提示，框架通过 ToAPI 上传本地图片，并把新 URL 按本地文件路径、大小和修改时间缓存到项目的 `assets/json/cache/reference_image_urls.json`。默认有效缓存时间为 86400 秒，可通过 `providers.toapi.options.toapi_reference_url_cache_ttl_seconds` 调整；缓存有效时后续 CLI 运行不会重复上传。RightCode 不接收本地图片或 inline base64。

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

如果输入文件已经是成熟分场剧本，默认 pregen 会先通过 `script_import` 导入为锁定的 `novel_full`，再通过 `script_detail_expand` 做保守细化。也可以显式使用导入命令刷新剧本文本：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli import-script --config config.yaml --project huyao --script inputs/狐妖.md --force
```

使用 `import-script` 时，需要同步补少量动作、空间、光影、材质、声音等可拍摄细节，可以加 `--detail-expand`：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli import-script --config config.yaml --project huyao --script inputs/狐妖.md --detail-expand --expanded-script-out inputs/狐妖_细化.md --force
```

如果项目已经生成了角色身份板、语音样例、道具、场景、故事板或镜头视频等资产，只想替换剧本文本并保留已有资产，不要使用 `--force`，改用 `--preserve-assets`：

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli import-script --config config.yaml --project huyao --script inputs/狐妖.md --detail-expand --expanded-script-out inputs/狐妖_细化.md --preserve-assets
```

Windows 下也可以直接用快捷脚本。它默认会保留已有资产、执行保守细化，并自动刷新 `script_novel_extract`：

```powershell
run\refresh_script.cmd --config config.yaml --project huyao --script inputs/狐妖.md --expanded-script-out inputs/狐妖_细化.md
```

需要同时刷新 pregen 故事板时加 `--storyboard`；默认不会生成镜头视频：

```powershell
run\refresh_script.cmd --config config.yaml --project huyao --script inputs/狐妖.md --expanded-script-out inputs/狐妖_细化.md --storyboard --episodes 1
```

`--preserve-assets` 会保留角色、道具、场景、图片、音频、视频状态，包括已生成的 roleboard 身份板，只把 `script_novel_extract` 和可选的 pregen 故事板节点标记为需要重跑。导入后先刷新剧情摘要：

```powershell
run\start.cmd --config config.yaml --project huyao --until script_novel_extract
```

再按需要重新生成 pregen 故事板：

```powershell
run\start.cmd --config config.yaml --project huyao --only clip_storyboard_image_generation --episodes 1
run\start.cmd --config config.yaml --project huyao --only clip_storyboard_keyframe_generation --episodes 1
run\start.cmd --config config.yaml --project huyao --only clip_manifest_generation --episodes 1
```

`import-script` 会写入 `assets/json/scripts/novel_full/episode_001.json`，并把 `script_import` 标记为已完成；加 `--detail-expand` 时也会标记 `script_detail_expand`。它仍会写入并标记 `script_outline` / `script_novel` 作为兼容信息，旧项目里 `script_outline` 完成会视为 `script_import` 完成，`script_novel` 完成会视为 `script_detail_expand` 完成。后续普通预生成会从 `script_novel_extract`、`clip_segment` 和主视觉节点继续：

```powershell
run\start.cmd --config config.yaml --project huyao
```

成熟剧本模式建议先跑到设计审查节点，确认角色、道具、场景没有被过度抽取：

```powershell
run\start.cmd --config config.yaml --project huyao --only layout_extract --force
run\start.cmd --config config.yaml --project huyao --only layout_finalize --force
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

`pregen` 默认运行到 `clip_manifest_generation`。`script_outline`、`script_novel`、`role_subject_*`、`role_voice_select` 和 `bgm_*` 节点代码仍保留，但不在默认链路里，需要时可用 `--only` 手动运行。

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
prop_extract
prop_finalize
layout_extract
layout_finalize
layout_prop_boundary_review
prop_prompt
layout_prompt
prop_image_generation
layout_image_generation
clip_segment
clip_prompt
clip_storyboard_prompt
clip_storyboard_prompt_audit
clip_storyboard_image_generation
clip_storyboard_keyframe_generation
clip_manifest_generation
```

如果只需要跑到主视觉原图生成，可把流程停在 `key_vision_image_generation`：

```powershell
run\start.cmd --config config.yaml --project <project_id> --until key_vision_image_generation
```

角色链的关键依赖顺序：

```text
role_extract_primary
role_extract_functional
role_finalize
roleboard_prompt
roleboard_image_generation
prop_extract
prop_finalize
layout_extract
layout_finalize
layout_prop_boundary_review
prop_prompt
layout_prompt
prop_image_generation
layout_image_generation
clip_segment
clip_prompt
clip_storyboard_prompt
clip_storyboard_prompt_audit
clip_storyboard_image_generation
clip_storyboard_keyframe_generation
clip_manifest_generation
```

`roleboard_prompt` 按角色递归运行，只读取该角色 `episode_keys` 对应的完整正文，并结合主视觉原图信息输出 prompt-only 的角色身份板提示词；代码负责生成 `role_id`、`appearance_id` 等内部 ID。`roleboard_prompt` 的模板按后续 `roleboard_image_generation` 绑定的生图 provider/model 自动选择，也可通过 `nodes.roleboard_image_generation.params.roleboard_prompt_template` 显式指定，便于为 GPT-Image、Seedream 等不同模型维护不同调性的角色板提示词。`roleboard_image_generation` 使用身份板提示词和 `key_vision_image_generation` 产出的主视觉原图作为参考图，生成正面、侧面、背面、表情、动作、服装细节等角色身份板，并要求图片边缘带小号“角色：<角色名> | <形象名>”以及可选的“正面/侧面/背面/头部/表情/动作/服装细节/配饰细节”视图标签，方便后续把图片单独作为参考图时直接识别角色；除这些指定标签外仍禁止字幕、水印、logo、编号、ID、文件名、项目名、剧情台词或乱码文字。`roleboard_image_generation` 支持按 `nodes.roleboard_image_generation.params.roleboard_image_generation_concurrency` 并发生成多张角色板。`role_voice_select` 读取全局 `.assets/voice_catalog/<provider>/<model>/manifest.json`，参考角色身份板并按手工覆盖、有效缓存、DeepSeek Flash 文本 top 3 初筛、Qwen3.5-Omni 音频 judge 终选、catalog 启发式和 provider fallback 的优先级给角色绑定官方 `voice_type`；后续 `shot_dialogue_audio_generation` 会直接使用该 `voice_type` 生成逐镜头对白音频。功能角色如果 `has_dialogue=false` 不选择声音。

`clip_segment` 在剧本正文和摘要之后运行，把每集文本切成建议 8-15 秒的 clip，并为每个 clip 提取 `role_names`、`prop_names`、`layout_names`；episode 时长只作为文本节奏参考，不再用来硬性校验 clip 数量。`clip_prompt` 在角色、道具、场景静态资产之后运行，为每个 clip 生成逐镜头视频提示词。`clip_storyboard_prompt` 严格跟随 `clip_segment` 的 clip 数和顺序，把项目约束、完整正文、剧情摘要、clip 片段、`clip_prompt`、角色身份板摘要以及道具/场景摘要整理成 storyboard clip；每个 clip 的 `video_prompt` 内部固定拆成 2-3 个真实 `Camera Shot`，12 秒左右默认使用 2 个，并写出 P01-P12 十二宫格面板规划。固定机位是默认选择，每个 clip 最多只有 1 个 Camera Shot 使用一种明确的主要运镜；P01-P12 是同一镜头内的视觉节奏采样，不是 1 秒 1 格，固定镜头的连续面板保持相同构图。真实切镜边界会要求在故事板宫格之间用醒目的红色斜杠标出。`clip_storyboard_prompt_audit` 随后按每批最多 8 个相邻 clip 串行审计站姿/坐姿、朝向、空间位置、道具和进出场连续性；有问题的 prompt 会被修订并回写为下游使用的正式 `clip_storyboard_prompt`，审计结果另存为报告。pregen 的 `clip_storyboard_image_generation` 会为每个 storyboard clip 生成一张 3840×2160、16:9 的黑白线稿 12 宫格故事板整图。`clip_manifest_generation` 是本地整理节点，会把故事板 prompt、故事板整图、角色/道具/场景 ID 和融合后的 `video_prompt` 写入 `shots/<episode_key>.json`。Kling 视频生成阶段按 `role_ids` 动态追加 subject element；首尾关键帧不再进入视频输入，跨 clip 衔接由后期剪辑处理。

`clip_storyboard_keyframe_generation` 在默认链中生成视觉关键帧；Kling 主体模式会把首帧和尾帧作为正式视频输入。

`role_finalize` 在 `role_extract_primary` 和 `role_extract_functional` 之后运行，合并主要/功能角色，执行一次批量收口审查，追加遗漏的 `episode_keys/source_chapters`，合并重复角色，并写出 `role_finalize.json` 与角色 JSON。

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

`generation` 阶段不再生成 storyboard JSON 或单独参考图资产。运行 `run\start.cmd --generation ...` 时进入动态流程；不带 `--generation` 时进入 pregen 流程。普通视频 provider 仍先运行 `shot_dialogue_audio_generation`；Kling 3.0 Omni 分支会跳过这个独立 TTS 节点，直接使用 pregen 持久化的角色主体和绑定音色生成 `audio=native` 的音画同步镜头。Kling 的 `clip_video_generation` 读取首帧、尾帧、故事板和 subject element，并在提示词前部追加 `@role_1` 等角色、音色和对白约束；文本只保留 Camera Shot，不重复 P01-P12。

Kling 分支在默认 pregen 中新增两个持久化节点：`role_kling_voice_generation` 首次运行时同步可灵官方音色列表及试听音频到 `.assets/voice_catalog/kling_omni/kling-v3-omni/`，再依据角色声音画像完成文本初筛和试听终选；`role_subject_element_generation` 将选出的官方 `voice_id` 绑定到角色主体。`providers.kling.options.role_voice_map` 只作为最高优先级的人工覆盖，也仍支持用公开的 5～30 秒干净样本 URL 创建自定义音色。远端音色列表刷新失败时会继续复用本地 catalog；首次同步失败且没有缓存时才会停止。建议使用 `image_refer` 从 roleboard 创建多图主体。任何有对白角色缺少音色、镜头角色缺少主体，都会在提交视频前直接报错，避免消耗生成额度后才发现串音。

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
run\start.cmd --config config.yaml --project <project_id> --until roleboard_image_generation
run\start.cmd --config config.yaml --project <project_id> --until clip_storyboard_image_generation
run\start.cmd --config config.yaml --project <project_id> --until clip_manifest_generation
run\start.cmd --generation --config config.yaml --project <project_id> --until clip_video_generation
```

只运行一个节点：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only roleboard_prompt
run\start.cmd --config config.yaml --project <project_id> --only roleboard_image_generation
run\start.cmd --config config.yaml --project <project_id> --only clip_segment
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_prompt
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_prompt_audit
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_image_generation
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_keyframe_generation
run\start.cmd --config config.yaml --project <project_id> --only clip_manifest_generation
run\start.cmd --config config.yaml --project <project_id> --only role_voice_select
run\start.cmd --config config.yaml --project <project_id> --only role_kling_voice_generation
run\start.cmd --config config.yaml --project <project_id> --only role_subject_element_generation
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_dialogue_audio_generation --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1
```

按剧集选择：

```powershell
run\start.cmd --config config.yaml --project <project_id> --only clip_segment --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only roleboard_prompt --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only roleboard_image_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_prompt --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_prompt --episodes 1 --clips 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_prompt_audit --episodes 1 --clips 1-8 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_image_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_image_generation --episodes 1 --clips 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_keyframe_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_manifest_generation --episodes 1 --clips 1-3 --force
run\start.cmd --config config.yaml --project <project_id> --only role_voice_select --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only role_kling_voice_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only role_subject_element_generation --episodes 1 --force
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1,3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes episode_001,episode_003
```

`clip_storyboard_prompt`、`clip_storyboard_prompt_audit`、`clip_storyboard_image_generation`、可选的 `clip_storyboard_keyframe_generation` 和 `clip_manifest_generation` 还支持 `--clips`，可使用 `1-3`、`2,5-7` 或完整 clip ID，只处理选中的 clip 并保留其他已有输出。局部生成 manifest 只要求选中 clip 的故事板整图已经存在，不要求关键帧。

`clip_storyboard_prompt` 会为每个 clip 同时落盘视频用 `video_prompt` 和仅供生图使用的 `storyboard_image_prompt`；后者只包含固定故事板模板和 P01-P12 逐格画面内容，不包含 `video_prompt`、episode/clip 标识或工作流说明。`clip_storyboard_image_generation` 只读取并原样提交 `storyboard_image_prompt`、附加参考图和保存图片，不再组装或安全重写提示词。旧输出缺少该字段时需要先重跑 `clip_storyboard_prompt`。

`pregen --episodes` 只支持配合 `--only` 使用，当前支持 `clip_segment`、`roleboard_prompt`、`roleboard_image_generation`、`role_kling_voice_generation`、`role_subject_video_generation`、`role_subject_element_generation`、`clip_storyboard_prompt`、`clip_storyboard_prompt_audit`、`clip_storyboard_image_generation`、`clip_storyboard_keyframe_generation`、`clip_manifest_generation`、`role_voice_select`、`prop_prompt`、`prop_image_generation` 和 `layout_image_generation`。clip、角色、故事板、关键帧、道具和场景图相关节点会按各自的 `episode_keys` 或目标集过滤；如果角色缺少 `episode_keys`，会直接报错，不会退回加载全文。`prop_design`、`prop_generation` 是旧别名，会分别转到 `prop_prompt`、`prop_image_generation`。

全局音色 catalog 命令：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples --sample-emotion all
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-profiles
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --miss-profiles
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog inspect --config config.yaml --provider volcengine
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider kling --force-manifest
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog inspect --config config.yaml --provider kling
```

`voice-catalog build` 会刷新 provider speaker manifest；对 `--provider kling`，它调用官方 `/v1/general/presets-voices`，并把每个音色自带的试听保存为 `official_trial`。Kling Omni 预置音色 ID 与数字人 `/v1/audio/tts` 的音色 ID 属于不同命名空间，不能直接互用；只有在 `official_voice_tts_map` 配置了官方映射时，短于阈值的试听才会用映射后的 TTS ID 补一份 `normal` 长样例。其他语音 provider 的 `--force-samples` 默认只为目标音色生成 `normal` 样例，避免全量 catalog 触发过多 TTS 请求；如果确实需要五情绪样例，可以加 `--sample-emotion all` 生成 `normal/angry/sad/happy/low`。`--force-profiles` 会调用 `routing.judge.voice_catalog_profile` 配置的 audio judge 重新生成自然语言听感画像，每个音色会单独落盘到 `.assets/voice_catalog/<provider>/<model>/profiles/<voice_type>.json`，同时回写 manifest；`--miss-profiles` 只补 manifest 中缺失或 `profile_hash` 过期的 profile，已有匹配画像会复用并跳过 judge；两种 profile 模式都会以 5 并发调用 judge；如需临时覆盖 judge，可加 `--judge-provider fake` 或其他已注册 judge。调试时可以加 `--voice-type <voice_type>` 或 `--limit 5` 控制范围。项目内选音会先把候选过滤到中文和角色同性别，再进行文本初筛；样例齐全时调用 `routing.judge.role_voice_select` 听 top 3 音频终选。`voice_label` 只作为人类可读展示字段，落盘和 API 绑定始终使用官方 `voice_type/voice_id`。

## Postgen 后处理

后处理固定按以下依赖顺序运行：

1. 收集带原生音画同步的源视频；先对每个镜头执行 ASR，再由 AI盒子 Gemini 3.5 Flash 结合连续音频、联系表、预期台词和 ASR 结果审计台词、角色音色、口型及可用区间。
2. 审计结果进入剪辑计划；FFmpeg 按同一裁切和变速参数处理画面与原生音频，生成锁定时间轴的剪辑版。
3. Kling 原生音色分支默认直接保留剪辑后的原声音轨；只有 `postgen.voice_alignment.enabled=true` 时才执行 Demucs、PyAnnote、RVC、时间轴回贴和双轨混音。
4. 可选用 WhisperX 做字级对齐，生成 SRT/ASS，并通过 FFmpeg + libass 烧录字幕。
5. AI盒子 Gemini 读取成片联系表、完整压缩音频、媒体参数和 ASR 时间轴，输出最终结构化审计报告。

运行完整链：

```powershell
run\postgen.cmd --config config.yaml --project <project_id>
```

可用 `--until <node>` 停在某节点，或用 `--only <node> --force` 单独重跑。开启源镜头/成片审计时，ASR 即使未烧录字幕也会为审计生成时间轴；开启字幕后同一套设置用于生成 ASS。RVC 已降级为可选兜底：只有旧素材或 Kling 原生音色异常时才建议开启，并填写 `speaker_role_map`、`role_rvc_models` 与 `rvc_command`。

主要产物：剪辑版在 `outputs/videos/<episode>_edited.mp4`，音色版在 `<episode>_voice_aligned.mp4`，最终版在 `<episode>_postgen.mp4`；字幕、分轨音频、说话人时间轴和两阶段审计 JSON 均保留在项目 `assets` 目录内。

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
