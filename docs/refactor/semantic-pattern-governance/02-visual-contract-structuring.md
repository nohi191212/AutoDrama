# 任务 2：视觉契约与人物状态结构化

## 任务目标

删除视觉链路中通过自然语言词表区分稳定身份、临时状态和视觉风格的逻辑，使以下结构成为唯一事实来源：

- `VisualStyleSpec`
- `RoleAppearance.identity_invariants`
- `RoleAppearance.wardrobe`
- `RoleAppearance` 变体有效期
- `ShotEntityState`

本任务依赖任务 1 的治理规则、Schema 版本策略和静态审计基线。

## 当前问题

主要问题集中在：

- `autodrama/src/autodrama/core/visual_contract.py`
  - `_TEMPORARY_STATE_PATTERNS`
  - `build_visual_style_spec()` 中的 2D/3D 关键词判断
  - 材质、色板、灯光和镜头关键词抽取
  - `避免|禁止` 负面约束正则
- 角色抽取和角色板提示词依赖上游自由文本质量。
- `identity_brief()` 和 `sanitize_identity_values()` 在消费阶段补救上游字段污染。

这种补救会删除整条身份信息，无法区分：

- 永久持有物与当镜手持物。
- 正式受伤造型变体与当镜伤势。
- 固定发光视觉标志与能量爆发状态。
- 稳定姿态特征与动作。

## 目标数据流

```text
视觉配置 / 用户输入
    -> 显式 VisualStyleSpec
    -> 提示词渲染

角色剧情文本
    -> role extract 结构化输出
    -> RoleAppearance 稳定字段与正式变体

镜头剧情文本
    -> clip_to_shots 结构化输出
    -> ShotEntityState
```

消费端不得重新扫描这些字段的自然语言内容。

## 实施范围

### 1. 将 `VisualStyleSpec` 变成配置拥有的契约

配置应能直接表达：

```yaml
visual_style:
  schema_version: 2
  medium: stylized_3d_cg
  render_engine_language:
    - ...
  materials:
    - ...
  palette:
    - ...
  lighting:
    - ...
  camera:
    - ...
  negative_constraints:
    - ...
```

要求：

- `medium` 不再由 `"二维"`、`"3d"`、`"渲染"` 等词推断。
- 材质、色板、灯光和镜头不再从自由文本分句抽取。
- `render_engine_language` 可以保留自由描述，但只用于渲染，不反向影响其他字段。
- 用户覆盖必须输出完整或明确的字段级 patch。
- 冲突检测只比较结构字段，不解析句子。

### 2. 更新配置 Schema 与加载逻辑

实现以下行为：

- 新配置直接验证 `VisualStyleSpec`。
- 缺少可选字段时使用空列表，不扫描说明文本。
- 缺少必需 `medium` 时明确失败或要求迁移。
- 记录 `source=yaml/user_override/migration`。
- style spec 版本和内容继续参与缓存或生成指纹。

不得保留“如果没有结构字段则调用旧解析器”的长期 fallback。

### 3. 强化角色抽取输出边界

更新角色抽取 Schema 和提示词，使模型明确区分：

#### 稳定身份字段

- 脸部和五官。
- 发型与稳定发色。
- 体型。
- 年龄感。
- 稳定视觉标志。

#### 稳定服装字段

- 当前正式造型的服装、鞋履、配饰。

#### 正式变体资产

- 持续跨多个镜头或事件区间的换装。
- 持续伤势、污损、湿身、伪装等需要独立资产的状态。
- 必须具备 `asset_role=variant`、参考基础造型和有效事件范围。

#### 禁止写入稳定身份的内容

- 单镜动作和姿势。
- 当镜手持物。
- 当镜情绪。
- 瞬时能量或光效。
- 未来事件描述。

提示词负责语义区分，Schema 负责字段约束；运行时代码不做词表清洗。

### 4. 简化 `identity_brief()`

目标行为：

- 只组合 `identity_invariants` 和 `wardrobe`。
- 保持顺序去重。
- 不执行自然语言正则。
- 不读取 `desc` 或 `visual_features` 作为静默 fallback。
- 数据缺失时返回明确校验错误，错误中指出需要重新执行哪个上游节点。

`sanitize_identity_values()` 应删除，或降级为仅做空白、标点和精确重复项清理，不能判断内容语义。

### 5. 接入 `ShotEntityState`

本任务负责明确视觉消费者如何使用已有 `ShotEntityState`：

- 角色基础身份来自 `RoleAppearance`。
- 当镜姿态、情绪、伤势、手持道具和能量状态来自 `ShotEntityState`。
- 两类数据分别渲染到提示词的稳定身份区和当前镜头状态区。
- 当镜状态不能反写到角色基础资产。
- 正式变体通过 `appearance_id` 引用，不依靠文字描述猜测。

`clip_to_shots` 具体输出改造在任务 4 完成；本任务可以先定义并验证消费接口。

### 6. 历史数据迁移

提供一次性迁移路径：

- 读取旧 `visual_style_prompt`。
- 通过用户显式配置或专用结构化迁移调用生成 `VisualStyleSpec v2`。
- 读取旧 `RoleAppearance`。
- 将稳定身份、正式变体和无法确定项分别输出。
- 无法确定的项记录 warning，不自动删除。
- 保存迁移来源、证据和置信度。
- 迁移前保留原产物备份或版本化副本。

迁移提示词只包含完成该内容任务需要的数据，不附加项目路径、无关项目 ID 等元数据。

## 预期涉及文件

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/core/visual_contract.py`
- `autodrama/src/autodrama/config.py`
- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/services/role_service.py`
- `autodrama/src/autodrama/prompts/role_extract_primary/default.md`
- `autodrama/src/autodrama/prompts/role_extract_functional/default.md`
- 各角色板 prompt 变体
- 配置示例文件
- 视觉契约迁移脚本
- `scripts/smoke/visual_contract_v2_smoke.py`

## 非目标

- 不在本任务修改音色选择。
- 不在本任务完成结构化对白。
- 不增加新的临时状态词表。
- 不把 `_TEMPORARY_STATE_PATTERNS` 移入配置文件。
- 不要求使用 LLM 在每次渲染时重新审查身份字段。

## 实施步骤

1. 定义 `VisualStyleSpec v2` 和配置兼容策略。
2. 更新配置示例和加载器。
3. 更新角色抽取 Schema 与提示词。
4. 明确基础造型和正式变体的字段约束。
5. 改造 `identity_brief()` 为纯结构消费。
6. 建立 `ShotEntityState` 消费接口。
7. 实现一次性历史数据迁移。
8. 删除 `_TEMPORARY_STATE_PATTERNS` 和自由文本视觉风格解析。
9. 更新视觉相关烟测和缓存指纹检查。
10. 从任务 1 基线中清除视觉类违规。

## 验证要求

不得使用 pytest。至少执行：

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src scripts/smoke/visual_contract_v2_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/visual_contract_v2_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/check_semantic_patterns.py
```

烟测场景至少包括：

- 同一稳定身份配多个不同 `ShotEntityState`，基础身份保持不变。
- 固定发光标志不会因为含“发光”而被删除。
- 当镜手持道具只出现在当前状态区。
- 正式受伤变体通过 `appearance_id` 引用。
- 视觉风格自由说明中出现“二维/三维”字样不会改变显式 `medium`。
- 缺失稳定身份时明确失败，不从 `desc` 猜测。
- v1 历史产物经过显式迁移后可进入 v2 流程。

## 完成定义

- `_TEMPORARY_STATE_PATTERNS` 已删除。
- `build_visual_style_spec()` 不再解析自然语言语义。
- `identity_brief()` 不再使用语义正则或词表。
- 视觉配置使用结构化 `VisualStyleSpec`。
- 稳定身份、正式变体和镜头状态有清晰数据边界。
- 历史数据有显式迁移路径。
- 视觉类违规从静态审计基线中清零。

## 风险与回滚

### 风险

- 旧配置只有一段自由风格文本，无法直接满足 v2。
- 角色抽取模型可能把临时状态继续写入稳定字段。
- 删除 fallback 后暴露已有脏数据。

### 控制方式

- 先提供迁移命令和清晰错误信息，再切换消费者。
- 要求抽取结果带证据和置信度。
- 对低置信度角色造型进入人工复核，不使用运行时词表补救。

### 回滚

- 保留迁移前 JSON 和配置备份。
- 可以暂时读取 v1 产物用于迁移展示，但不能重新启用关键词推断。
- 若新抽取质量不足，回滚上游模型版本，不回滚到运行时语义清洗。
