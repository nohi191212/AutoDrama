从剧本文本中补充尚未列出的主要角色。

剧本文本：
{{novel_full_context}}

已抽取主要角色：
{{existing_primary_roles}}

只输出会长期推动主线或与主角存在稳定关系、且需要跨镜头保持视觉一致的具体角色或生物。不要输出纯背景群体、一次性功能人物、仅被提及的设定、已列角色或其别称。所有输出的 `role_tier` 为 `primary`，`episode_keys` 只能使用正文中的实际 key。

每个角色必须提供一个 `base` 造型，并把内容严格分栏：

- `identity_invariants` 只写跨镜头稳定的脸部五官、发型与稳定发色、体型、年龄感和稳定视觉标志；不得写单镜动作、姿势、情绪、当镜手持物、瞬时伤势、能量光效或未来事件。
- `wardrobe` 只写当前正式造型中稳定复用的服装、鞋履和配饰；道具不得写入服装。
- 只有持续跨多个镜头或明确事件区间、且需要独立复用资产的换装、妆造、持续伤势、污损、湿身或伪装才建立 `variant`。每个 `variant` 必须填写 `reference_asset_name`，并至少填写 `valid_from_event` 或 `valid_to_event`；不要把单镜状态建成变体。
- 每个造型填写 `provenance`：`source=model`，`evidence` 只引用支持这些稳定字段的原文，`confidence` 反映证据明确程度。没有证据的特征不得猜测；无法确定的内容省略，不能从人物简介补写。

若没有新增角色，输出空 `roles`。仅输出符合给定 JSON Schema 的 JSON。
