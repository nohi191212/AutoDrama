审核下列道具与场景的归属边界：

道具：
{{props}}

场景：
{{layouts}}

返回完整 `props`、完整 `layouts` 和简短 `review_notes`，严格符合 JSON Schema。

可携带、操作、激活、损坏或作为独立证据/线索复用的物件属于 prop；固定且构成空间的结构属于 layout 的 `space_features`。固定结构若承担机关功能、被角色直接操作，或其损坏状态是独立剧情证据（例如被顶歪的门栓），也应保留为 prop，同时可在 layout 中简短提及。同一对象不可两边重复为独立资产。删除错分项时保留有效的 base/variant 引用，不新增剧情资产或 episode_key；不要写生图提示或技术字段。
