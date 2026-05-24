# 任务

检查单个角色 JSON 的 `episode_keys` 是否遗漏了该角色实际出现或被明确提到并影响剧情的剧集。你只负责直接找出“需要追加”的 `missing_episode_keys`，不负责删除任何已有 key。

# 输入

标题：{{title}}

当前角色 JSON：
{{role_json}}

完整小说正文（按 episode_key 组织）：
{{novel_full}}

全集 episode_key 列表：{{episode_keys}}

# 判断规则

- 只审查当前角色 JSON 中的这个角色，不要新增其他角色，不要输出角色设计内容。
- 如果该角色在某一集正文中实际出场、有对白、被明确点名、使用别名/称号出现，或虽未直接出场但被明确提到并对当前剧情产生影响，应把该集视为该角色出现。
- 需要考虑 `name`、`aliases`、`brief`、`appearance_notes`、已有 `source_chapters` 中的称谓线索。
- `missing_episode_keys` 是唯一会被程序写回的补充依据。发现遗漏章节、遗漏出场或遗漏提及时，必须先定位它对应的全集 episode_key，然后把该 key 写入 `missing_episode_keys`。
- `missing_episode_keys` 只填写当前角色 JSON 中尚未包含、但按正文判断应该追加的 episode_key。
- 不要输出已经存在于当前角色 JSON `extract.episode_keys` 中的 key。
- 不要输出全集 episode_key 列表之外的 key，不要输出中文章节名、数字或自造 key。
- `missing_source_chapters` 只是辅助证据，不是补充依据，不能替代 `missing_episode_keys`。如果某个章节线索代表角色遗漏出场，则必须同时在 `missing_episode_keys` 中给出对应 episode_key；不要只输出 `missing_source_chapters`。
- `missing_source_chapters` 只填写可以从正文或现有章节标记中稳妥判断、且当前角色 JSON 尚未包含的章节线索；无法判断时填空数组。无法把章节线索稳定映射到 episode_key 时，不要把它作为遗漏输出，只在 `evidence` 中说明疑点。
- 对原 JSON 的原则是只增不减：即使你认为某个已有 episode_key 可疑，也不要要求删除它，可以在 `evidence` 中简短说明。
- 如果没有遗漏，`missing_episode_keys` 和 `missing_source_chapters` 都输出空数组。
- `evidence` 用一两句话说明依据，指出涉及的 episode_key 或文本线索。
- `confidence` 使用 0 到 1 的数值，表示你对追加判断的把握。

# 输出

输出必须符合调用方提供的 JSON schema。
