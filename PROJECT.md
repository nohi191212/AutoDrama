# AutoDrama — AI 自动短剧生成系统

基于 LangGraph 的多模态短剧生成框架。从剧本到成片，全流程自动化。

---

## 项目愿景

做一个**剧集生成界的 Claude Code**——让多模态 LLM + 视频生成模型实现可靠的剧集交付。

现有视频生成工具（Seedance、Seedream 等）已成熟，但开源视频生成框架在一致性方面非常差，高质量短剧仍然大量依赖人类检查、剪辑、监督。

**核心设计思路**：通过尽可能预先固定声音、道具、场景、人物、剧本等关键元素，使逻辑清晰、主要元素一致性高，让观众的观看体验更好。

**成本策略**：文本 token 非常便宜（输入整部剧本可能只需不到 1 毛钱），而图像和视频生成昂贵（一张图几毛钱，一秒视频 1 块甚至更贵）。因此大量使用文本模型在前期做尽可能多的工作（如每次生成图像前确认有没有重复），来尽可能提高画面一致性。

---

## 发展路径

```
阶段 1: 固定节点工作流（LangGraph）  ← 当前阶段
    ↓
阶段 2: 自定义 LangGraph 工作流（参考 Claude Code 框架）
    ↓
阶段 3: 多 Agent 工作流（参考 Claude Code 框架）
```

**内容递进**：1 分钟短片 → 长剧 → 整部高质量剧集

**技术递进**：ComfyUI 工作流修改 → LangGraph 工作流 → 多 Agent 工作流

---

## 架构概览

```
用户输入 (.docx 剧本)
    │
    ▼
┌─────────────────────────┐
│  预生成资产 (12 节点)     │  ← 文本为主，低成本高质量
│  剧本 · 角色 · 道具 · 场景  │
│  BGM · 压缩 · 确认         │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  动态生成资产 (4 节点)    │  ← 图像+视频，高成本
│  分镜 · 参考帧 · 视频生成   │
│  资产固化 → 循环            │
└──────────┬──────────────┘
           ▼
┌─────────────────────────┐
│  剪辑与后期 (5 节点)      │
│  剪辑方案 · 音画分离 · 配乐  │
│  音画剪辑 · 超分            │
└──────────┬──────────────┘
           ▼
        output.mp4
```

---

## Pipeline 拓扑（22 节点）

```
input_document          ← 读取 .docx 剧本
    │
script_outline          ← 【1】剧本-大纲
    │
script_detail           ← 【2】剧本-详细剧本生成（分集，50~70s/集）
    │
script_polish           ← 【3】剧本-打磨（KIMI+DSv4Pro rebuttle 5X）
    │
character_profile       ← 【4】角色-基本设定（rebuttle 3X）
    │
character_voice         ← 【5】角色-声音（音色库：normal/angry/sad...）
    │
character_appearance    ← 【6】角色-基本外形图（base + 各场景变装）
    │
props_setting           ← 【7】道具-基本设定
    │
script_compress         ← 【8】剧本-压缩（各集简约剧本 + 全局简约剧本）
    │
scene_description       ← 【9】场景-场景描述生成（查重→生成→注册）
    │
scene_confirm           ← 【10】场景确认（去重、修BUG）
    │
scene_image             ← 【11】场景-场景图生成（无人物的空场景图）
    │
bgm_assets              ← 【12】音乐资产（主旋律3首 + 情绪通用10首 + 转场4-7首）
    │
storyboard              ← 【13】分镜生成（layout_id, focal_length, camera, content, duration, refs）
    │
reference_frame         ← 【14】参考帧生成（带人物的分镜参考帧）
    │
video_generation        ← 【15】视频生成（分镜 + 角色 + 参考帧 + 前2帧 → 视频）
    │
asset_solidify          ← 【16】资产固化（新增资产→回写ROLES/PROPS/DESIGN_LAYOUT）
    │                    └──→ 循环回 storyboard（如果还有集要处理）
editing_plan            ← 【17】剪辑方案（Qwen3.5Max 决定剪辑手法）
    │
av_separation           ← 【18】音画分离-可选（J-Cut / L-Cut 前处理）
    │
av_editing              ← 【19】基础音画剪辑（pydub + moviepy）
    │
bgm_scoring             ← 【20】配乐方案（Qwen3.5Max 决定 BGM 时间线）
    │
super_resolution        ← 【21】画面超分（1080P → 2K）
    │
final_output            ← 输出 MP4 + 元数据 + 清理临时文件
```

### 路由机制

每个节点通过条件边路由：
- **continue** → 下一节点（无错误）
- **retry** → 本节点重试（错误 + 重试次数 < max_retries）
- **abort** → error_handler（重试耗尽 或 不可恢复错误 `retry_count >= 99`）

### 循环机制

`asset_solidify` 检查 `remaining_episodes`：
- 有剩余剧集 → 回到 `storyboard` 继续生成
- 无剩余 → 进入 `editing_plan` 开始剪辑

---

## 模块结构

```
autodrama/
├── config/                  # YAML 配置加载、Pydantic 校验、${ENV_VAR} 插值
│   ├── schema.py            # AppConfig, LLMConfig, PipelineConfig 等
│   └── loader.py            # 配置文件加载器
│
├── llm/                     # 多厂商 LLM 统一抽象层
│   ├── base.py              # LLMProvider 抽象基类
│   ├── openai_provider.py   # OpenAI / DeepSeek 兼容接口
│   ├── anthropic_provider.py# Anthropic Claude
│   ├── ernie_provider.py    # 文心一言
│   └── factory.py           # LLM 工厂
│
├── models/                  # Pydantic 数据模型
│   ├── script.py            # Character, DialogueLine, Scene, Script
│   ├── scene_plan.py        # ShotComposition, ScenePlan
│   ├── media.py             # ImageAsset, AudioAsset, BackgroundMusic
│   └── video.py             # VideoSegment, FinalVideo
│
├── script/                  # 剧本生成模块
│   ├── generator.py         # ScriptGenerator（LLM 结构化输出）
│   ├── templates.py         # System prompt 模板 + 题材风格字典
│   └── validators.py        # 剧本结构校验
│
├── scene/                   # 分镜规划模块
│   ├── planner.py           # ScenePlanner（镜头规划 + LLM）
│   ├── prompt_builder.py    # 图生文 prompt 构建器
│   └── shot_types.py        # 镜头类型注册表（角度/景别/布光）
│
├── media/                   # 媒体生成模块
│   ├── image/
│   │   ├── base.py          # BaseImageGenerator 抽象类
│   │   ├── dalle_generator.py # DALL-E 图像生成
│   │   └── factory.py       # 图像生成器工厂
│   └── audio/
│       ├── base.py          # BaseTTSGenerator 抽象类
│       ├── edge_tts_generator.py # Edge TTS 语音合成
│       └── factory.py       # TTS 工厂
│
├── video/                   # 视频合成模块
│   ├── composer.py          # VideoComposer（MoviePy 合成）
│   ├── transitions.py       # 转场效果（crossfade, fade-in/out）
│   ├── subtitles.py         # 字幕渲染
│   └── effects.py           # 视频特效
│
├── audio/                   # 音频处理模块
│   ├── processor.py         # AudioProcessor（拼接/混音/音量）
│   └── effects.py           # AudioEffects（淡入淡出/归一化/静音）
│
├── pipeline/                # LangGraph 工作流编排
│   ├── state.py             # DramaState（全局状态 TypedDict）
│   ├── graph.py             # build_pipeline()（22 节点拓扑）
│   └── nodes/               # 所有节点实现
│       ├── input_node.py            # 读取 .docx
│       ├── script_outline_node.py   # 【1】
│       ├── script_detail_node.py    # 【2】
│       ├── script_polish_node.py    # 【3】
│       ├── character_profile_node.py# 【4】
│       ├── character_voice_node.py  # 【5】
│       ├── character_appearance_node.py # 【6】
│       ├── props_node.py            # 【7】
│       ├── script_compress_node.py  # 【8】
│       ├── scene_description_node.py# 【9】
│       ├── scene_confirm_node.py    # 【10】
│       ├── scene_image_node.py      # 【11】
│       ├── bgm_node.py              # 【12】
│       ├── storyboard_node.py       # 【13】
│       ├── reference_frame_node.py  # 【14】
│       ├── video_generation_node.py # 【15】
│       ├── asset_solidify_node.py   # 【16】
│       ├── editing_plan_node.py     # 【17】
│       ├── av_separation_node.py    # 【18】
│       ├── av_editing_node.py       # 【19】
│       ├── bgm_scoring_node.py      # 【20】
│       ├── super_resolution_node.py # 【21】
│       ├── output_node.py           # 最终输出
│       ├── script_node.py           # [Legacy] 旧剧本生成
│       ├── scene_node.py            # [Legacy] 旧分镜规划
│       ├── image_node.py            # [Legacy] 旧图像生成
│       ├── audio_node.py            # [Legacy] 旧 TTS
│       └── compose_node.py          # [Legacy] 旧视频合成
│
└── utils/                   # 工具模块
    ├── logger.py            # Loguru 结构化日志
    ├── file_utils.py        # 文件清理工具
    └── retry.py             # 重试装饰器
```

---

## 全局状态设计（DramaState）

### 核心注册表（方案中定义的单例全局变量）

| 注册表 | 类型 | 说明 |
|--------|------|------|
| `raw_script` | `str` | SCRIPT.raw_script — 原始文档文本 |
| `detailed_script` | `dict[episode, text]` | SCRIPT.detailed_script — 分集详细剧本 |
| `final_script` | `dict[episode, text]` | SCRIPT.final_script — 打磨后剧本 |
| `simple_script` | `dict[episode, text]` | SCRIPT.simple_script — 压缩后简约剧本 |
| `global_script` | `str` | SCRIPT.global_script — 全局概括 |
| `roles` | `dict[name, RoleData]` | ROLES — 角色注册表 |
| `props` | `dict[name, PropData]` | PROPS — 道具注册表 |
| `design_layout` | `dict[scene, dict[variant, LayoutData]]` | DESIGN_LAYOUT — 场景布局注册表 |
| `bgms` | `list[BgmData]` | BGMS — BGM 注册表 |
| `storyboards` | `dict[episode, dict[shot_id, ShotData]]` | STORYBOARDS — 分镜注册表 |

### RoleData 结构

```
role.intro        — 人物详细设定文本
role.audio        — {emotion: {desc: 音色描述, audio: 音频路径}}
role.appearance   — {variant: {desc: 外形描述, image: 图像路径}}
```

### Design Layout 结构

```
DESIGN_LAYOUT['场景名'] = {
    '俯视图': {desc, image},
    '侧俯视图': {desc, image},
    '主卧': {desc, image},
    ...
}
```

### ShotData 结构（分镜）

```
shot.id                    — episode_X_shot_Y
shot.layout_id             — 对应场景 ID
shot.focal_length          — 镜头远近
shot.camera_shooting_angle — 拍摄角度
shot.camera_movement       — 运镜方式
shot.content               — 画面内故事（详细，有画面感）
shot.duration              — 预估秒数
shot.remark                — 备注
shot.role_appearances      — [角色-变体, ...]
shot.role_audios           — [角色-音色, ...]
shot.ref_props             — [参考道具, ...]
shot.ref_frame             — 参考帧
shot.prev_shots            — [参考前序镜头, ...]
```

---

## 实现进度

### 预生成资产（12/12 节点已创建，0/12 已实现）

| # | 节点 | 文件 | 状态 |
|---|------|------|:--:|
| 1 | 剧本-大纲 | `script_outline_node.py` | 🔴 stub |
| 2 | 剧本-详细剧本生成 | `script_detail_node.py` | 🔴 stub |
| 3 | 剧本-打磨 | `script_polish_node.py` | 🔴 stub |
| 4 | 角色-基本设定 | `character_profile_node.py` | 🔴 stub |
| 5 | 角色-声音 | `character_voice_node.py` | 🔴 stub |
| 6 | 角色-基本外形图 | `character_appearance_node.py` | 🔴 stub |
| 7 | 道具-基本设定 | `props_node.py` | 🔴 stub |
| 8 | 剧本-压缩 | `script_compress_node.py` | 🔴 stub |
| 9 | 场景-场景描述生成 | `scene_description_node.py` | 🔴 stub |
| 10 | 场景确认 | `scene_confirm_node.py` | 🔴 stub |
| 11 | 场景-场景图生成 | `scene_image_node.py` | 🔴 stub |
| 12 | 音乐资产 | `bgm_node.py` | 🔴 stub |

### 动态生成资产（4/4 节点已创建，0/4 已实现）

| # | 节点 | 文件 | 状态 |
|---|------|------|:--:|
| 13 | 分镜生成 | `storyboard_node.py` | 🔴 stub |
| 14 | 参考帧生成 | `reference_frame_node.py` | 🔴 stub |
| 15 | 视频生成 | `video_generation_node.py` | 🔴 stub |
| 16 | 资产固化 | `asset_solidify_node.py` | 🔴 stub |

### 剪辑与后期（5/5 节点已创建，0/5 已实现）

| # | 节点 | 文件 | 状态 |
|---|------|------|:--:|
| 17 | 剪辑方案 | `editing_plan_node.py` | 🔴 stub |
| 18 | 音画分离-可选 | `av_separation_node.py` | 🔴 stub |
| 19 | 基础音画剪辑 | `av_editing_node.py` | 🔴 stub |
| 20 | 配乐方案 | `bgm_scoring_node.py` | 🔴 stub |
| 21 | 画面超分 | `super_resolution_node.py` | 🔴 stub |

### 已完成

| 节点 | 文件 | 状态 |
|------|------|:--:|
| Docx 输入 | `input_node.py` | 🟢 已实现 |
| 最终输出 | `output_node.py` | 🟢 已实现 |

---

## 模型选型

| 环节 | 前期（当前） | 后期（高品质） | 后期（低成本） |
|------|:-----------:|:------------:|:------------:|
| 写代码 + DEBUG | Claude Code + DeepSeek v4 (API) | Claude Code + Claude Opus 4.6 (API) | Claude Code + Claude Opus 4.6 (API) |
| 剧本/角色/道具/音乐描述 | DeepSeek v4 Pro (API) | DeepSeek v4 Pro (API) | DeepSeek v4 Pro (API) |
| 图片生成 | Seedream 5.0 Lite (API) | GPT Image 2 (API, ~0.4元/张) | Seedream 5.0 Lite (API) |
| 视频生成 | Wan 2.7 (720P, ~0.6元/s) | Seedance 2.0 (1080P, 1-2元/s) | Wan2.7 / 等开源模型 |
| BGM 生成 | MiniMax Music 2.6 (29元/月) | MiniMax Music 2.6 (29元/月) | MiniMax Music 2.6 (29元/月) |
| 音画剪辑 | Qwen3.5-Omni-Flash (~0.02元/s) | Qwen3.5-Omni-Plus (~0.06元/s) | Qwen3.5-Omni-Flash (~0.02元/s) |

---

## 核心音画剪辑手法（已纳入 av_editing 节点设计）

| 手法 | 核心逻辑 | 效果 |
|------|---------|------|
| J-Cut / L-Cut | 声音先入/后出 | 转场流畅、制造悬念 |
| 有源转无源 | 画面内有源声音→无源配乐 | 主观情绪化、打破第四墙 |
| 声音骤停 | 关键时刻抽走所有声音 | 冲击、震惊、时间凝固 |
| 平行音效 | 用非画面内声音描述动作 | 夸张质感、风格化 |
| 声音超前 | 未来声音提前进入 | 紧张、预感、宿命感 |
| 声音延迟 | 画面结束后声音持续 | 余韵、思念、未完成 |

---

## 快速开始

```bash
# 1. 激活环境
conda activate autodrama

# 2. 配置 API Key
cp .env.example .env
# 编辑 .env 填入各 API Key

# 3. 运行完整 Pipeline
python -c "
from autodrama.pipeline.graph import build_pipeline
graph = build_pipeline()
result = graph.invoke({'docx_path': 'path/to/script.docx'})
print(f'Output: {result.get(\"final_video_path\")}')
"

# 4. 运行测试
pytest tests/ -v
```

---

## 依赖

| 类别 | 技术 |
|------|------|
| Python | 3.11 |
| 工作流 | LangGraph |
| LLM SDK | OpenAI / Anthropic / 文心 / 通义 |
| 视频合成 | MoviePy |
| 图像处理 | Pillow |
| 音频处理 | pydub |
| 语音合成 | Edge TTS |
| 文档解析 | python-docx |
| 数据校验 | Pydantic |
| 日志 | Loguru |

---

## 下一步计划

1. **逐节点实现**：按顺序填充每个 stub 节点的具体逻辑（预生成资产 12 个节点优先）
2. **全局注册表**：创建 `ROLES`、`PROPS`、`DESIGN_LAYOUT`、`BGMS`、`STORYBOARDS` 的单例数据模型
3. **模型对接**：接入 DeepSeek v4 Pro（文本）、Seedream 5.0 Lite（图片）、Wan 2.7（视频）、MiniMax Music 2.6（BGM）
4. **质量迭代**：逐步优化每个节点的效果直到产出满意视频
5. **本地部署**：将可本地部署的小模型（音色生成、音乐生成）租用 5090 显卡部署
