将当前连续剧情拆分为连续的 3–15 秒镜头。

本段可用时长约 {{available_seconds}} 秒。所有镜头时长之和应贴近该预算。
每镜最多使用 {{reference_budget}} 个视觉引用；低叙事权重群众应作为背景群体描述，不占独立身份引用。

当前剧情：
{{clip_text}}

上一段必要衔接：
{{previous_context}}

下一段必要衔接：
{{next_context}}

可用视觉资产（`ref_ids` 只能使用这些 ID）：
{{asset_index}}

只输出 JSON。顶层 key 必须是连续的 `shot_1`、`shot_2`……，从 1 开始。每个值只能包含：
`shot_description`、`narrative_angle`、`opening_state`、`ref_ids`、`video_prompt`、`duration_seconds`、
`entity_states`、`dialogue_lines`、`overlay_text_spec`、`allowed_props`。

要求：
- 覆盖全部剧情且按发生顺序排列；`duration_seconds` 为 3–15 的整数。
- `shot_description` 用中文简洁说明当前画面事件。
- `narrative_angle` 必须非空，只表达一个明确的观察方向和叙事关注点；正反打、反应镜头或视角切换必须拆为不同镜头。
- `opening_state` 只描述起始瞬间的人物位置、朝向、视线、遮挡与固定道具状态。
- 每个镜头必须且只能引用一个 layout；即使是黑场、抽象转场或特写，也选择承接该画面的空间母版。只引用实际可见且对本镜不可缺少的角色造型和道具。
- `video_prompt` 只用中文描述本镜头可见动作、表演、节奏和镜头意图；不得写宽高比、分辨率、供应商占位符、字幕/水印禁令或其他模型控制参数。
- `entity_states` 只描述当前镜头的临时状态。每项包含 `schema_version=1`、有效 `entity_id`，以及可选的 `appearance_id`、`pose`、`emotion`、`injury`、`held_props`、`energy_state`、`event_refs`；不得把状态写回角色永久身份。
- `allowed_props` 只列本镜允许出现的有效道具 ID。`held_props` 必须是 `allowed_props` 的子集。
- `dialogue_lines` 按原文逐条输出。每项包含 `schema_version=1`、从 1 连续编号的 `line_index`、已知角色的 `speaker_role_id`、`speaker_name`、只含可朗读内容的 `text`、显式 `emotion`、0–1 的 `intensity` 或 null、`delivery_mode`（`on_screen`/`offscreen`/`voiceover`）、原始证据 `source_text`，以及 `provenance`。
- `provenance` 固定包含 `schema_version=1`、`source="model"`、直接证据数组 `evidence`、0–1 的 `confidence` 和可选 `model=null`。无法确定说话人时使用 null，不得猜测。
- 只有画面确实需要精确可读文字时才输出 `overlay_text_spec`，否则为 null。该对象包含 `schema_version=1`、`text`、`render_mode`（`postproduction` 或 `in_scene`）、可选 `placement_hint`、`start_seconds`、`end_seconds` 和同样格式的 `provenance`。普通对白引号、作品名和引用不是画面文字。
- `postproduction` 表示生成 clean plate 后由后期叠字；`in_scene` 表示文字属于场景内画面。overlay 时间不得超出本镜时长。
- 无法确定的可选字段使用 null、空数组或 `other`，不得从上下文捏造。
