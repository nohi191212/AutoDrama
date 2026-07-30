# 任务 4：镜头状态、对白、画面文字与视频提示词结构化

## 任务目标

让镜头规划模型直接输出镜头消费者需要的业务结构，删除后续代码对自然语言镜头描述、对白和视频提示词的二次猜测。

本任务治理：

- 当镜人物状态。
- 结构化对白与说话人。
- 对白情绪和表演模式。
- 旁白/画外音。
- 精确画面文字与后期叠字。
- 视频提示词的自然语言清洗和改写。

本任务依赖任务 1，并应复用任务 2 的 `ShotEntityState` 和任务 3 的角色声音契约。

## 当前问题

当前 `clip_to_shots` 只输出：

- `shot_description`
- `narrative_angle`
- `opening_state`
- `ref_ids`
- `video_prompt`
- `duration_seconds`
- `dialogue: list[str]`

下游因此必须继续猜测：

- `角色名：对白` 中的说话人。
- `VO/V.O/OS/旁白` 是否表示旁白。
- 对白属于 angry、sad、tense、whisper 等哪种情绪。
- 引号内容是不是画面文字。
- 视频 prompt 中哪些短语应删除或改写。
- 当镜伤势、手持物、姿态和能量状态。

## 目标 Schema

### 1. `DialogueLine`

```text
DialogueLine
- line_index
- speaker_role_id: str | null
- speaker_name: str | null
- text: str
- emotion: normal | angry | sad | happy | tense | whisper | other
- intensity: float | null
- delivery_mode: on_screen | offscreen | voiceover
- source_text: str | null
- provenance: SemanticProvenance
```

约束：

- `text` 只包含可朗读文本。
- 舞台说明不能混入 `text`。
- 已知角色必须使用 `speaker_role_id`。
- 无法确定说话人时允许 `null`，但后续 TTS 必须明确报错或进入复核。
- `emotion` 由镜头规划输出，消费端不得扫描句子修改。

### 2. `OverlayTextSpec`

```text
OverlayTextSpec
- text
- render_mode: postproduction | in_scene
- placement_hint: str | null
- start_seconds: float | null
- end_seconds: float | null
- provenance: SemanticProvenance
```

规则：

- 需要精确可读文字时必须输出 spec。
- `postproduction` 对应 clean plate 和后期叠加。
- 普通对白引号、作品名和引用文本不能自动变成 overlay。
- 没有 spec 时，下游不得扫描 `"文字"`、`"匾额"`、引号等内容。

### 3. 镜头结构扩展

扩展 `ClipToShotsModelItem`、`ShotPlanItem` 和必要的 manifest 字段：

```text
- entity_states: list[ShotEntityState]
- dialogue_lines: list[DialogueLine]
- overlay_text_spec: OverlayTextSpec | null
- allowed_props: list[str]
- camera_spec: structured value or existing explicit fields
```

旧 `dialogue: list[str]` 可以在迁移期只用于展示，但不能继续作为运行时事实来源。

### 4. Provider 参数与自然语言分离

宽高比、画幅、provider placeholder 和模型控制参数应通过 metadata/请求参数传递，不应先写进自然语言再删除。

例如：

- `aspect_ratio`
- `resolution`
- reference placeholders
- clean plate 开关
- negative prompt

视频 prompt 只描述本镜头可见动作、表演、节奏和镜头意图。

## 实施范围

### 1. 更新 `clip_to_shots` Schema 和提示词

修改 `clip_to_shots/default.md`，要求模型直接输出新增结构。

提示词必须明确：

- `entity_states` 只描述当前镜头状态。
- 每个状态引用有效角色/道具 ID。
- 对白逐条输出说话角色、情绪和 delivery mode。
- 精确文字单独进入 `overlay_text_spec`。
- 不把 provider 参数写入 `video_prompt`。
- 无法确定的字段使用 `null/other`，不能捏造。

### 2. 更新模型输出验证

`ShotAssetNodeBase._validate_model_output()` 只做结构校验：

- ID 引用存在。
- 每镜恰好一个 layout。
- 时长范围正确。
- `speaker_role_id` 属于可用角色。
- `held_props` 和 `allowed_props` 引用有效。
- overlay 时段不超出镜头。
- `postproduction` overlay 必须触发 clean plate。

不得检查自然语言是否包含某些词。

### 3. 改造对白音频生成

删除：

- `_dialogue_speaker_prefix()`
- `_clean_dialogue_speaker_name()` 中的语义兼容职责
- `_dialogue_speaker_is_voiceover()`
- `_role_for_dialogue_line()` 对字符串格式的运行时解析
- `_shot_dialogue_emotion()`

新的音频生成直接读取：

- `speaker_role_id`
- `text`
- `emotion`
- `delivery_mode`

如果角色或音色缺失，输出结构化失败记录，不能猜测单角色或默认说话人。

### 4. 改造画面文字流程

删除 `ShotKeyframePromptNode._exact_text()`。

新流程：

- `overlay_text_spec.render_mode=postproduction`
  - 关键帧生成 clean plate。
  - 记录 overlay 文本。
  - 后期按 spec 叠加。
- `render_mode=in_scene`
  - 明确允许模型尝试生成。
  - 仍保留质量审计。
- `overlay_text_spec=null`
  - 不执行任何引号或文字关键词扫描。

`requires_exact_text` 如继续保留，应由 spec 派生，不接受独立冲突值。

### 5. 删除视频提示词自然语言清洗

删除或重构 `utils/video_prompts.py` 中：

- `_ASPECT_RATIO_WORDS`
- `_TEXT_ARTIFACT_BAN_PATTERNS`
- `_CAMERA_REWRITES`
- `sanitize_video_prompt_text()` 的语义处理

替代方式：

- 上游 prompt 直接要求合规输出。
- 画幅通过 provider 参数传递。
- 不允许的字幕、水印等要求放在统一生成约束中，不做事后中文句子删除。
- 镜头朝向由结构化 camera/narrative 字段决定，不通过固定短语改写。

格式性 placeholder 清理如确有协议需要，可以保留在 provider adapter，但必须只处理精确协议 token。

### 6. 历史产物迁移

为旧镜头产物提供一次性转换：

- 将字符串对白交给专用结构化迁移流程。
- 将旧 `requires_exact_text/overlay_text` 转为 `OverlayTextSpec`。
- 已有 `entity_states` 原样验证。
- 缺失人物状态不从 `video_prompt` 猜测；标记为空或待复核。
- 迁移保留原始对白和证据。

运行时不得保留旧对白词表 fallback。

## 预期涉及文件

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/prompts/clip_to_shots/default.md`
- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/workflows/pregen.py`
- `autodrama/src/autodrama/utils/video_prompts.py`
- `autodrama/src/autodrama/editing/planner.py`
- `autodrama/src/autodrama/postgen/*`
- 相关 fake provider 输出
- 镜头产物迁移脚本
- `scripts/smoke/structured_shot_contract_smoke.py`
- `scripts/smoke/shot_pipeline_fake_e2e_smoke.py`

## 非目标

- 不使用一个更大的情绪词典替代 `keyword_map`。
- 不使用 NLP 分词库在运行时继续分类。
- 不保留 `角色名：对白` 作为唯一运行时格式。
- 不通过正则修复模型漏填的 `overlay_text_spec`。
- 不在 provider adapter 中改写镜头业务语义。

## 实施步骤

1. 定义 `DialogueLine` 和 `OverlayTextSpec`。
2. 升级镜头相关 Schema 版本。
3. 更新 `clip_to_shots` 提示词和 fake 输出。
4. 更新模型输出结构校验。
5. 接入 `ShotEntityState`。
6. 切换对白音频生成。
7. 切换画面文字和 clean plate 逻辑。
8. 分离 provider 参数与自然语言 prompt。
9. 删除视频 prompt 语义清洗。
10. 实现旧镜头产物迁移。
11. 更新 postgen/editing 消费者。
12. 从任务 1 基线中清除镜头和对白类违规。

## 验证要求

不得使用 pytest。至少执行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts/smoke/structured_shot_contract_smoke.py scripts/smoke/shot_pipeline_fake_e2e_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/structured_shot_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_pipeline_fake_e2e_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/check_semantic_patterns.py
```

烟测场景至少包括：

- 角色名包含 `os` 等字符时不会被误判为旁白。
- 同一句对白文本在不同显式 `emotion` 下选择不同表演计划。
- `emotion=normal` 时对白中的“冷”“证据”“沉默”不改变情绪。
- 引号对白不会自动生成 overlay。
- 显式 postproduction overlay 会生成 clean plate。
- 显式 in-scene text 不进入后期叠字。
- 横幅广告等自然语言不会因“横幅”被删除。
- provider 宽高比参数不出现在自然语言 prompt。
- 当镜状态和角色基础身份分别渲染。

## 完成定义

- `dialogue_lines` 成为对白唯一运行时事实来源。
- 对白说话人、情绪和 delivery mode 不再由字符串解析。
- `OverlayTextSpec` 成为精确文字唯一事实来源。
- `_exact_text()` 已删除。
- `_shot_dialogue_emotion()` 已删除。
- 旁白 marker 规则已删除。
- 视频提示词中不存在业务语义 PATTERN 和 rewrite 表。
- 镜头/对白类违规从静态审计基线中清零。

## 风险与回滚

### 风险

- 镜头 Schema 变化会影响多个下游节点和已有 JSON。
- 模型可能漏填 `speaker_role_id` 或 emotion。
- postgen 仍依赖旧 `dialogue` 字段。

### 控制方式

- 先双写结构化字段和旧展示字段，再逐个切换消费者。
- 双写期间禁止从旧字段反推新字段。
- 对模型漏填项明确拒绝或进入复核。
- 使用 fake E2E 验证完整节点链。

### 回滚

- 保留迁移前镜头 JSON。
- 可以回滚到上一版结构化 Schema 和 prompt。
- 不得重新启用旁白、情绪、文字或视频提示词关键词推断。
