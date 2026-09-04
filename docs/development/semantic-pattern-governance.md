# 自然语言语义 PATTERN 治理规范

## 目的

业务代码不得通过零散自然语言关键词、同义词正则或替换表猜测内容语义。自由文本只允许在明确的语义提取边界被理解一次；提取结果必须结构化、版本化并保存，后续消费者只读取结构字段。

本规范适用于 `autodrama/src/**/*.py`、`scripts/**/*.py` 以及运行时加载的
JSON/YAML，并作为新增代码和代码审查的强制边界。

## 禁止规则

当自然语言词项的命中结果影响以下任一行为时，属于禁止的语义 PATTERN：

- 推断类型、性别、年龄、情绪、动作、状态、风格或内容意图。
- 删除、保留或重写业务内容。
- 修改候选评分、排序或准入结果。
- 选择资产、供应商参数、声音或生成策略。
- 用同义词正则、关键词数组或替换表模拟自然语言理解。

把词项移动到公共模块、配置文件或 YAML 不会使其合规。以“临时兼容”为由新增关键词 fallback 同样不被允许。

## 允许规则

不推断内容含义的机械规则可以保留：

- ID、文件名、URL、Base64、JSON、SSE 和占位符格式。
- 明确的标点、括号、数字、尺寸和宽高比语法。
- HTTP 状态码、供应商固定错误码和官方错误消息。
- 节点兼容别名、官方枚举和精确 provider 标识映射。

允许项必须有明确格式或协议来源，且自然语言同义改写不会改变其业务结果。静态审计器发生机械规则误报时，应修改检测精度或增加带理由的代码级排除；不得为业务语义规则增加豁免。

## 唯一语义提取边界

自由文本进入系统时，可由 LLM、人工输入或可信官方元数据完成一次结构化提取。提取边界必须：

1. 输出严格 Schema，禁止额外字段。
2. 保存 `SemanticProvenance`，记录来源、证据、置信度、模型和 Schema 版本。
3. 没有证据时输出 `unknown`、`unspecified` 或 `null`；这些都是合法状态。
4. 低置信度或相互冲突的数据进入人工复核或明确失败，不得静默猜测。
5. 只向 LLM 提供内容任务所需信息，不附加无关项目元数据。

结构化结果持久化后，任何资产生成、声音选择、镜头、后期或 provider 节点都不得重新扫描原始自然语言。

## Schema 版本与迁移

以下契约的后续结构升级都必须声明 `schema_version`：

- `Role` 及角色声音需求
- `VoiceCatalogProfile`
- `ClipToShotsModelItem`
- `ShotPlanItem`
- `ShotManifestItem`
- 结构化对白

版本规则：

- 新输出写入明确版本；消费者只接收其明确支持的版本。
- 旧版本只能通过独立、显式的迁移步骤进入新版流程。
- validator 不得用关键词从旧文本补出新字段，也不得把旧文本自动视为已迁移。
- 迁移来源必须为 `source=migration`，原产物或迁移前快照必须可追溯。
- Schema 升级必须检查所有包含序列化结构或 prompt 输入的缓存哈希；字段增加、默认值变化、序列化变化或 prompt 变化时必须使相关缓存失效。
- 迁移无法确定的值保持未知，不提供运行时 legacy fallback。

`SemanticProvenance.schema_version=1` 是通用来源契约的首版。它独立于承载该来源的业务对象版本。

## 零违规审计

运行：

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/check_semantic_patterns.py
```

检查器没有历史基线或业务 allowlist：Python、运行时 JSON/YAML 中只要发现一项
可疑语义规则就返回非零。机械协议误报应通过提高检测精度解决，不得加入文件级或
规则级豁免。

当前允许的自然语言集合仅限 prompt 正文和结构化业务数据；它们不得使用
`PATTERN`、`KEYWORD`、`MARKER`、`REWRITE`、`PHRASE` 等规则键由运行时代码执行
分类、删除、替换、打分或过滤。

## 任务 5 验证记录

收口验证使用仓库指定 Python 环境执行，日志保存到
`.tmp/semantic-pattern-governance/`：

- `compileall` 覆盖 `autodrama/src` 与 `scripts`。
- 零违规静态审计。
- 语义 Schema、视觉 v2、声音 v2、结构化镜头契约 smoke。
- `shot_pipeline_fake_e2e_smoke.py`，覆盖到镜头清单及 fake 视频生成。

人工复审同时覆盖 `PATTERN/KEYWORD/MARKER/REWRITE/TOKEN` 命名、
`any/all` 包含判断、正则、文本替换和评分增量。保留项及理由如下：

- Prompt 占位符、镜头 ID、URL、Base64、JSON/SSE 与文件名正则：机械语法。
- 图片 provider 的重试、安全审核和固定错误消息：供应商协议错误识别。
- 火山音色文档的章节名、布尔值和情绪别名：官方文档结构及精确枚举映射；
  不对角色或对白自由文本做子串推断。
- 尺寸分隔符、标点切句、ASS 转义：确定性格式转换。

Mock 和 smoke 不再通过人物简介、代词、情绪或画面关键词选择业务结果。测试 fixture
只使用稳定 ID、显式 Schema 字段或机械格式断言。
