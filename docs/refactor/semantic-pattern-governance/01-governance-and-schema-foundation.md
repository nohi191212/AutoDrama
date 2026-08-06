# 任务 1：语义 PATTERN 治理基线与 Schema 基础

## 任务目标

建立全项目统一的治理规则和结构化数据基础，阻止继续引入“通过零散自然语言关键词、正则或替换表推断业务语义”的实现。

本任务只建立边界、基础 Schema、静态审计能力和迁移约束，不切换视觉、声音、镜头等具体消费者。后续任务 2～5 均依赖本任务。

## 背景

当前项目在多个业务路径中通过自然语言片段完成分类、过滤、打分、删除或重写，例如：

- 临时状态与稳定身份的区分。
- 视觉媒介、材质、色板和灯光的推断。
- 角色和音色的性别推断。
- 对白情绪、旁白和画面文字的识别。
- 视频提示词的自然语言删除和改写。

这些规则没有稳定边界，存在覆盖不全、误匹配、多处重复和修改不可追踪等问题。把词表移动到一个公共模块、配置文件或 YAML 只能集中问题，不能解决问题。

## 治理定义

### 禁止的语义 PATTERN

只要代码根据自然语言词项命中结果执行以下任一行为，即视为禁止：

- 推断业务类型、性别、年龄、情绪、动作、状态、风格或内容意图。
- 删除或保留业务内容。
- 修改候选分数、排序或准入结果。
- 选择资产、供应商参数、声音或生成策略。
- 将自然语言短语替换为另一段业务语义。
- 使用同义词正则模拟自然语言理解。

典型代码形态包括但不限于：

```python
any(token in text for token in (...))
sum(1 for marker in markers if marker in text)
re.search(r"词项A|词项B|词项C", text)
for source, replacement in rewrites:
    text = text.replace(source, replacement)
```

### 允许的机械规则

以下规则不推断内容语义，可以保留：

- ID、文件名、URL、Base64、JSON、SSE、占位符和协议格式。
- 明确标点、括号、数字、尺寸和宽高比语法。
- HTTP 状态码、供应商固定错误码和官方错误消息。
- 节点兼容别名、官方枚举和精确 provider 标识映射。

允许项必须能够回答其格式规范来源，且不能因自然语言同义改写而改变业务结果。

## 实施范围

### 1. 建立治理说明

在项目开发文档中记录：

- 禁止与允许边界。
- 自由文本进入系统时的唯一语义提取边界。
- 消费端不得重新扫描自然语言。
- `unknown`、`unspecified` 和 `null` 是合法状态，不得静默猜测。
- 低置信度数据必须进入复核或明确失败。
- 禁止以“临时兼容”为由增加新的关键词 fallback。

治理说明应被后续开发任务和代码审查引用。

### 2. 建立通用语义来源模型

在核心 Schema 中定义可复用的来源信息。建议最小字段：

```text
SemanticProvenance
- source: user | model | official_metadata | migration | manual_review
- evidence: list[str]
- confidence: float | null
- model: str | null
- schema_version: int
```

要求：

- `confidence` 限制在 `0.0～1.0`。
- 官方结构数据和用户显式输入可以不填写模型信息。
- 迁移结果必须标明 `source=migration`。
- 不把完整无关项目元数据传给 LLM。

### 3. 明确 Schema 版本策略

为将被后续任务升级的结构定义版本规则：

- `Role` 及声音需求
- `VoiceCatalogProfile`
- `ClipToShotsModelItem`
- `ShotPlanItem`
- `ShotManifestItem`
- 对白结构

约束：

- 新输出使用明确 `schema_version`。
- 旧版本只能通过显式迁移进入新版流程。
- 不能在模型 validator 中用关键词补出新字段。
- 不能把旧文本自动视为已经完成结构化迁移。
- Schema 升级必须说明缓存哈希是否失效。

### 4. 建立 AST 静态审计器

新增只读静态检查脚本，建议位置：

```text
scripts/check_semantic_patterns.py
```

检查范围：

- `autodrama/src/**/*.py`
- `scripts/**/*.py`

最低检测能力：

- tuple/list/set 字面量被用于 `token in text`。
- `any()`、`all()`、`sum()` 中对自然语言字符串做包含判断。
- `re.compile`、`re.search` 中包含自然语言 alternation。
- 多组自然语言 `.replace()`。
- 名称含 `PATTERNS`、`KEYWORDS`、`MARKERS`、`REWRITES` 的业务常量。
- 同一函数中对多个自然语言字面量执行 `in`/`not in`。

输出必须包含：

- 文件。
- 行号。
- 命中类型。
- 简短代码摘要。
- 是生产代码还是脚本。

审计器不能根据某个固定中文词表判断违规，否则它自身会重复同类问题。应基于 AST 结构、字符串字符类别和调用方式识别。

### 5. 建立现有违规基线

生成当前违规清单并固定为任务 2～5 的削减基线。基线只记录：

- 文件与代码位置。
- 违规类别。
- 所属后续任务。
- 当前状态。

基线不得保存或集中业务关键词内容，也不得成为运行时配置。

任务 1 阶段允许已有违规继续存在，但必须做到：

- 新增违规检查失败。
- 已有违规不能扩大匹配范围。
- 每个已有违规必须分配到任务 2～5。
- 任务 5 结束后删除基线豁免并启用零违规门禁。

## 预期涉及文件

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/core/voice_catalog.py`
- `scripts/check_semantic_patterns.py`
- `scripts/smoke/semantic_schema_contract_smoke.py`
- 项目治理或贡献说明文档

具体文件可以按实现调整，但不得把运行时语义词表移动到新公共模块。

## 非目标

- 本任务不删除 `_TEMPORARY_STATE_PATTERNS`。
- 本任务不切换音色筛选逻辑。
- 本任务不修改 `clip_to_shots` 业务输出。
- 本任务不迁移历史项目产物。
- 本任务不要求一次性消除现有全部违规。

## 实施步骤

1. 固化禁止和允许规则。
2. 设计并添加语义来源 Schema。
3. 为后续核心对象确认版本升级策略。
4. 编写 AST 审计器。
5. 对当前工作树运行审计并生成基线。
6. 给每个违规分配后续治理任务。
7. 增加 Schema round-trip 烟测。
8. 在 CI 或标准验证入口中启用“禁止新增违规”检查。

## 验证要求

不得使用 pytest。至少执行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts/check_semantic_patterns.py scripts/smoke/semantic_schema_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/semantic_schema_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/check_semantic_patterns.py
```

烟测必须覆盖：

- `SemanticProvenance` 的合法与非法置信度。
- Schema JSON round-trip。
- `unknown/unspecified` 不会被 validator 自动猜测。
- 审计器能命中已知示例。
- 审计器不会把 URL、ID、错误码和标点解析误报成业务语义规则。

## 完成定义

- 项目有明确、可引用的语义 PATTERN 治理规则。
- 通用语义来源和版本策略已经落入 Schema。
- 静态审计器可以稳定报告当前违规。
- 当前每个违规都有后续任务归属。
- 新增同类违规会被自动检查阻止。
- 未改动任何现有业务判断结果。

## 风险与回滚

### 风险

- AST 检查器初期误报机械格式规则。
- Schema 来源字段设计过度，增加所有模型负担。
- 基线豁免长期存在，变成新的技术债。

### 控制方式

- 只为机械格式规则提供带理由的代码级说明，不为业务语义规则开豁免。
- 来源模型保持最小字段集。
- 在任务 5 中把“删除全部基线豁免”列为强制验收项。

### 回滚

任务 1 不切换业务行为。若审计器误报，可暂时从强制模式退回报告模式，但不得删除治理文档和违规基线。
