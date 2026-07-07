# 任务

检查已有结构化场景视觉资产 `layouts`，合并重复空间，统一命名，并修正 base / variant 关系。

这个节点只做空间资产审计、去重、合并和命名规范化，不读取剧本文本，不新增剧情里未给出的场景，不写生图 prompt。

# 输入

已有场景资产 layouts：
{{layouts}}

# 输出字段

只输出符合 JSON schema 的对象：

- `layouts`：去重、合并、规范化后的完整场景资产数组。
- `merge_notes`：简短说明合并、重命名、保留状态场景、修正引用或删除重复项的原因。

# 审计规则

- 输出必须是完整 `layouts` 数组，不是差异补丁。
- 如果多个 base 本质上是同一物理空间，保留一个短名称，把稳定空间信息合并进 `brief` 和 `space_features`。
- 同一空间的别名、功能称呼、局部称呼、镜头角度称呼要合并；不要保留「大厅门口视角」「大厅中央」「大厅角落」这类误拆资产。
- 如果某项不是空间资产，比如人物、道具、服装、车辆特写、纯天气、纯情绪、技术字段、路径、URL、模型名称，应删除。
- 如果 variant 确实代表同一空间的不同视觉状态，并且需要独立参考图，则保留；variant 必须有 `reference_asset_name` 指向一个 base。
- variant 的 `brief` 可以概括用途，但 `state_delta` 必须只写相对 base 的变化，不要重复 base 的完整空间介绍。
- 如果 variant 实际改变了空间拓扑、室内外边界、入口关系、楼层或核心功能区，应改成独立 base。
- 如果某个状态只是一次镜头角度、临时人物站位、临时道具摆放或短暂动作，不应作为 variant，合并回 base。
- 如果 variant 没有对应 base，但从名称能明确判断 base，应补齐或改名引用已有 base；无法判断时改成 base，不要悬空引用。

# 字段要求

每个 layout 必须包含：

- `name`
- `group`
- `asset_role`：只能是 `base` 或 `variant`
- `reference_asset_name`：base 为空或 null；variant 指向 base 的 `name`
- `episode_keys`：非空，保留该资产实际出现或延续出现的 episode_key
- `source_chapters`
- `brief`
- `space_features`
- `state_delta`：base 为空；variant 只写变化

# 禁止内容

不要输出 prompt、asset_path、asset_url、URL、ID、模型名称、图片参数、文件名、节点名或项目 ID。

输出必须符合调用方提供的 JSON schema。
