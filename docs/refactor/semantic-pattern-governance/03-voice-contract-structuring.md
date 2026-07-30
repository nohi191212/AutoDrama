# 任务 3：角色声音需求与音色选择结构化

## 任务目标

删除角色声音选择链路中的多套性别、性格和语言关键词推断，使角色声音需求、音色官方元数据和音色画像成为唯一决策输入。

本任务只治理角色级基础音色选择和 provider fallback。逐句对白情绪与旁白模式在任务 4 治理。

本任务依赖任务 1；建议在任务 2 完成后实施，以便声音选择可以稳定读取角色年龄感和视觉造型信息。

## 当前问题

当前至少存在三套不一致的性别判断：

1. `VoiceCatalogService._infer_role_gender()`
2. `VoiceCatalogService._voice_gender()`
3. `VolcengineSeedTTSProvider._infer_gender_hint()`

此外还有：

- 通过角色简介和音色画像关键词修改匹配分数。
- 通过音色名称、voice type 和标签文本猜测语言、年龄和性别。
- 性别猜测结果被用于硬过滤候选。
- 未命中时 provider 默认选择男声或女声。
- Mock provider 根据角色文本选择假音色。

同一角色在不同路径上可能得到不同结论，并且误判会直接排除正确候选。

## 目标数据模型

### 1. `RoleVoiceRequirements`

建议作为 `Role` 的独立字段：

```text
RoleVoiceRequirements
- schema_version
- language: zh | en | ja | es | other | unspecified
- gender_presentation: female | male | neutral | unspecified
- age_impression: child | teen | young_adult | adult | mature | elderly | unspecified
- performance_traits: list[str]
- baseline_emotion: str | null
- hard_constraints: list[str]
- provenance: SemanticProvenance
```

约束：

- `unspecified` 是合法值。
- `gender_presentation` 描述声音呈现需求，不推断角色生理属性。
- 没有证据时不得根据角色名、代词、亲属称谓或职位猜测。
- `performance_traits` 是上游模型或用户显式输出，不由消费端扫描简介生成。

### 2. `VoiceCatalogProfile`

现有结构已经包含：

- `gender_presentation`
- `age_impression`
- `performance_style`
- `best_role_types`
- `avoid_role_types`
- `emotion_quality`

需要补强：

- 对性别和年龄字段使用明确枚举。
- 每个字段记录来源：官方 metadata、音频 judge、人工修订。
- 官方 metadata 优先使用固定字段，不解析 label。
- 模型画像与官方字段冲突时保留冲突状态，不能通过关键词覆盖。

## 实施范围

### 1. 角色抽取阶段生成声音需求

更新角色抽取或角色声音设计流程，让 LLM 直接输出 `RoleVoiceRequirements`。

输入只包含：

- 角色必要身份和年龄线索。
- 对白表现需求。
- 人物视觉年龄或造型参考。
- 与声音选择直接相关的性格信息。

输出必须带：

- 结构字段。
- 支持字段判断的证据。
- 置信度。
- 无法确定时的 `unspecified`。

不得把角色项目路径、无关剧情元数据等内容加入提示词。

### 2. 音色目录元数据规范化

音色目录导入时完成一次性规范化：

- 官方 `gender`、`language`、`model_family` 使用精确字段映射。
- 官方值未知时保留 `unspecified`。
- 不扫描 `voice_label` 或 `voice_type` 中的自然语言词片段。
- 对缺少官方字段的音色使用现有 omni profile/judge 流程生成结构画像。
- 画像结果持久化，不在每次 shortlist 时重新分析。

官方枚举映射可以保留，但必须是精确值到内部枚举的映射，不允许子串匹配。

### 3. 改造候选池过滤

新的过滤顺序：

1. 用户手动绑定的 `voice_type`。
2. provider/model 明确能力兼容性。
3. 官方或结构画像中的语言。
4. 只有双方性别呈现都明确时才允许执行性别约束。
5. `unspecified` 不应因性别未知直接排除。
6. 其余角色气质和表演匹配交给结构化 shortlist/judge。

硬过滤只能依据：

- 精确 provider 能力。
- 精确模型家族。
- 明确语言。
- 用户明确要求的性别呈现。

不能依据：

- 角色文本关键词。
- 音色标签子串。
- 音色名称中的“御姐”“大叔”等词。

### 4. 改造本地评分

删除基于自然语言包含关系的加减分。

允许保留的确定性评分：

- 明确枚举相等。
- provider 能力满足。
- 可用情绪样本完整度。
- 音频质量审计得分。
- 结构化 `emotion_quality` 数值。

角色类型、性格、质感等开放语义由现有 shortlist/judge 模型基于结构化 profile 选择，不在 Python 中维护词表。

### 5. 改造 provider fallback

`VolcengineSeedTTSProvider` 等 provider 不得读取角色简介猜测性别。

fallback 顺序建议：

1. 已绑定 `voice_type`。
2. 角色声音需求匹配的配置默认音色。
3. 中性或全局默认音色。
4. 明确失败并要求先执行角色音色选择。

provider 层不得拥有独立角色语义。

### 6. 改造 Mock

Mock provider 根据以下稳定输入返回结果：

- 显式 `voice_type`。
- `RoleVoiceRequirements.gender_presentation`。
- 固定 fixture role ID。

不得扫描角色名字、简介、性格或代词。

### 7. 缓存与迁移

需要重新计算：

- `role_profile_hash`
- shortlist 缓存
- voice selection 缓存
- 与旧 `catalog_heuristic` 结果有关的选择记录

迁移策略：

- 用户手动绑定保持不变。
- 官方字段明确且旧结果一致时可以迁移。
- 旧 heuristic 选择标记为待复核。
- 不通过旧文本词表反推出新版字段。
- 新选择结果记录 `selection_source` 和新版 schema/catalog version。

## 预期涉及文件

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/core/voice_catalog.py`
- `autodrama/src/autodrama/services/voice_catalog_service.py`
- `autodrama/src/autodrama/providers/volcengine/audio/seed_tts.py`
- `autodrama/src/autodrama/providers/local/mock/fake.py`
- `autodrama/src/autodrama/workflows/nodes/voice_nodes.py`
- `autodrama/src/autodrama/prompts/role_voice_select_shortlist/default.md`
- `autodrama/src/autodrama/prompts/role_voice_select_audio_judge/default.md`
- 角色声音需求提取 prompt
- 音色目录迁移脚本
- `scripts/smoke/voice_contract_v2_smoke.py`

## 非目标

- 不在本任务决定单句对白情绪。
- 不在本任务解析 `VO:`、`OS:` 或说话人。
- 不把三套性别词表合并成一套公共词表。
- 不通过配置增加更多性别同义词。
- 不强制所有角色必须有二元性别呈现。

## 实施步骤

1. 定义 `RoleVoiceRequirements`。
2. 收紧 `VoiceCatalogProfile` 枚举和来源。
3. 更新角色声音需求提取流程。
4. 更新音色目录导入与画像持久化。
5. 改造候选池硬过滤。
6. 删除自然语言本地评分。
7. 改造 provider fallback。
8. 改造 Mock。
9. 失效并迁移旧选择缓存。
10. 删除三套性别推断函数。
11. 从任务 1 基线中清除角色声音类违规。

## 验证要求

不得使用 pytest。至少执行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts/smoke/voice_contract_v2_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_contract_v2_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/check_semantic_patterns.py
```

烟测场景至少包括：

- 角色简介出现“他”“她”“主管”等文本不会改变候选池。
- `gender_presentation=unspecified` 时不执行性别硬过滤。
- 双方显式性别呈现冲突时按配置策略过滤或警告。
- 音色 label 改名不会改变结构画像和候选结果。
- 用户手动绑定优先级最高。
- provider 无绑定时不会扫描角色文本。
- 同一结构输入重复执行得到确定候选池。
- 旧 heuristic 选择被标记为待迁移，而不是静默复用。

## 完成定义

- `_infer_role_gender()` 已删除。
- `_voice_gender()` 不再做自然语言子串推断；如保留，只能读取结构字段和精确枚举。
- `_infer_gender_hint()` 已删除。
- 本地评分不再扫描角色或音色画像自然语言。
- provider fallback 不再猜测角色性别。
- Mock 不再依赖角色文本关键词。
- 角色声音需求和音色画像具有明确来源。
- 声音类违规从静态审计基线中清零。

## 风险与回滚

### 风险

- 大量音色缺少官方结构化性别或语言字段。
- `unspecified` 增多会扩大候选池和模型调用成本。
- 旧选择缓存失效后生成结果发生变化。

### 控制方式

- 先批量完成音色 omni profile，再切换候选过滤。
- 对候选数量设置结构化能力过滤和明确上限。
- 保留用户手动绑定和已人工确认选择。
- 对重新选择记录前后差异报告。

### 回滚

- 可以恢复旧的已确认 `voice_type` 绑定。
- 可以回滚 shortlist/judge 模型版本。
- 不得回滚到角色文本或音色名称关键词推断。
