from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import ProjectState, ScriptBundle, ScriptImportOutput
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.script_nodes import build_story_fact_bundle


SOURCE_PATH = ROOT / "inputs" / "扫地八十年，从宗门杂役飞升成仙_第1集.txt"
FORBIDDEN_PROMPT_TEXT = [
    "{{title}}",
    "{{project_id}}",
    "{{source_script_file}}",
    "{{episode_key}}",
    "{{episode_count}}",
    "{{episode_duration_seconds}}",
    "项目 ID",
    "原始文件路径",
    "当前 episode_key",
    "expected_output_seconds",
]
INTERNAL_TEMPLATE_TOKENS = [
    "`outline`",
    "`episode_outlines`",
    "`roles`",
    "`props`",
    "`layouts`",
    "`event_id`",
    "`source_span`",
]


def entity(name: str, entity_type: str, quote: str, *, time_period: str | None = None) -> dict:
    return {
        "name": name,
        "entity_type": entity_type,
        "time_period": time_period,
        "evidence_quotes": [quote],
    }


def main() -> None:
    raw_script = SOURCE_PATH.read_text(encoding="utf-8").strip()
    schema = ScriptImportOutput.model_json_schema()
    properties = set(schema.get("properties") or {})
    expected = {"outline", "episode_outlines", "roles", "props", "layouts", "facts", "notes"}
    if properties != expected:
        raise AssertionError(f"script_import schema fields should be {expected!r}, got {properties!r}")

    prompt = PromptStore().render("script_import", raw_script=raw_script)
    for text in FORBIDDEN_PROMPT_TEXT:
        if text in prompt:
            raise AssertionError(f"script_import prompt contains forbidden metadata text: {text}")
    for text in INTERNAL_TEMPLATE_TOKENS:
        if text in prompt:
            raise AssertionError(f"script_import template leaks internal field text: {text}")
    if "{{raw_script}}" in prompt or raw_script not in prompt:
        raise AssertionError("script_import prompt did not render the source script")

    output = ScriptImportOutput.model_validate(
        {
            "outline": "叶凡以八十年杂役贡献换得外门弟子身份，只为追问柳菡烟旧日承诺。闪回揭示少年叶凡将赤金小炉交给少女柳菡烟；现实中柳菡烟沉默，叶凡倒下并拒绝继续接受灵力。黄昏外廊，柳菡烟交付装有三枚延寿丹的碧绿玉瓶、归还小炉后离去。叶凡心血触及小炉，古老纹路苏醒并留下灵物钩子。",
            "episode_outlines": [
                "叶凡追问承诺未获回答而倒下；柳菡烟交付延寿丹、归还赤金小炉，叶凡心血触炉后小炉激活。"
            ],
            "roles": [
                {
                    "name": "叶凡",
                    "role_tier": "primary",
                    "intro": "天剑宗杂役，以八十年贡献换取外门身份。",
                    "aliases": ["老叶", "叶哥", "叶师弟"],
                    "identity_notes": ["天剑宗杂役"],
                    "appearance_stages": [
                        {
                            "time_period": "当前",
                            "appearance_notes": ["白发苍苍"],
                            "evidence_quotes": ["白发苍苍的叶凡"],
                        },
                        {
                            "time_period": "八十年前闪回",
                            "appearance_notes": ["少年"],
                            "evidence_quotes": ["少年叶凡"],
                        },
                    ],
                    "has_dialogue": True,
                    "visual_reuse_required": True,
                    "evidence_quotes": ["天剑宗杂役叶凡，扫地八十年，挑水八十年。"],
                },
                {
                    "name": "柳菡烟",
                    "role_tier": "primary",
                    "intro": "外门柳师姐，与叶凡有八十年前的承诺。",
                    "aliases": ["柳师姐", "菡烟"],
                    "identity_notes": ["天剑宗外门弟子"],
                    "appearance_stages": [
                        {
                            "time_period": "当前",
                            "appearance_notes": ["一袭白衣"],
                            "evidence_quotes": ["一袭白衣的柳菡烟"],
                        },
                        {
                            "time_period": "八十年前闪回",
                            "appearance_notes": ["少女"],
                            "evidence_quotes": ["少女柳菡烟"],
                        },
                    ],
                    "has_dialogue": True,
                    "visual_reuse_required": True,
                    "evidence_quotes": ["柳菡烟转身，看见叶凡的一瞬间神色微变。"],
                },
                {
                    "name": "李德海",
                    "role_tier": "functional",
                    "intro": "杂役总管，劝叶凡下山养老。",
                    "aliases": ["杂役总管"],
                    "identity_notes": ["杂役总管"],
                    "appearance_stages": [],
                    "has_dialogue": True,
                    "visual_reuse_required": True,
                    "evidence_quotes": ["杂役总管李德海快步追来，皱着眉拦住他。"],
                },
            ],
            "props": [
                {
                    "name": "木杖",
                    "identity_description": "叶凡登阶时使用的木杖。",
                    "aliases": [],
                    "evidence_quotes": ["拄着木杖"],
                },
                {
                    "name": "碧绿玉瓶",
                    "identity_description": "装有延寿丹的玉瓶。",
                    "aliases": [],
                    "evidence_quotes": ["一只碧绿玉瓶"],
                },
                {
                    "name": "延寿丹",
                    "identity_description": "玉瓶中的三枚凡阶下品丹药。",
                    "aliases": ["三枚延寿丹"],
                    "evidence_quotes": ["三枚凡阶下品延寿丹"],
                },
                {
                    "name": "赤金小炉",
                    "identity_description": "叶凡与柳菡烟之间的定情信物。",
                    "aliases": [],
                    "evidence_quotes": ["一只赤金小炉"],
                },
            ],
            "layouts": [
                {
                    "name": "天剑宗外门牌楼",
                    "spatial_description": "山门、牌楼与青石阶组成的外门入口。",
                    "time_periods": ["当前日间"],
                    "evidence_quotes": ["【场景一：天剑宗外门牌楼·日】"],
                },
                {
                    "name": "八十年前山脚",
                    "spatial_description": "宗门外的山脚。",
                    "time_periods": ["八十年前闪回"],
                    "evidence_quotes": ["八十年前的山脚"],
                },
                {
                    "name": "外门执事堂",
                    "spatial_description": "石柱高耸、弟子分列两侧的大殿。",
                    "time_periods": ["当前日间"],
                    "evidence_quotes": ["【场景二：外门执事堂·日】"],
                },
                {
                    "name": "执事堂外廊",
                    "spatial_description": "连接台阶与云海视野的外廊。",
                    "time_periods": ["当前黄昏"],
                    "evidence_quotes": ["【场景三：执事堂外廊·黄昏】"],
                },
            ],
            "facts": {
                "entity_mentions": [
                    entity("叶凡", "role", "天剑宗杂役叶凡，扫地八十年，挑水八十年。", time_period="当前"),
                    entity("柳菡烟", "role", "一袭白衣的柳菡烟", time_period="当前"),
                    entity("李德海", "role", "杂役总管李德海快步追来，皱着眉拦住他。", time_period="当前"),
                    entity("木杖", "prop", "拄着木杖", time_period="当前"),
                    entity("碧绿玉瓶", "prop", "一只碧绿玉瓶", time_period="当前"),
                    entity("延寿丹", "prop", "三枚凡阶下品延寿丹", time_period="当前"),
                    entity("赤金小炉", "prop", "一只赤金小炉", time_period="八十年前闪回"),
                    entity("天剑宗外门牌楼", "layout", "【场景一：天剑宗外门牌楼·日】", time_period="当前日间"),
                    entity("八十年前山脚", "layout", "八十年前的山脚", time_period="八十年前闪回"),
                    entity("外门执事堂", "layout", "【场景二：外门执事堂·日】", time_period="当前日间"),
                    entity("执事堂外廊", "layout", "【场景三：执事堂外廊·黄昏】", time_period="当前黄昏"),
                ],
                "events": [
                    {
                        "summary": "叶凡以贡献换得外门弟子身份并执意要一个回答。",
                        "time_period": "当前日间",
                        "participant_names": ["叶凡", "李德海"],
                        "prop_names": ["木杖"],
                        "layout_names": ["天剑宗外门牌楼"],
                        "precondition": "叶凡扫地挑水八十年。",
                        "result": "叶凡进入外门。",
                        "evidence_quotes": ["今日，他终于以一生贡献换来外门弟子的身份。"],
                    },
                    {
                        "summary": "少年叶凡将赤金小炉交给少女柳菡烟，柳菡烟作出条件承诺。",
                        "time_period": "八十年前闪回",
                        "participant_names": ["叶凡", "柳菡烟"],
                        "prop_names": ["赤金小炉"],
                        "layout_names": ["八十年前山脚"],
                        "precondition": "少年叶凡与少女柳菡烟并肩而立。",
                        "result": "柳菡烟作出拜入外门后嫁给叶凡的承诺。",
                        "evidence_quotes": ["少年把一只赤金小炉交到她手中。"],
                    },
                    {
                        "summary": "柳菡烟沉默，叶凡期待熄灭并倒下。",
                        "time_period": "当前日间",
                        "participant_names": ["叶凡", "柳菡烟"],
                        "prop_names": [],
                        "layout_names": ["外门执事堂"],
                        "precondition": "叶凡追问旧日承诺。",
                        "result": "柳菡烟扶住叶凡并渡入灵力。",
                        "evidence_quotes": ["柳菡烟红唇微张，却始终没有回答。", "叶凡身形一晃，向后倒去。"],
                    },
                    {
                        "summary": "柳菡烟交付延寿丹并归还赤金小炉。",
                        "time_period": "当前黄昏",
                        "participant_names": ["叶凡", "柳菡烟"],
                        "prop_names": ["碧绿玉瓶", "延寿丹", "赤金小炉"],
                        "layout_names": ["执事堂外廊"],
                        "precondition": "柳菡烟从袖中取出玉瓶和小炉。",
                        "result": "柳菡烟离去，叶凡持有小炉。",
                        "evidence_quotes": ["这是三枚凡阶下品延寿丹，能帮你续命三个月。", "她将小炉放入叶凡手中，转身御剑离去。"],
                    },
                    {
                        "summary": "叶凡心血触及赤金小炉后，小炉发光并浮现钩子古字。",
                        "time_period": "当前黄昏",
                        "participant_names": ["叶凡"],
                        "prop_names": ["赤金小炉"],
                        "layout_names": ["执事堂外廊"],
                        "precondition": "叶凡持有赤金小炉。",
                        "result": "赤金小炉被激活。",
                        "evidence_quotes": ["一口心血喷在赤金小炉上。", "赤金小炉骤然亮起炽烈红光，古老纹路逐一苏醒。"],
                    },
                ],
                "prop_observations": [
                    {
                        "prop_name": "木杖",
                        "action": "出现并由叶凡拄持",
                        "state": "常态",
                        "holder_name": "叶凡",
                        "time_period": "当前日间",
                        "evidence_quotes": ["白发苍苍的叶凡拄着木杖"],
                    },
                    {
                        "prop_name": "赤金小炉",
                        "action": "交给柳菡烟",
                        "state": "常态",
                        "holder_name": "柳菡烟",
                        "time_period": "八十年前闪回",
                        "evidence_quotes": ["少年把一只赤金小炉交到她手中。"],
                    },
                    {
                        "prop_name": "碧绿玉瓶",
                        "action": "交付给叶凡",
                        "state": "常态",
                        "holder_name": "叶凡",
                        "time_period": "当前黄昏",
                        "evidence_quotes": ["这是三枚凡阶下品延寿丹，能帮你续命三个月。"],
                    },
                    {
                        "prop_name": "延寿丹",
                        "action": "交付给叶凡",
                        "state": "常态",
                        "holder_name": "叶凡",
                        "time_period": "当前黄昏",
                        "evidence_quotes": ["这是三枚凡阶下品延寿丹，能帮你续命三个月。"],
                    },
                    {
                        "prop_name": "赤金小炉",
                        "action": "归还给叶凡",
                        "state": "常态",
                        "holder_name": "叶凡",
                        "time_period": "当前黄昏",
                        "evidence_quotes": ["她将小炉放入叶凡手中，转身御剑离去。"],
                    },
                    {
                        "prop_name": "赤金小炉",
                        "action": "被心血触发并激活",
                        "state": "激活",
                        "holder_name": "叶凡",
                        "time_period": "当前黄昏",
                        "evidence_quotes": ["赤金小炉骤然亮起炽烈红光，古老纹路逐一苏醒。"],
                    },
                ],
            },
            "notes": None,
        }
    )
    facts = build_story_fact_bundle(raw_script, output)

    furnace_observations = [
        item for item in facts.prop_observations if item.prop_name == "赤金小炉"
    ]
    if [item.action for item in furnace_observations] != ["交给柳菡烟", "归还给叶凡", "被心血触发并激活"]:
        raise AssertionError("furnace observations are not in source order")
    if furnace_observations[-1].state != "激活":
        raise AssertionError("furnace activation state is missing")
    if furnace_observations[-1].source_spans[0].start_char <= furnace_observations[1].source_spans[0].start_char:
        raise AssertionError("furnace activation must follow the return")

    role = next(item for item in output.roles if item.name == "叶凡")
    if {stage.time_period for stage in role.appearance_stages} != {"当前", "八十年前闪回"}:
        raise AssertionError("叶凡 must preserve both current and flashback appearance stages")
    prop_payload = next(item for item in output.model_dump(mode="json")["props"] if item["name"] == "赤金小炉")
    if "status" in prop_payload or "owner_role_name" in prop_payload:
        raise AssertionError("script_import prop identity must not contain a global state or owner")
    for fact in facts.events:
        for span in fact.source_spans:
            if raw_script[span.start_char : span.end_char] != span.quote:
                raise AssertionError("story fact source span does not round-trip to the source script")

    state = ProjectState(
        project_id="script_import_contract_smoke",
        title="contract smoke",
        raw_script=raw_script,
        script=ScriptBundle(raw_script=raw_script, facts=facts),
    )
    reloaded = ProjectState.model_validate(state.model_dump(mode="json"))
    if reloaded.script.facts != facts:
        raise AssertionError("story fact bundle does not round-trip through ProjectState")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "script_import_contract_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("script_import_contract_smoke: ok")


if __name__ == "__main__":
    main()
