# 任务 5：遗留规则清理与零违规门禁

## 任务目标

在任务 2～4 完成业务切换后，彻底清理生产代码、Mock、烟测脚本和迁移兼容层中的语义 PATTERN，将任务 1 的静态审计从“禁止新增”升级为“全项目零违规”。

本任务是治理收口任务，依赖任务 1～4 全部完成。

## 完成后的目标状态

- 生产 Python 代码中不存在通过自然语言词表或正则推断业务语义的实现。
- Mock 和烟测不再依赖中文关键词决定行为。
- 旧项目只能通过显式迁移进入新版流程。
- 不存在隐藏的 legacy fallback。
- 静态检查不再需要任何历史违规基线。
- CI 对新增语义 PATTERN 直接失败。

## 清理范围

### 1. 删除已替代的生产规则

至少确认以下实现已删除或变成纯结构消费：

- `_TEMPORARY_STATE_PATTERNS`
- 自由文本视觉媒介与风格抽取规则
- `_infer_role_gender()`
- `_voice_gender()` 中的文本子串推断
- `_infer_gender_hint()`
- 角色/音色自然语言匹配加分
- `_shot_dialogue_emotion()`
- 旁白/画外音 marker 集合
- `_exact_text()`
- `_ASPECT_RATIO_WORDS`
- `_TEXT_ARTIFACT_BAN_PATTERNS`
- `_CAMERA_REWRITES`
- `SAMPLE_TEXT_FORBIDDEN_PHRASES`

如果某项仍然存在，必须证明它只处理明确机械格式；否则任务不能完成。

### 2. 清理辅助脚本

重点检查：

- `scripts/smoke/synthesize_query_wavs.py`
- 视觉契约烟测
- 音色目录抽取和样本生成脚本
- 右码、多模态和图像审计烟测
- 所有包含 `KEYWORDS`、`PATTERNS`、`MARKERS`、`REWRITES` 的脚本

替代原则：

- 选择儿童音色等行为读取显式 catalog metadata。
- fixture 使用稳定 ID 和结构字段。
- 测试断言结构行为，不断言某些中文词一定被删除。
- 安全信息脱敏、URL 和协议格式正则可以保留。

### 3. 清理 Mock

Mock 必须根据明确输入生成确定输出：

- Schema 字段。
- 固定 fixture ID。
- 显式 provider 参数。
- 显式角色和音色绑定。

不得根据角色简介、名字、代词、情绪词或画面词选择返回值。

### 4. 清理 legacy fallback

全项目检查以下兼容形式：

- 新字段为空时扫描旧自由文本。
- 新 Schema 校验失败时调用旧关键词函数。
- provider 缺少绑定时猜测性别或情绪。
- 旧 `dialogue` 字符串在运行时重新解析。
- 缺失 `overlay_text_spec` 时扫描引号。
- 缺失 `VisualStyleSpec` 时解析 style prompt。
- 缺失稳定身份时从 `desc` 过滤生成。

允许保留的兼容能力：

- 识别旧 schema version。
- 给出明确迁移命令。
- 展示旧原始数据。
- 从备份恢复。

不允许保留的兼容能力：

- 静默生成新版语义字段。
- 在正式工作流中自动猜测并继续执行。

### 5. 收口迁移工具

迁移工具必须：

- 是显式 CLI 或独立工作流。
- 输出迁移前后版本。
- 保存来源、证据、置信度和 warnings。
- 支持 dry-run。
- 不覆盖原文件，除非用户明确执行写入模式。
- 对无法确定项返回非成功状态或待复核清单。
- 不被正常生成工作流隐式调用。

迁移完成后，应能列出仍未迁移的项目和产物。

## 零违规静态门禁

### 1. 删除历史基线

任务 1 建立的现有违规基线必须全部清空并删除。不能通过以下方式让检查通过：

- 扩大 allowlist。
- 重命名常量。
- 把词表移入 JSON/YAML/Markdown 后由运行时加载。
- 动态拼接正则逃避静态检查。
- 给业务规则标注为“机械规则”。

### 2. 强制检查范围

最终检查至少覆盖：

- `autodrama/src/**/*.py`
- `scripts/**/*.py`
- 运行时加载的 JSON/YAML 规则文件

对于 JSON/YAML，需要检查是否存在被代码作为以下用途加载的自然语言集合：

- 分类。
- 删除。
- 替换。
- 打分。
- 过滤。

Prompt 模板中的自然语言要求不属于运行时词表，但 prompt 输出必须进入结构化 Schema。

### 3. CI 行为

`scripts/check_semantic_patterns.py` 最终必须：

- 零违规返回 0。
- 任一违规返回非 0。
- 输出文件、行号和违规类型。
- 不允许默认忽略 scripts。
- 支持本地完整扫描。
- 结果稳定，不依赖网络或模型。

## 全项目复审

完成代码清理后重新执行人工复审，至少检查以下模式：

```text
PATTERN / PATTERNS
KEYWORD / KEYWORDS
MARKER / MARKERS
TOKEN / TOKENS
REWRITE / REWRITES
any(... in text ...)
re.search / re.compile
.replace(...)
score += / score -=
```

人工复审需要将命中分类为：

- 机械语法：保留并记录理由。
- 供应商固定协议：保留并记录来源。
- 业务语义：必须删除。
- 无调用遗留代码：直接删除。

特别检查没有明显命名但仍执行相同行为的 inline tuple 和局部变量。

## 预期涉及文件

本任务可能触及所有在审计中命中的文件，重点包括：

- `autodrama/src/autodrama/core/visual_contract.py`
- `autodrama/src/autodrama/services/voice_catalog_service.py`
- `autodrama/src/autodrama/providers/volcengine/audio/seed_tts.py`
- `autodrama/src/autodrama/providers/local/mock/fake.py`
- `autodrama/src/autodrama/workflows/pregen.py`
- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/utils/video_prompts.py`
- `scripts/smoke/*`
- `scripts/check_semantic_patterns.py`
- CI 配置和开发文档

## 非目标

- 不删除合法的 URL、ID、协议和错误码解析。
- 不禁止 prompt 模板使用自然语言。
- 不建立集中式业务关键词服务。
- 不为了兼容旧项目保留运行时语义猜测。
- 不在本任务重新设计任务 2～4 已经确定的 Schema。

## 实施步骤

1. 确认任务 2～4 的所有消费者已经切换。
2. 删除生产语义 PATTERN 和无调用遗留代码。
3. 删除 Mock 中的文本启发式。
4. 改造烟测和工具脚本。
5. 检查并删除所有 legacy runtime fallback。
6. 验证迁移 CLI 与正常工作流完全隔离。
7. 运行 AST 扫描并人工复核全部命中。
8. 删除任务 1 的违规基线。
9. 启用全项目零违规 CI。
10. 执行完整非 pytest 验证。
11. 更新开发文档和迁移说明。

## 验证要求

不得使用 pytest。至少执行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts
D:/miniforge3/envs/autodrama/python.exe scripts/check_semantic_patterns.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/semantic_schema_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/visual_contract_v2_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/voice_contract_v2_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/structured_shot_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_pipeline_fake_e2e_smoke.py
```

根据实际节点变更，还应执行相关直接 CLI 工作流和现有边界烟测。

验证输出必须保存到仓库 `.tmp/`，不得写入系统临时目录。

## 验收清单

- [ ] 任务 1 的违规基线已经删除。
- [ ] 静态审计全项目零违规。
- [ ] 没有把词表移动到配置或资源文件。
- [ ] 所有结构字段缺失时都明确失败、迁移或复核。
- [ ] 正常工作流不调用迁移工具。
- [ ] Mock 不依赖业务自然语言关键词。
- [ ] 烟测不依赖“某个词必须被删除”的旧行为。
- [ ] 机械正则都有明确格式用途。
- [ ] 生产消费者只读取结构化字段。
- [ ] 旧产物有可追踪迁移路径。
- [ ] 所有验证均未使用 pytest。

## 完成定义

- 全项目语义 PATTERN 静态检查为零。
- CI 已启用零违规强制门禁。
- 任务 2～4 引入的新 Schema 是唯一运行时事实来源。
- 无任何静默 legacy 语义 fallback。
- 辅助脚本和 Mock 遵守相同规则。
- 治理文档、迁移文档和验证记录完整。

## 风险与回滚

### 风险

- 清理遗留 fallback 后，历史项目首次运行会失败。
- 静态审计器可能阻止合法机械规则修改。
- 某些低频脚本依赖旧词表但此前未被纳入常规验证。

### 控制方式

- 在删除 fallback 前完成迁移清单和 dry-run。
- 对机械规则记录协议来源并补充针对性烟测。
- 扫描并执行所有受影响脚本的最小验证路径。

### 回滚

- 可以回滚具体消费者或 Schema 版本。
- 可以恢复迁移前项目数据。
- 零违规门禁如有误报可以暂时调整检测器实现。
- 不得恢复任何自然语言语义词表、正则或 rewrite fallback。
