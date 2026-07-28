# AutoDrama《扫地八十年》逐节点工作流改造方案

> 审计对象：`config.saodi_bashinian_terra_image2.yaml` 对应工作流。
> 本文只描述可复用框架改造，不修改本次运行生成的 JSON、图片、音频或视频中间产物。本次失败产物应保留为 golden failure regression fixture。

## 1. 改造目标

本次成片问题的主因链是：角色永久身份混入剧情状态，污染关键帧提示；错误关键帧继续进入视频生成；时长、引用预算和语义审计没有形成硬门禁；Postgen 审计中断后仍存在可被误认为最终成片的硬拼文件。

改造完成后，工作流必须满足以下原则：

1. 永久身份、逐镜状态、道具时间线和场景变体分别建模。
2. 所有视觉节点共享唯一的 `VisualStyleSpec`。
3. 分镜总时长必须处于目标时长的 95%–105%。
4. 工作流只能从白名单字段读取关键帧上下文，并在过滤未来事件与状态后渲染为自然语言提示。
5. 精确文字由后期合成，不交给图像或视频模型生成。
6. `completed` 不再等于“文件存在”；所有 required gates 必须 `accepted`。
7. 回退到最早污染节点，只局部重生成受影响资产。

### 三层职责边界

#### YAML 配置

保存剧集相关设定和视觉约束，例如目标时长、画幅、语言、视觉媒介、材质、色板、灯光与审计阈值。

#### 工作流代码

负责确定性的剧集无关工作：ID 分配、schema 映射、字段筛选、状态查询、预算计算、时间线过滤、冲突检测、引用裁剪、fingerprint、结果校验、缓存、回退和交付状态。

#### 提示词模板

只保存剧集无关、需要裸模完成的语义理解、内容生成、创意设计和主观评价规则。模板不得硬编码项目标题、项目路径、人物、道具、剧情、节点名或内部 schema 字段名。

#### 运行时注入

工作流读取 YAML 和上游产物，将当前任务真正需要的剧集内容转换成人类可读的自然语言上下文后再发送给模型。剧集内容可以作为运行时任务输入，但模板本身不得固化这些内容，也不能要求裸模理解内部数据结构。

强制规则：

1. 模型只负责无法通过确定性规则完成的语义理解、内容生成、创意设计和主观质量判断。
2. ID、字段转换、预算、状态机、引用选择、校验、缓存和回退不得交给模型。
3. API 输出 schema 可以约束模型响应格式，但模板正文不应讲解项目内部字段；工作流负责响应映射。
4. YAML 视觉设定必须先由代码检测冲突，再渲染为自然语言视觉简报。
5. 项目特定人物、道具和镜头只允许出现在运行时输入与 golden fixture，不得写入通用模板。

## 2. 实施优先级

| 优先级 | 节点数 | 目标 |
| --- | ---: | --- |
| P0 | 9 | 阻断状态污染、超时长、错误关键帧和未审计交付 |
| P1 | 12 | 统一视觉风格、身份资产、道具状态和场景连续性 |
| P2 | 6 | 完善事实溯源、预算分配和工程可维护性 |

推荐顺序：先完成 4、5、8、20、23、24、25、26、27；再完成 P1 资产链；最后补齐 P2 的事实溯源与效率优化。

## 3. 逐节点修改方案

### 01. `script_import`

- 阶段：剧本入口
- 优先级：P2
- 改造目标：把成熟剧本转成后续可验证的事实底座，而不仅是自由文本摘要。

#### 当前问题

当前角色、道具、场景抽取基本正确，但事件顺序、首次出现镜头和状态变化没有稳定 ID，下游只能重复从文本猜测。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/script_nodes.py`
- `autodrama/src/autodrama/services/script_service.py`
- `autodrama/src/autodrama/prompts/script_import/default.md`

#### 数据契约变化

```text
代码侧新增 StoryFactBundle：events[]、entity_mentions[]、timeline_order。
裸模只返回事件语义与证据原文；event_id 和 source_span 由代码稳定生成。
```

#### 实施步骤

1. 扩展 ScriptImportOutput，保留现有字段兼容旧项目，同时增加结构化 facts。
2. 通用模板只要求识别事件、参与实体、前置条件、结果和证据原文，不出现 StoryFactBundle、event_id、source_span 等内部名称。
3. 工作流对模型结果规范化，生成稳定 ID，并在原剧本中确定性定位证据区间。
4. 节点落盘后由代码检查 ID 唯一性、证据可定位性和实体引用完整性，再把内容摘要写入 fingerprint。

#### 确定性门禁

- 所有事件都有 event_id 与 source_span。
- 实体引用必须能解析到 roles / props / layouts。
- 不得把推测事实标成 confirmed。

#### 失败与回退

结构化 facts 校验失败时允许重试 LLM；重试耗尽则阻断，不用空 facts 继续。

#### 验收与 Smoke

- 现有剧本可定位“柳菡烟倒下”“玉瓶交付”“心血触炉”三个独立事件。
- contract smoke 验证旧字段仍可读取，新字段能 round-trip。

#### 上下游依赖

无上游改造依赖；为 2、3、6、11、13、19 提供统一事实源。

### 02. `script_detail_expand`

- 阶段：剧本扩写
- 优先级：P2
- 改造目标：扩写只补表演细节，不改变事实时序和实体状态。

#### 当前问题

现有输出只有 expanded_script，虽未破坏本片主线，但缺少“哪些内容是新增、哪些事实不可改”的机器可验证边界。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/script_nodes.py`
- `autodrama/src/autodrama/services/script_service.py`
- `autodrama/src/autodrama/prompts/script_detail_expand/default.md`

#### 数据契约变化

```text
代码侧输出 ExpansionTrace：source_event_id、added_detail、fact_effect。
模型只做扩写；字段映射和 fact_effect 判定由工作流完成。
```

#### 实施步骤

1. 工作流把既定事实渲染成人类可读的“不可改变事实”区块传给模型，不暴露 script_import.events 等节点或字段名。
2. 通用模板只约束扩写不得改变输入中的人物、因果、时序与道具状态。
3. 代码新增扩写前后事件签名比较器，并将新增台词、动作、环境细节分类写入 ExpansionTrace。
4. 发现事实漂移时只重做本集扩写，不使原始导入失效。

#### 确定性门禁

- 事件集合与拓扑顺序不变。
- 不得提前引入道具或改变人物年龄阶段。
- 新增台词必须有 speaker_id。

#### 失败与回退

比较失败则保留原成熟剧本并标记 expansion_skipped，不允许使用漂移版本。

#### 验收与 Smoke

- 扩写前后事件哈希一致。
- smoke 注入一个提前出现的小炉，validator 必须拒绝。

#### 上下游依赖

依赖 1 的事件 ID；输出供 3 和 19 使用。

### 03. `script_novel_extract`

- 阶段：剧本汇总
- 优先级：P2
- 改造目标：形成带溯源的集级叙事包，避免后续节点各自解释长文本。

#### 当前问题

当前集级文本可用，但缺少顶层结构化事件表、时间阶段和证据引用。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/script_nodes.py`
- `autodrama/src/autodrama/services/script_service.py`
- `autodrama/src/autodrama/prompts/script_novel_extract/default.md`

#### 数据契约变化

```text
代码侧 NovelExtractEpisode 增加 beats[]、time_periods[]、event_refs[]。
模型返回 beat 语义与证据原文；代码匹配并写入内部 event_refs。
```

#### 实施步骤

1. 通用模板要求从输入内容提炼 narrative beat、情绪、时间阶段、必达视觉事件和证据，不提 episode_key 或 event_refs。
2. 工作流按当前集范围组织输入与保存 provenance，多集边界不交给模型猜测。
3. 代码根据证据、实体和顺序匹配事件 ID，并将少年/老年等阶段写入结构化字段。
4. 生成完成后由代码校验 event coverage，核心事件不得遗漏。

#### 确定性门禁

- confirmed event 覆盖率 100%。
- beat 的时间阶段与事件顺序无冲突。
- 所有实体 ID 可解析。

#### 失败与回退

遗漏核心事件时自动带缺失清单重试；仍失败则阻断角色与分镜节点。

#### 验收与 Smoke

- 输出明确包含 flashback/少年阶段。
- smoke 验证每个核心 event 恰好被至少一个 beat 引用。

#### 上下游依赖

依赖 1、2；为 4、6、7、11、13、19 提供输入。

### 04. `key_vision_prompt`

- 阶段：视觉总纲
- 优先级：P0
- 改造目标：配置中的视觉方向成为全项目唯一风格契约。

#### 当前问题

模板硬编码二维水墨，直接覆盖配置要求的风格化 3D CG。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/services/director_service.py`
- `autodrama/src/autodrama/workflows/nodes/director_nodes.py`
- `autodrama/src/autodrama/prompts/key_vision_prompt/default.md`

#### 数据契约变化

```text
代码侧新增 VisualStyleSpec：medium、render_engine_language、materials、palette、lighting、camera、negative_constraints。
工作流将其渲染为自然语言风格简报；内部字段名不发送给模型。
```

#### 实施步骤

1. 从 YAML 配置解析 VisualStyleSpec，并在项目初始化时冻结 style_spec_version。
2. 工作流先做 2D/3D、写实/风格化等确定性冲突检测，再渲染成人类可读的视觉约束。
3. 删除模板中的固定 medium；通用模板只要求严格遵守输入的视觉简报并完成主视觉构图任务。
4. 把字段到最终自然语言提示的映射与版本落盘，但不把字段名或项目元数据发给裸模。

#### 确定性门禁

- 代码渲染的自然语言简报覆盖 YAML 中所有必需风格维度。
- 工作流在调用 API 前拒绝与 negative constraints 冲突的文本。
- 风格来源只能是 YAML 配置或显式用户覆盖。

#### 失败与回退

风格冲突不自动猜测，停止在提示节点并输出冲突字段。

#### 验收与 Smoke

- 本配置渲染结果唯一指向 stylized_3d_cg。
- smoke 扫描所有视觉模板，确保没有固定 medium 覆盖配置。

#### 上下游依赖

依赖 3；是 5、9、15、16、21、23 的 P0 前置。

### 05. `key_vision_image_generation`

- 阶段：视觉总纲
- 优先级：P0
- 改造目标：只有风格合格的主视觉才能成为下游参考。

#### 当前问题

二维主视觉生成成功即完成节点，图像审计关闭后仍被当作 style reference。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/director_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
- `autodrama/src/autodrama/services/asset_service.py`
- `autodrama/src/autodrama/workflows/nodes/__init__.py`

#### 数据契约变化

```text
输出增加 style_spec_version、prompt_fingerprint、audit_status、attempts[]。
accepted 才能发布 reference_asset_id。
```

#### 实施步骤

1. 生成后强制运行轻量 style gate，不受 enable_image_audit 总开关影响。
2. 审计比较 medium、材质、光照、色板和文字污染；分数与证据落盘。
3. 失败时基于结构化差异改写提示，最多重试有限次数。
4. 只有 accepted 版本写入项目风格引用；失败版本保留为诊断资产。

#### 确定性门禁

- medium 与 VisualStyleSpec 完全一致。
- 无水印、乱码和非预期 UI/排版。
- 参考图尺寸、色彩空间和文件完整性合格。

#### 失败与回退

重试耗尽后阻断角色板/道具/场景生成，禁止静默使用最近一张图。

#### 验收与 Smoke

- 向审计器注入二维样本时必须拒绝。
- 节点 completed 必须蕴含 audit_status=accepted。

#### 上下游依赖

依赖 4；通过后解锁 9、15、16。

### 06. `role_extract_primary`

- 阶段：角色建模
- 优先级：P1
- 改造目标：把主角的稳定身份与剧情阶段变体完整物化。

#### 当前问题

节点识别到少年叶凡，但只写入 notes，未形成可引用的 appearance。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/services/role_service.py`
- `autodrama/src/autodrama/prompts/role_extract_primary/default.md`

#### 数据契约变化

```text
代码侧 RoleAppearanceExtractItem 增加 time_period、age_band、identity_invariants、valid_from_event、valid_to_event。
模型返回阶段差异、稳定特征与证据；ID 和有效区间由代码映射。
```

#### 实施步骤

1. 通用模板要求识别角色的长期稳定外貌和剧情中明确出现的年龄/造型阶段，不出现内部 appearance 字段名。
2. 工作流根据事件表和证据枚举年龄、伪装、战损等必须变体，并分配 identity_group_id。
3. 代码只将脸型、体型、发型等稳定内容写入 invariant，将服装按变体保存。
4. 对每个剧情阶段做 appearance coverage 检查；模板与 smoke 均不得硬编码本剧角色。

#### 确定性门禁

- 每个明确年龄阶段至少一个 appearance。
- appearance 有合法有效区间。
- 剧情动作、持有物、情绪不得进入 identity_invariants。

#### 失败与回退

缺变体时带缺失阶段重试；仍缺失则 role_finalize 不得运行。

#### 验收与 Smoke

- 叶凡同时产出 young 与 old appearance。
- smoke 验证“手持小炉”不能进入 invariant。

#### 上下游依赖

依赖 3 的 time_periods；供 8、9、23 使用。

### 07. `role_extract_functional`

- 阶段：角色建模
- 优先级：P1
- 改造目标：让功能角色稳定、可区分，又避免无谓增加视频引用负载。

#### 当前问题

三名弟子外貌信息不足、身份辨识度弱，后续又作为多个独立参考塞进同一镜头。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/services/role_service.py`
- `autodrama/src/autodrama/prompts/role_extract_functional/default.md`

#### 数据契约变化

```text
代码侧增加 visual_distinction_tokens、crowd_eligible、narrative_weight。
模型只提出通用的视觉区分设计；降级和引用模式由代码决定。
```

#### 实施步骤

1. 通用模板要求为容易混淆的功能角色设计清楚且互不冲突的轮廓、脸型、色彩点和服装层级。
2. 工作流按叙事权重和 provider 预算判断是否需要独立角色板，不把 crowd_eligible 等内部字段发给模型。
3. 代码运行同场角色 pairwise distinction validator。
4. 工作流写入 recommended_reference_mode；无法唯一判断时才调用通用语义复核。

#### 确定性门禁

- 同场角色区分 token 不冲突。
- 低权重角色不得强制占用视频身份引用。
- 不得凭空增加具名角色。

#### 失败与回退

区分度不足时重做描述；无法区分则合并为群众组并保留台词 speaker 映射。

#### 验收与 Smoke

- 三弟子至少在轮廓/色彩两个维度可分。
- 群体镜引用预算 smoke 不超过 provider 上限。

#### 上下游依赖

依赖 3；供 8、9、20、23 使用。

### 08. `role_finalize`

- 阶段：角色建模
- 优先级：P0
- 改造目标：建立干净、不可污染的角色身份资产与逐镜状态入口。

#### 当前问题

当前把 appearance_desc、brief、visual_features、clothing、prompt_hint 全量串联，未来动作和小炉状态进入永久描述。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/prompts/role_finalize_audit/default.md`
- `autodrama/src/autodrama/repositories/project_repo.py`

#### 数据契约变化

```text
RoleAppearance 仅含 identity_invariants 与 wardrobe。
新增 ShotEntityState：pose、emotion、injury、held_props、energy_state、event_refs。
废弃聚合式 desc 作为下游图像标签。
```

#### 实施步骤

1. 迁移 schema：旧 desc 只作 legacy_source，不再自动注入提示。
2. 代码按 identity_group 合并角色、保留变体、构建 event→state transition 并验证有效区间。
3. 确定性可判定的字段归类、状态区间、引用完整性不得交给 LLM。
4. 只有语义重复是否为同一角色等不确定问题才调用 audit 裸模；模板只描述通用判重原则，不出现字段名或本剧内容。
5. 节点 fingerprint 纳入角色变体、事件表与风格版本。

#### 确定性门禁

- 身份字段不得含动作、姿态、持有物或未来事件。
- 所有时间阶段都有 appearance。
- state 引用的 prop 与 event 必须存在。

#### 失败与回退

确定性校验失败直接阻断；禁止“保留抽取结果继续”绕过契约。

#### 验收与 Smoke

- 第 1–28 镜角色身份描述中不存在“激活小炉”。
- 少年镜能解析到 young appearance。
- 新增 role_state_contract_smoke 覆盖未来信息泄漏。

#### 上下游依赖

依赖 6、7；是 9、20、23、26 的最关键 P0 前置。

### 09. `roleboard_prompt`

- 阶段：角色资产
- 优先级：P1
- 改造目标：角色板只表达身份与指定变体，并严格继承统一风格。

#### 当前问题

提示受二维主视觉和自由描述影响，输出宣纸水墨；身份字段还可能夹带事件动作。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/role_nodes.py`
- `autodrama/src/autodrama/repositories/roleboard_prompt_repo.py`
- `autodrama/src/autodrama/prompts/roleboard_prompt/default.md`
- `autodrama/src/autodrama/core/schemas.py`

#### 数据契约变化

```text
代码侧 RoleboardPromptItem 增加 appearance_id、style_spec_version、included_fields、excluded_state_fields。
发送给模型的只是自然语言身份简报与 YAML 风格简报。
```

#### 实施步骤

1. 工作流用字段白名单选择脸、体型、发型、服装和稳定标志，并在发送前删除姿态、情绪、持有物和事件状态。
2. 工作流将 YAML 的 medium、材质、灯光渲染为自然语言，不发送 VisualStyleSpec 字段名。
3. 通用模板只要求表现长期稳定身份、排除临时状态并产出标准角色设定图描述。
4. 每个 appearance 单独调用；年龄阶段分流由代码完成，模板不包含少年/老年等项目实例。

#### 确定性门禁

- 代码侧 provenance 能由最终提示反向追溯到 appearance_id，但该 ID 不发送给模型。
- 发送内容中无临时状态泄漏。
- 自然语言风格简报与 YAML 及主视觉契约一致。

#### 失败与回退

缺 appearance 或风格契约时停止，不使用角色名+自由文本临时拼接。

#### 验收与 Smoke

- 叶凡生成两张独立角色板提示。
- 快照 smoke 确认不含“瘫坐/小炉/御剑”。

#### 上下游依赖

依赖 4、5、8；供 10、23、26 使用。

### 10. `roleboard_image_generation`

- 阶段：角色资产
- 优先级：P1
- 改造目标：产生可稳定复用、可审计的身份参考图。

#### 当前问题

六张角色板风格不一致，少年变体缺失，功能角色相似；生成成功被等同于资产合格。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
- `autodrama/src/autodrama/services/asset_service.py`
- `autodrama/src/autodrama/core/schemas.py`

#### 数据契约变化

```text
每项输出 appearance_id、identity_score、style_score、distinction_score、audit_status。
accepted 资产才写入 RoleAppearance.asset_ids。
```

#### 实施步骤

1. 生成正面/侧面/背面时锁定 seed 或 identity reference。
2. 强制角色板审计：身份一致、风格一致、视图完整、无文字、无道具污染。
3. 对同场功能角色做 pairwise distinction 检查。
4. 失败只重做对应 appearance，不覆盖已通过版本。

#### 确定性门禁

- identity/style 分数达配置阈值。
- 每个 required appearance 均有 accepted 资产。
- 三视图不得换脸或换服装。

#### 失败与回退

重试耗尽则阻断关键帧；允许非关键群众角色降级为 crowd archetype。

#### 验收与 Smoke

- young/old 叶凡均有独立 accepted roleboard。
- 审计关闭配置不能绕过 required identity gate。

#### 上下游依赖

依赖 9；为 23、24、26 提供身份参考。

### 11. `prop_extract`

- 阶段：道具建模
- 优先级：P2
- 改造目标：把道具的存在时机、持有人和状态转移结构化。

#### 当前问题

小炉、玉瓶、木杖抽取完整，但 first_seen、holder 和状态触发仍主要是文本。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/services/script_service.py`
- `autodrama/src/autodrama/prompts/prop_extract/default.md`

#### 数据契约变化

```text
代码侧 Prop 增加 first_available_event、holders[]、state_transitions[]、mutual_exclusion_group。
模型返回道具语义、状态变化和证据；代码生成事件引用与时间线。
```

#### 实施步骤

1. 通用模板只要求识别道具、外观状态、交付/归还/激活等变化及证据，不出现 StoryFactBundle 或内部字段名。
2. 工作流将模型证据匹配到事件，区分 ownership 与 current_holder。
3. 代码为状态建立显式状态机并生成 first_available_event。
4. 代码校验触发事件前不可使用目标状态；不确定语义可重试模型，但最终拓扑由代码裁决。

#### 确定性门禁

- 每个状态变化都有触发事件。
- holder 区间不重叠。
- 首次可用事件不晚于首次合法引用。

#### 失败与回退

状态不明时保持 unknown 且禁止图像节点主动补全，不猜测为 activated。

#### 验收与 Smoke

- 小炉 activated 只能从心血触炉事件之后成立。
- 玉瓶交付镜 current_holder 能正确切换。

#### 上下游依赖

依赖 1、3；供 12、15、20、23 使用。

### 12. `prop_finalize`

- 阶段：道具建模
- 优先级：P1
- 改造目标：完成道具去重的同时保留完整时间线和状态差异。

#### 当前问题

现有常态/激活态可用，但 finalize 未把 owner/holder/availability 固化为下游契约。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/prompts/prop_finalize/default.md`

#### 数据契约变化

```text
PropAsset 表示视觉状态；PropTimeline 表示何时可引用。
去重不得跨 mutual_exclusion_group 或合并语义不同状态。
```

#### 实施步骤

1. 代码先按规范化 identity 与 state 做确定性去重，不把不同状态合成模糊描述。
2. 只有别名或语义是否同一道具无法确定时才调用裸模；模板使用通用判重规则，不暴露 PropTimeline 字段。
3. 建立 episode/shot 可查询的 allowed_prop_assets API，并验证 holder 与 state transition 拓扑。
4. 将旧 owner 字段迁移为 ownership，避免与 current_holder 混用。

#### 确定性门禁

- 每个资产状态有合法有效区间。
- 引用 activated asset 必须满足 trigger。
- 去重后所有 event_refs 仍可解析。

#### 失败与回退

去重不确定时保留两个候选并阻断自动选取，交给审计而非静默合并。

#### 验收与 Smoke

- shot 21 查询结果不允许 activated furnace。
- shot 26 同时解析玉瓶与小炉但 holder/state 正确。

#### 上下游依赖

依赖 11；供 15、17、20、23、25 使用。

### 13. `layout_extract`

- 阶段：场景建模
- 优先级：P2
- 改造目标：场景资产明确时段、拓扑与可复用机位。

#### 当前问题

主场景抽取正确，但闪回时段和山门复用逻辑未结构化。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/prompts/layout_extract/default.md`

#### 数据契约变化

```text
代码侧 Layout 增加 topology_id、time_period、coverage_zones、lighting_states。
模型返回空间语义、时段和证据；内部 ID、坐标与变体关联由代码生成。
```

#### 实施步骤

1. 通用模板要求区分物理空间、时段与光照变化，并抽取入口、活动区等可见空间关系。
2. 工作流根据证据生成 topology_id、coverage zone ID 和 base_layout_id，不把这些字段名发给模型。
3. 代码判断闪回等时段是否复用同一拓扑，并建立 delta。
4. 根据提取结果生成稳定场景坐标和屏幕方向约束。

#### 确定性门禁

- 同一 topology 的变体不能改变门窗/轴线。
- 每个镜头场景引用有合法 time_period。
- coverage zone ID 唯一。

#### 失败与回退

时段不明确则使用 base，但禁止自动套用强光/赤光 variant。

#### 验收与 Smoke

- 山门现实与闪回共享 topology、拥有不同 time_period。
- 布局 smoke 校验变体拓扑不漂移。

#### 上下游依赖

依赖 3；供 14、16、19、21 使用。

### 14. `layout_finalize`

- 阶段：场景建模
- 优先级：P1
- 改造目标：将场景变体表达为局部 delta，而不是整体重绘描述。

#### 当前问题

赤光状态偏向全屏染红，缺少局部光源、曝光和基础拓扑约束。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/prompts/layout_finalize/default.md`

#### 数据契约变化

```text
LayoutVariant 仅保存 delta：light_sources、local_effects、exposure_delta、affected_zones。
base topology 不可被 variant 覆盖。
```

#### 实施步骤

1. 代码合并同一物理空间、保留时段和照明变体，并将有效区间绑定事件。
2. 局部光效的 source、范围、衰减和曝光约束来自 YAML 或上游内容，工作流渲染为自然语言任务输入。
3. topology hash 仅由代码生成并用于变体前后一致性验证，永不发送给裸模。
4. 只有无法确定两个描述是否同一空间时才使用通用语义复核模板。

#### 确定性门禁

- variant 不得修改几何拓扑。
- 局部效果必须有 affected_zones。
- 曝光不得超过配置阈值。

#### 失败与回退

variant 校验失败时退回 base lighting，不生成全局滤镜替代品。

#### 验收与 Smoke

- 赤光只从炉体附近扩散且保留人物细节。
- smoke 拒绝“整个画面纯红”描述。

#### 上下游依赖

依赖 13；供 16、18、21、22 使用。

### 15. `prop_prompt`

- 阶段：道具资产
- 优先级：P1
- 改造目标：道具提示继承统一 3D CG 风格，并逐状态生成。

#### 当前问题

模板硬编码二维水墨且排除 3D 产品渲染，与项目视觉契约相反。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/prompts/prop_prompt/default.md`
- `autodrama/src/autodrama/core/schemas.py`

#### 数据契约变化

```text
代码侧 PropPromptItem 增加 prop_asset_id、style_spec_version、state_visual_delta、forbidden_states。
裸模只接收自然语言道具简报、状态差异与 YAML 风格简报。
```

#### 实施步骤

1. 移除模板中的二维/非 3D 和其它固定项目风格。
2. 工作流按当前资产状态筛选内容：常态只传常态，激活态只追加合法 delta。
3. 工作流把 YAML 的材质、渲染、色板和灯光渲染为自然语言，不暴露 VisualStyleSpec 字段。
4. 通用模板只约束标准道具设定图、尺度可读性、视图完整性以及不得发明角色或剧情。

#### 确定性门禁

- 提示 medium 与项目一致。
- 不得泄漏无效时间段的状态。
- 每个 state_visual_delta 可追溯到 transition。

#### 失败与回退

缺风格或状态契约则阻断，不复用旧模板默认值。

#### 验收与 Smoke

- 小炉常态无发光描述，激活态才包含血纹/赤光。
- 模板 smoke 确认没有“二维水墨/排除3D”。

#### 上下游依赖

依赖 4、5、12；供 17 使用。

### 16. `layout_prompt`

- 阶段：场景资产
- 优先级：P1
- 改造目标：场景提示锁定拓扑、机位覆盖和统一材质语言。

#### 当前问题

生成结果偏摄影写实，提示缺少可验证风格字段和稳定拓扑约束。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/prompts/layout_prompt/default.md`
- `autodrama/src/autodrama/core/schemas.py`

#### 数据契约变化

```text
代码侧 LayoutPromptItem 增加 topology_id、camera_zone、style_spec_version、exposure_envelope、variant_delta。
模型只接收人类可读的空间关系、合法变体和 YAML 风格简报。
```

#### 实施步骤

1. 工作流从 YAML 风格配置和 Layout 数据生成自然语言 brief，不发送字段名或 hash。
2. 提示输入可描述门窗、轴线和主要空间关系；通用模板只要求忠实保持输入拓扑。
3. 代码保证 base 与 variant 共用 topology，发送 variant 时只追加人类可读的合法 delta。
4. 机位族数量和范围由工作流规划，模型只负责在给定视角内完成视觉描述。

#### 确定性门禁

- topology hash 仅留在代码元数据，不得进入提示。
- 代码拒绝与 YAML 风格冲突的输出。
- variant 未重复描述或改写 base topology。

#### 失败与回退

冲突时回到结构化字段修复，不让模型自由重写场景。

#### 验收与 Smoke

- 四个场景资产风格分类一致。
- 同场不同 variant 的拓扑特征匹配。

#### 上下游依赖

依赖 4、5、14；供 18、21、22 使用。

### 17. `prop_image_generation`

- 阶段：道具资产
- 优先级：P1
- 改造目标：生成风格一致、状态可控、可被镜头安全引用的道具图。

#### 当前问题

道具清楚但为二维插画，与关键帧世界不统一；缺少状态语义审计。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
- `autodrama/src/autodrama/services/asset_service.py`

#### 数据契约变化

```text
输出 style_score、state_score、silhouette_score、audit_status、prompt_fingerprint。
资产发布需 accepted。
```

#### 实施步骤

1. 每个 PropAsset 单独生成并保留透明/中性背景版本。
2. 审计风格、材质、尺度、状态和文字污染。
3. 常态与激活态做差分审计，确保只变化规定 delta。
4. 失败只重做对应状态，不覆盖其它已通过资产。

#### 确定性门禁

- 常态不得发光，激活态必须有规定特征。
- 风格与 key vision 一致。
- 道具轮廓在多视图稳定。

#### 失败与回退

重试失败则标记 unavailable，阻断引用它的镜头，而不是用错误状态替代。

#### 验收与 Smoke

- 常态/激活态差异符合 PropTimeline。
- 二维样本被 style gate 拒绝。

#### 上下游依赖

依赖 15；为 23、24、26 提供参考。

### 18. `layout_image_generation`

- 阶段：场景资产
- 优先级：P1
- 改造目标：生成拓扑稳定、曝光可控、风格统一的场景参考。

#### 当前问题

场景写实度与角色板冲突；赤光变体压死细节，独立重绘带来空间漂移。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/static_asset_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
- `autodrama/src/autodrama/services/asset_service.py`

#### 数据契约变化

```text
输出 topology_score、style_score、exposure_score、base_asset_id、variant_diff、audit_status。
```

#### 实施步骤

1. 先生成 base 并审计，通过后用 image edit 派生 variant。
2. 审计拓扑关键点、风格、曝光和局部效果范围。
3. 固定同场景 LUT/material tokens。
4. 变体失败时不污染 base 资产。

#### 确定性门禁

- topology_score 达阈值。
- variant 只改变允许区域。
- 暗部人物可用曝光范围合格。

#### 失败与回退

variant 失败退回 base 并阻断依赖强事件光效的镜头，不接受全屏色滤镜。

#### 验收与 Smoke

- 执事堂 base 与赤光 variant 几何一致。
- 红光区域外细节保留。

#### 上下游依赖

依赖 16；供 21、22、23 使用。

### 19. `clip_segment`

- 阶段：分镜规划
- 优先级：P2
- 改造目标：在进入逐镜规划前完成叙事段落与时长预算分配。

#### 当前问题

三个 clip 只有粗粒度内容，缺 allocated_seconds、核心事件覆盖和顶层 provenance summary。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/script_nodes.py`
- `autodrama/src/autodrama/prompts/clip_segment/default.md`

#### 数据契约变化

```text
代码侧 ClipSegment 增加 allocated_seconds、event_ids、required_beats、coverage_goal、pace_class。
模型只建议语义分段；预算、ID、覆盖映射和总和约束由代码完成。
```

#### 实施步骤

1. 工作流从 YAML 读取目标时长，按对白字数、事件权重和节奏类型计算初始预算。
2. 通用模板只要求根据输入剧情划分连贯段落并说明叙事重点，不出现 event_ids、coverage_goal 等字段名。
3. 代码把模型分段匹配到事件，分配 allocated_seconds、最大镜头数和 coverage_goal。
4. 输出顶层 summary，并由代码验证预算总和与事件覆盖。

#### 确定性门禁

- 预算和为目标片长。
- 核心事件覆盖 100%。
- clip 顺序与事件拓扑一致。

#### 失败与回退

预算不可行时报告对白最低时长冲突并要求上游压缩，不把超长问题推给镜头节点。

#### 验收与 Smoke

- 150 秒目标被精确分配到 3 个 clip。
- smoke 对预算误差大于 0.5 秒直接失败。

#### 上下游依赖

依赖 3、13；为 20 提供硬预算。

### 20. `clip_to_shots`

- 阶段：分镜规划
- 优先级：P0
- 改造目标：生成满足时长、叙事覆盖、镜头语言和引用预算的可执行分镜。

#### 当前问题

调用未传目标时长，33 镜合计 218 秒；同场景站位重复，多角色引用过载。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/services/director_service.py`
- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/prompts/clip_to_shots/default.md`

#### 数据契约变化

```text
代码侧 ShotPlanItem 增加 duration_budget、event_ids、coverage_type、entity_states、allowed_props、reference_budget、requires_exact_text。
模型只设计镜头内容；内部映射和总时长 95%–105% 由代码保证。
```

#### 实施步骤

1. DirectorService 从 YAML/clip 预算和 provider 配置读取时长与能力边界，先计算对白最低时长和可用镜头数。
2. 工作流把当前剧情、可用时长、合法人物/道具状态渲染成人类可读输入；模板只包含通用分镜创作、景别变化和因果清晰度约束。
3. 模型提出 beat→shot 创意方案；代码分配/归一化时长并匹配 event_ids、entity_states 与 allowed_props。
4. 引用选择、群众降级和 provider reference budget 完全由代码处理，不要求裸模理解 provider 限制。
5. 输出后运行确定性 duration、coverage、timeline、reference validator。

#### 确定性门禁

- Σduration 在 142.5–157.5 秒。
- 每个 required event 至少一镜且因果顺序正确。
- 实体/道具状态在有效区间。
- references 不超过 provider budget。

#### 失败与回退

validator 返回结构化差异并仅重做违规 clip；不得把不合格计划写成 completed。

#### 验收与 Smoke

- 本项目不再产生 218 秒计划。
- shot 8 使用 young appearance；shot 21 不允许 activated furnace。
- 新增 shot_plan_contract_smoke。

#### 上下游依赖

依赖 8、12、14、19；是 21、23、25、26 的 P0 前置。

### 21. `layout_to_background_prompt`

- 阶段：镜头背景
- 优先级：P1
- 改造目标：先规划有限机位覆盖，再为镜头复用稳定背景。

#### 当前问题

21 个背景独立生成，缺少 coverage 语义，增加空间漂移与成本。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/prompts/layout_to_background_prompt/default.md`

#### 数据契约变化

```text
代码侧新增 CoverageCamera：camera_id、layout_id、zone、lens、screen_direction、reusable_shots[]。
模型只接收自然语言空间、视角和风格简报；camera_id 由代码分配。
```

#### 实施步骤

1. 代码按场景和 coverage_goal 规划 3–6 个锁定机位并分配 camera_id。
2. 同机位镜头复用 base background，仅对合法事件光效派生 variant。
3. 工作流把镜头位置、镜头朝向、空间关系和 YAML 风格渲染为自然语言。
4. 通用模板只负责把给定视角转成高质量无人物背景描述，不出现 layout topology、VisualStyleSpec 等内部名称。

#### 确定性门禁

- camera_id 在同场唯一。
- 连续剪辑不跨轴，除非有明确过轴镜头。
- 复用镜头的 topology/exposure 一致。

#### 失败与回退

无法覆盖时新增机位必须说明 coverage gap，不允许自由生成无坐标背景。

#### 验收与 Smoke

- 执事堂背景数量明显下降且覆盖 wide/two/close/insert。
- 连续镜头屏幕方向 smoke 通过。

#### 上下游依赖

依赖 14、16、18、20；供 22、23 使用。

### 22. `shot_background_image_generation`

- 阶段：镜头背景
- 优先级：P1
- 改造目标：生成并复用空间稳定、曝光合格的镜头背景。

#### 当前问题

技术上完整，但执事堂过暗，逐镜独立生成造成拓扑和光照漂移。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
- `autodrama/src/autodrama/services/asset_service.py`

#### 数据契约变化

```text
输出 camera_id、base_background_id、topology_score、exposure_score、reuse_map、audit_status。
```

#### 实施步骤

1. 按 CoverageCamera 生成 base 背景，而不是按 shot 全量生成。
2. 事件 variant 使用 image edit，并保存与 base 的 diff。
3. 审计拓扑关键点、曝光包络、风格和人物禁入。
4. 镜头输出只记录复用映射，不复制生成资产。

#### 确定性门禁

- 背景无人物/道具污染。
- topology 与 layout 资产一致。
- 曝光满足人物合成可见度。

#### 失败与回退

失败机位可重做；其它机位不失效。variant 失败不能覆盖 base。

#### 验收与 Smoke

- 执事堂背景拓扑稳定、亮度适合人物。
- 同 camera_id 的像素/特征差异在阈值内。

#### 上下游依赖

依赖 21；供 23、24 使用。

### 23. `shot_keyframe_prompt`

- 阶段：关键帧
- 优先级：P0
- 改造目标：按本镜白名单状态拼装提示，彻底阻断未来信息泄漏。

#### 当前问题

当前直接注入聚合 appearance.desc；闪回保留老年叶凡，第 33 镜又同时禁字和要求精确古字。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/workflows/generation.py`
- `autodrama/src/autodrama/prompts/shot_keyframe_prompt/default.md`

#### 数据契约变化

```text
代码侧 PromptContext = identity_invariants + current ShotEntityState + allowed_props + camera + VisualStyleSpec。
工作流渲染为自然语言镜头简报；prompt_provenance 等内部元数据不发送给模型。
```

#### 实施步骤

1. 移除聚合 desc 注入路径；代码按白名单查询当前 appearance、状态、合法道具、机位和 YAML 视觉约束。
2. 发送前运行确定性反事实校验，删除未来实体/状态，并把结构化内容渲染成人类可读镜头简报。
3. 通用模板只约束构图、角色可辨识度、动作瞬间、视觉连续性和严格遵循输入，不出现 ShotEntityState 等字段名。
4. requires_exact_text 时由代码路由到 clean plate；模型只收到通用禁字要求，overlay_text 直接交给后期。
5. prompt_provenance 在代码侧记录字段来源，但永不作为裸模上下文。

#### 确定性门禁

- 无未来实体/状态。
- 年龄变体与 time_period 匹配。
- 风格、机位、背景引用一致。
- exact text 不进入生成提示。

#### 失败与回退

缺状态时停止该镜并回退 8/12/20，不用自由文本补齐。

#### 验收与 Smoke

- shot 8 只引用 young 叶凡。
- shot 21 无小炉激活信息。
- shot 33 输出 clean_plate + overlay spec。

#### 上下游依赖

依赖 8、10、12、17、20、22；是 24、25、26 的最核心 P0 节点。

### 24. `shot_keyframe_image_generation`

- 阶段：关键帧
- 优先级：P0
- 改造目标：关键帧先通过语义与连续性审计，再允许视频生成。

#### 当前问题

局部 502 恢复后缺顶层 summary/fingerprint；提前小炉、年龄错误、风格漂移和重复构图均未拦截。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/workflows/nodes/image_audit_nodes.py`
- `autodrama/src/autodrama/services/asset_service.py`
- `autodrama/src/autodrama/repositories/project_repo.py`

#### 数据契约变化

```text
输出 attempt_id、input_fingerprint、semantic_scores、continuity_edges、audit_status、resume_provenance。
completed 要求所有 selected shots accepted。
```

#### 实施步骤

1. 每次生成由代码绑定完整输入 fingerprint，恢复时只接受 fingerprint 相同的文件。
2. 确定性可检查的尺寸、文件、引用、hash 和时间线先由代码审计。
3. 需要视觉理解的身份、年龄、道具状态、动作准备态、风格和构图使用通用审计模板；模型只收到人类可读的期望内容与图像，不收到内部字段名。
4. 代码建立相邻镜 continuity edge、汇总审计结果，并按错误类型回退：状态错回 23，构图错回 20/21，纯画质错仅重生本镜。
5. 保存 episode 顶层 summary，列出 accepted/rejected/skipped。

#### 确定性门禁

- semantic/identity/style/continuity 全部达阈值。
- 恢复文件有匹配 provenance。
- 阻断错误不得以 warning 通过。

#### 失败与回退

分层回退到最早污染节点；禁止“文件存在即成功”。

#### 验收与 Smoke

- 故意注入提前发光小炉时审计拒绝。
- 断点恢复 smoke 验证旧 fingerprint 资产不会被复用。

#### 上下游依赖

依赖 23；通过后才解锁 25、26。

### 25. `shot_manifest_generation`

- 阶段：生成清单
- 优先级：P0
- 改造目标：把 manifest 从文件索引升级为最终生成前的语义合同。

#### 当前问题

当前 warnings 为空只代表结构/路径合法，不代表时长、状态、引用和审计合格。

#### 代码落点

- `autodrama/src/autodrama/core/schemas.py`
- `autodrama/src/autodrama/workflows/nodes/shot_asset_nodes.py`
- `autodrama/src/autodrama/repositories/shot_manifest_repo.py`
- `autodrama/src/autodrama/workflows/dynamic_assets.py`

#### 数据契约变化

```text
ShotManifestItem 增加 contract_version、gate_results、entity_state_snapshot、reference_budget、text_overlay_spec、input_fingerprints。
ready_for_video 为派生字段。
```

#### 实施步骤

1. 聚合 duration/timeline/style/keyframe/reference/audio 等 required gates。
2. 校验所有引用资产 audit_status=accepted 且 fingerprint 匹配。
3. 按 provider 能力生成降载后的 video inputs。
4. exact-text 镜头附带 overlay spec，不把文字要求写回 video_prompt。
5. 只有 required gates 全通过才置 ready_for_video=true。

#### 确定性门禁

- 总时长、事件覆盖和 provider budget 合格。
- 每镜关键帧及引用资产 accepted。
- 状态快照与 shot plan 一致。

#### 失败与回退

输出 diagnostic manifest 但 ready_for_video=false；动态生成器必须拒绝消费。

#### 验收与 Smoke

- 缺任一 audit 文件时不能生成视频。
- shot 19/20 多角色输入被预算器压到合法范围。

#### 上下游依赖

依赖 20、24；是 26 的硬闸门。

### 26. `shot_video_generation`

- 阶段：动态生成
- 优先级：P0
- 改造目标：在受控引用与动作 beat 下生成视频，并把声音和文字从不稳定原生能力中剥离。

#### 当前问题

33 镜虽成功，但有道具幻觉、动作物理错误、触发因果缺失、乱码文字和未锁定声线。

#### 代码落点

- `autodrama/src/autodrama/workflows/nodes/shot_video_node.py`
- `autodrama/src/autodrama/workflows/generation.py`
- `autodrama/src/autodrama/workflows/dynamic_assets.py`
- `autodrama/src/autodrama/workflows/nodes/video_audit_node.py`
- `autodrama/src/autodrama/core/schemas.py`

#### 数据契约变化

```text
代码侧 VideoGenerationRequest 增加 action_beats、reference_policy、audio_mode、text_render_mode、expected_events。
模型接收自然语言动作简报；内部策略字段不进入提示。
```

#### 实施步骤

1. 生成前由代码强制检查 manifest.ready_for_video。
2. 复杂动作可由 LLM 提议拆成 1–3 个可观察 beat，但工作流负责验证起始/结束姿态、事件顺序和时长可行性，再渲染成自然语言动作提示。
3. 引用选择、最多主身份数、群众背景化和合法道具状态完全由代码决定。
4. 通用视频模板只约束动作可观察、物理连续、身份稳定、不得发明语音/文字/道具，不出现 reference_policy 等内部字段。
5. 角色台词使用独立 TTS/锁声轨；exact text 由代码路由到 clean plate 和后期叠加。
6. 视频审计采用通用评价模板加当前镜头的人类可读期望，结果再由代码映射、门禁和分层回退。

#### 确定性门禁

- required events 可见且顺序正确。
- 无非预期语音/文字/道具。
- 首尾帧与 continuity edge 匹配。
- 视频时长在预算内。

#### 失败与回退

模型能力失败时先降引用/拆动作，再局部重生；状态错误回退 23/20，禁止只在视频提示里补丁。

#### 验收与 Smoke

- shot 23 明确完成向后倒；shot 24 接扶连续。
- shot 26 玉瓶正确；shot 30 血触炉因果可见。
- shot 21 ASR 无清晰人声。

#### 上下游依赖

依赖 25；审计 accepted 后才能进入 27 与最终交付。

### 27. `postgen_source_collect`

- 阶段：后期入口
- 优先级：P0
- 改造目标：只收集已审计素材，并建立不可绕过的交付状态机。

#### 当前问题

本次只完成素材收集，后续 ASR 因 WhisperX/PyTorch 兼容失败；另有全量硬拼文件可被误认为 final。

#### 代码落点

- `autodrama/src/autodrama/workflows/postgen.py`
- `autodrama/src/autodrama/postgen/source_collect.py`
- `autodrama/src/autodrama/postgen/subtitle_pipeline.py`
- `autodrama/src/autodrama/postgen/schemas.py`
- `autodrama/src/autodrama/postgen/edit_plan_validator.py`

#### 数据契约变化

```text
PostgenSourceClip 增加 video_audit_status、quality_tier、allowed_ranges、expected_dialogue、gate_provenance。
项目输出区分 preview 与 deliverable。
```

#### 实施步骤

1. source collect 由代码只接纳 video audit accepted；rejected 仅进入 review reel。
2. ASR 抽象为 backend chain：faster-whisper 主转写，WhisperX 只作可选对齐；故障切换完全由代码处理。
3. 编辑计划 LLM 只接收人类可读的素材摘要和通用剪辑原则，不暴露内部状态字段；目标时长来自 YAML。
4. 坏片段排除、时间线长度、事件覆盖、字幕对齐和 required gates 全部由代码验证。
5. composition、字幕、音频和 final audit 全部 accepted 后才生成 deliverable 路径；否则只输出带标记 preview。

#### 确定性门禁

- source clip 均有 accepted video audit。
- ASR 与剧本逐镜对齐，无对白镜无清晰人声。
- 最终时间线不默认保留 100% 素材。
- required postgen gates 全部落盘。

#### 失败与回退

ASR 全部失败则阻断正式交付，但允许无字幕 review preview；不得把硬拼文件命名为 final。

#### 验收与 Smoke

- WhisperX 模拟失败时 faster-whisper 自动接管。
- 209.43 秒硬拼无法通过 duration/delivery gate。
- 只有 postgen_final_audit accepted 才出现 deliverable=true。

#### 上下游依赖

依赖 26 的逐镜审计；后续覆盖全部 Postgen 节点与最终成片。

## 4. 跨节点公共改造

### 4.1 统一状态与溯源

- 为 `StoryFactBundle`、`VisualStyleSpec`、`RoleAppearance`、`ShotEntityState`、`PropTimeline`、`CoverageCamera` 和 `ShotManifestItem` 增加独立 schema 版本。
- 每个生成节点保存输入 fingerprint、模板版本、模型参数、引用资产版本和重试 provenance。
- 旧字段只作为迁移输入，禁止继续成为提示拼装的默认来源。

### 4.2 统一节点完成语义

节点状态至少区分 `generated`、`audited`、`accepted`、`rejected`、`degraded`。只有 required gates 全部 accepted，节点才能进入可交付完成态。

### 4.3 分层回退规则

| 错误类型 | 回退节点 |
| --- | --- |
| 年龄、身份、未来状态污染 | `role_finalize` / `prop_finalize` |
| 时长、事件遗漏、构图覆盖不足 | `clip_to_shots` |
| 提示字段错误 | `shot_keyframe_prompt` |
| 单张图画质问题 | `shot_keyframe_image_generation` 当前镜 |
| 视频动作或物理问题 | `shot_video_generation` 当前镜，必要时拆 beat |
| ASR 后端故障 | 切换 backend；不得绕过 delivery gate |

### 4.4 回归验证

不得使用 pytest。新增 contract/smoke 脚本应放在 `scripts/smoke/`，临时产物写入 `.tmp/`。至少覆盖：

- 少年/老年 appearance 完整性。
- 小炉激活状态不得提前出现。
- 分镜总时长为目标 ±5%。
- provider reference budget 不超限。
- exact text clean-plate 分流。
- 恢复生成时 fingerprint 必须匹配。
- 无对白镜不得出现清晰人声。
- WhisperX 故障时 faster-whisper fallback。
- 未完成 final audit 时不能生成正式 deliverable。

## 5. 完成定义

只有同时满足以下条件，改造才算完成：

1. 27 个节点的 contract smoke 全部通过。
2. 使用本次失败运行作为 golden fixture 重新跑整链。
3. 第 8 镜使用少年叶凡，第 21 镜没有提前激活的小炉和无关语音。
4. 第 26 镜正确表现玉瓶，第 30 镜完整表现心血触炉因果。
5. 第 33 镜文字由后期精确叠加。
6. 分镜与最终成片均处于 150 秒 ±5%。
7. 所有 required image/video/postgen audits 均有落盘结果且为 accepted。
8. 未通过审计的结果只能输出带显著标识的 preview，不能命名或标记为 final。

---

生成来源：`index.html` 中的 27 节点审计与改造数据。
