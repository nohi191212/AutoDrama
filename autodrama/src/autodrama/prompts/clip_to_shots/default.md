将当前连续剧情拆分为连续的 3–15 秒镜头。

当前剧情：
{{clip_text}}

上一段必要衔接：
{{previous_context}}

下一段必要衔接：
{{next_context}}

可用视觉资产（`ref_ids` 只能使用这些 ID）：
{{asset_index}}

只输出 JSON。顶层 key 必须是连续的 `shot_1`、`shot_2`……，从 1 开始。每个值只能包含：
`shot_description`、`narrative_angle`、`opening_state`、`ref_ids`、`video_prompt`、`duration_seconds`、`dialogue`。

要求：
- 覆盖全部剧情且按发生顺序排列；`duration_seconds` 为 3–15 的整数。
- `shot_description` 用中文简洁说明当前画面事件。
- `narrative_angle` 必须非空，只表达一个明确的观察方向和叙事关注点；正反打、反应镜头或视角切换必须拆为不同镜头。
- `opening_state` 只描述起始瞬间的人物位置、朝向、视线、遮挡与固定道具状态。
- 有实体场景的镜头必须且只能引用一个 layout；引用所有实际可见的重要角色造型和必要道具。
- `video_prompt` 用中文描述本镜头内的动作、节奏和对白执行，不要加入额外切镜或运镜计划。
- `dialogue` 只保留原文对白，格式为 `角色名：对白`，不得由 `video_prompt` 推断。
