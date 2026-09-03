from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
LAB = ROOT / ".tmp" / "clip-to-shots-template-evolution-20260809"
CASE_ID = "E03_courtyard_rule_exposition"
REPORT_PATH = (
    ROOT
    / ".assets"
    / "storyboarding"
    / "clip_to_shots-prompt-output-comparison-20260809.html"
)


CANDIDATES = [
    {
        "id": "A",
        "cycle": "cycle01-saodi",
        "version": "V0",
        "title": "现网 rich 基线",
        "short": "模型直接制造完整生产结构",
        "change": "规则较少；要求裸模同时输出 scene/ref、opening、camera optics、dialogue provenance 等机械字段。",
        "verdict": "能拍，但时长分配僵硬：长对白被压缩，结尾又留下 7 秒静态物件空镜。",
        "template": LAB / "candidates" / "cycle01" / "A-incumbent-rich.md",
        "features": [False, False, False, False],
        "accent": "#f06f61",
    },
    {
        "id": "B",
        "cycle": "cycle01-saodi",
        "version": "V1",
        "title": "专业规则 + rich",
        "short": "先改善电影语法，接口仍然很重",
        "change": "加入切镜动机、轴线、视线、运动触发与时长规则；仍让模型输出大量代码可推导字段。",
        "verdict": "三镜结构更紧凑，手势到机关的转移更清楚；长解释仍偏说话人中心。",
        "template": LAB / "candidates" / "cycle01" / "B-professional-rich.md",
        "features": [True, False, False, False],
        "accent": "#e59c42",
    },
    {
        "id": "C",
        "cycle": "cycle01-saodi",
        "version": "V2",
        "title": "专业 lean",
        "short": "只让裸模做导演判断",
        "change": "输出缩为 shots-only 创意接口；ID、对白原文、引用、provenance 与光学参数由代码编译。",
        "verdict": "四镜覆盖完整，接口经济性显著提升；11 秒解释镜仍略静态，听者反应不足。",
        "template": LAB / "candidates" / "cycle01" / "C-professional-lean.md",
        "features": [True, True, False, False],
        "accent": "#9fbb59",
    },
    {
        "id": "D",
        "cycle": "cycle05-saodi",
        "version": "V3",
        "title": "listener-aware lean",
        "short": "长解释也要拍关系与听者",
        "change": "明确超过约 7 秒的连续解释不能默认静态 speaker single；用双人、过肩或有动机重构图保留听者变化。",
        "verdict": "本 clip 单案最高：规则说明随听者视线推进，机关警告后补一个短反应镜。",
        "template": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
        "features": [True, True, True, False],
        "accent": "#42c8b7",
    },
    {
        "id": "G",
        "cycle": "cycle05-saodi",
        "version": "V4",
        "title": "当前 saodi 推广组合",
        "short": "D 模板 + 更干净稳定的实际输入",
        "change": "本 clip 的提示词正文与 D 相同，实际差异只有角色索引缩为 role_id + 姓名；完整案例集中的触感样例另应用了不改对白与事件的 provider-safe 可视化措辞，它属于输入清理，不冒充提示词收益。",
        "verdict": "三镜最精炼：双人状态 → 解释与手势 → 机关特写。该 clip 略低于 D，但跨案例零硬失败、综合最稳。",
        "template": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
        "features": [True, True, True, True],
        "accent": "#5ad6e8",
    },
]


DIMENSION_NAMES = {
    "D01_narrative_coverage": "叙事覆盖",
    "D02_cut_motivation_and_information_delta": "切镜动机",
    "D03_coverage_architecture": "覆盖架构",
    "D04_spatial_geography_and_axis": "空间与轴线",
    "D05_blocking_and_performance": "调度与表演",
    "D06_shot_size_angle_and_perspective_intent": "景别角度意图",
    "D07_camera_movement_motivation": "运镜动机",
    "D08_pacing_and_duration_feasibility": "节奏与时长",
    "D09_reaction_subtext_and_attention": "反应与潜台词",
    "D10_natural_visual_progression": "自然视觉推进",
    "D11_production_executability": "生产可执行",
    "D12_state_continuity": "状态连续",
    "D13_dialogue_and_visible_text_integrity": "对白与文字",
    "D14_interface_economy": "接口经济性",
}

KEY_DIMENSIONS = [
    "D01_narrative_coverage",
    "D02_cut_motivation_and_information_delta",
    "D08_pacing_and_duration_feasibility",
    "D09_reaction_subtext_and_attention",
    "D10_natural_visual_progression",
    "D14_interface_economy",
]


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def h(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def ordered_shots(compiled: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        value
        for key, value in sorted(
            compiled.items(),
            key=lambda item: int(item[0].rsplit("_", 1)[-1]),
        )
    ]


def load_case() -> tuple[dict[str, Any], dict[str, Any]]:
    cases = read_json(LAB / "cases.json")
    case = next(item for item in cases["cases"] if item["id"] == CASE_ID)
    return cases, case


def load_candidate(spec: dict[str, Any]) -> dict[str, Any]:
    cycle = LAB / "cycles" / spec["cycle"]
    generation_path = cycle / "generation" / CASE_ID / f"{spec['id']}.json"
    judge_path = cycle / "judge" / f"{CASE_ID}.json"
    result = read_json(generation_path)
    judge = read_json(judge_path)
    if result["status"] != "success":
        raise ValueError(f"candidate {spec['id']} has no successful generation")
    score = judge["scores"][spec["id"]]
    prompt_path = ROOT / result["prompt_path"]
    template_path = spec["template"]
    shots = ordered_shots(result["compiled_output"])
    lowest = sorted(
        score["dimension_scores"],
        key=lambda key: (score["dimension_scores"][key], key),
    )[:3]
    return {
        **spec,
        "generation_path": generation_path,
        "judge_path": judge_path,
        "prompt_path": prompt_path,
        "prompt": prompt_path.read_text(encoding="utf-8"),
        "template_text": template_path.read_text(encoding="utf-8"),
        "result": result,
        "score": score,
        "shots": shots,
        "lowest_dimensions": lowest,
    }


def feature_cell(enabled: bool) -> str:
    return '<span class="yes">●</span>' if enabled else '<span class="no">—</span>'


def score_tone(score: float) -> str:
    if score >= 9.4:
        return "excellent"
    if score >= 9.0:
        return "strong"
    if score >= 8.0:
        return "good"
    return "weak"


def dialogue_html(lines: list[dict[str, Any]]) -> str:
    if not lines:
        return '<span class="silent">无对白</span>'
    return "".join(
        f'<div class="dialogue"><b>{h(line.get("speaker_name") or line.get("speaker_role_id") or "画外")}</b>'
        f'<span>{h(line.get("text"))}</span><i>{h(line.get("delivery_mode"))}</i></div>'
        for line in lines
    )


def shot_html(shot: dict[str, Any], index: int) -> str:
    camera = shot.get("camera") or {}
    placements = shot.get("character_placements") or []
    character_labels = []
    role_names = {"role_jiang": "江未晞", "role_jiushao": "九韶"}
    for placement in placements:
        role_id = str(placement.get("role_id") or "")
        character_labels.append(role_names.get(role_id, role_id))
    characters = " / ".join(character_labels) if character_labels else "物件 / 空镜"
    dialogue = dialogue_html(shot.get("dialogue_lines") or [])
    return f"""
      <article class="shot-card">
        <div class="shot-head">
          <div><span class="shot-index">SHOT {index:02d}</span><strong>{h(shot.get('duration_seconds'))}s</strong></div>
          <span class="shot-size">{h(camera.get('shot_size'))} · {h(camera.get('shooting_angle'))}</span>
        </div>
        <h4>{h(shot.get('narrative_angle'))}</h4>
        <div class="shot-grid">
          <div><label>画面动作</label><p>{h(shot.get('shot_description'))}</p></div>
          <div><label>摄影机</label><p>{h(camera.get('scene_position'))} → {h(camera.get('target'))}</p></div>
          <div><label>运动</label><p>{h(camera.get('movement'))}</p></div>
          <div><label>主体</label><p>{h(characters)}</p></div>
        </div>
        <div class="dialogue-list">{dialogue}</div>
        <details class="micro"><summary>展开 opening state 与引用</summary>
          <p><b>Opening：</b>{h(shot.get('opening_state'))}</p>
          <p><b>Refs：</b>{h(', '.join(shot.get('ref_ids') or []) or '(none)')}</p>
        </details>
      </article>
    """


def key_score_html(candidate: dict[str, Any]) -> str:
    scores = candidate["score"]["dimension_scores"]
    return "".join(
        f"""
        <div class="mini-score">
          <span>{h(DIMENSION_NAMES[key])}</span>
          <div class="mini-track"><i style="width:{scores[key] * 10:.1f}%"></i></div>
          <b>{scores[key]:g}</b>
        </div>
        """
        for key in KEY_DIMENSIONS
    )


def defect_html(candidate: dict[str, Any]) -> str:
    score = candidate["score"]
    rows = []
    for key in candidate["lowest_dimensions"]:
        defect = score["dimension_defects"].get(key) or "None"
        rows.append(
            f'<li><b>{h(DIMENSION_NAMES[key])} {score["dimension_scores"][key]:g}</b>'
            f'<span>{h(defect)}</span></li>'
        )
    return "".join(rows)


def full_dimension_rows(candidate: dict[str, Any]) -> str:
    score = candidate["score"]
    return "".join(
        f"<tr><td>{h(DIMENSION_NAMES[key])}</td><td><b>{value:g}</b></td>"
        f"<td>{h(score['dimension_defects'].get(key) or 'None')}</td></tr>"
        for key, value in score["dimension_scores"].items()
    )


def candidate_section(candidate: dict[str, Any], rank: int) -> str:
    score = float(candidate["score"]["gated_score"])
    audit = candidate["result"]["deterministic_audit"]
    prompt = candidate["prompt"]
    template = candidate["template_text"]
    raw_json = json.dumps(candidate["result"]["raw_output"], ensure_ascii=False, indent=2)
    shots = "".join(shot_html(shot, index) for index, shot in enumerate(candidate["shots"], 1))
    return f"""
    <section class="candidate-section" id="candidate-{candidate['id']}" style="--candidate:{candidate['accent']}">
      <div class="candidate-heading">
        <div>
          <span class="version">{h(candidate['version'])} · CANDIDATE {h(candidate['id'])}</span>
          <h2>{h(candidate['title'])}</h2>
          <p class="candidate-short">{h(candidate['short'])}</p>
        </div>
        <div class="score-orb {score_tone(score)}"><small>本 clip 得分</small><strong>{score:.4f}</strong><span>/ 10</span></div>
      </div>

      <div class="candidate-summary">
        <div><label>这一版提示词改变了什么</label><p>{h(candidate['change'])}</p></div>
        <div><label>看完分镜后的直观结论</label><p>{h(candidate['verdict'])}</p></div>
        <div class="candidate-facts">
          <span><b>{len(candidate['shots'])}</b> 镜</span>
          <span><b>{audit['duration_sum']}</b> 秒</span>
          <span><b>{audit['average_raw_shot_field_count']:g}</b> raw 字段/镜</span>
          <span><b>{len(candidate['score']['failed_gates'])}</b> 硬失败</span>
        </div>
      </div>

      <div class="prompt-panel">
        <div class="prompt-title">
          <div><span>实际发送给 API 裸模</span><b>{len(prompt.splitlines())} 行 · {len(prompt)} 字符</b></div>
          <code>{h(candidate['prompt_path'].relative_to(ROOT).as_posix())}</code>
        </div>
        <details {'open' if candidate['id'] == 'G' else ''}>
          <summary>查看完整渲染后提示词</summary>
          <pre>{h(prompt)}</pre>
        </details>
        <details>
          <summary>查看模板原文（变量未渲染）</summary>
          <pre>{h(template)}</pre>
        </details>
      </div>

      <div class="storyboard-title"><span>模型最终给出的分镜</span><small>下列为同一次 API 输出经确定性编译后的可读视图；对白文字来自源文恢复。</small></div>
      <div class="shots">{shots}</div>

      <div class="score-review">
        <div>
          <h3>关键维度</h3>
          {key_score_html(candidate)}
        </div>
        <div>
          <h3>最低三项与评审意见</h3>
          <ol class="defects">{defect_html(candidate)}</ol>
        </div>
      </div>
      <details class="full-review"><summary>查看全部 14 维评分</summary>
        <table><thead><tr><th>维度</th><th>分数</th><th>主要缺点</th></tr></thead><tbody>{full_dimension_rows(candidate)}</tbody></table>
      </details>
      <details class="raw-output"><summary>查看裸模型原始 JSON</summary><pre>{h(raw_json)}</pre></details>
    </section>
    """


def overview_rows(candidates: list[dict[str, Any]]) -> str:
    rows = []
    for candidate in candidates:
        score = candidate["score"]["gated_score"]
        features = "".join(f"<td>{feature_cell(value)}</td>" for value in candidate["features"])
        rows.append(
            f"<tr style=\"--row:{candidate['accent']}\"><td><a href=\"#candidate-{candidate['id']}\">"
            f"<b>{h(candidate['version'])}</b><span>{h(candidate['title'])}</span></a></td>"
            f"{features}<td>{len(candidate['shots'])}</td><td>{candidate['result']['deterministic_audit']['duration_sum']}s</td>"
            f"<td><strong>{score:.4f}</strong></td></tr>"
        )
    return "".join(rows)


def clip_dialogues(case: dict[str, Any]) -> str:
    # Human-readable source lines, intentionally not inferred from generated outputs.
    return """
      <div class="source-line"><b>九韶 · 语气呆板</b><p>“乐园场景已呈现，现在请求为宿主解释乐园规则。”</p></div>
      <div class="source-line action"><b>动作 / 反应</b><p>江未晞仍盯着指尖花粉，迟钝点头，视线扫着庭院。</p></div>
      <div class="source-line"><b>江未晞 · 呆呆地点头</b><p>“啊……你直接说就行。”</p></div>
      <div class="source-line"><b>九韶 · 严肃冰山脸</b><p>完整解释秘境支线、剧情任务、解锁密室、魅惑追捕、寻找生门。</p></div>
      <div class="source-line action"><b>动作 / 信息转移</b><p>九韶抬手指向庭院一侧；江未晞跟随视线，发现覆苔石灯与缝隙幽光。</p></div>
      <div class="source-line"><b>九韶 · 指向石灯</b><p>“游客触碰此机关后会被迷惑，如不及时挣脱，就会被隐藏在假山后的狐爪抓住。”</p></div>
    """


def build_html(cases: dict[str, Any], case: dict[str, Any], candidates: list[dict[str, Any]]) -> str:
    sections = "".join(candidate_section(candidate, rank) for rank, candidate in enumerate(candidates, 1))
    source_image = "../../" + cases["scene_anchors"][case["scene_id"]]["path"]
    best = max(candidates, key=lambda item: item["score"]["gated_score"])
    final = next(item for item in candidates if item["id"] == "G")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>同一 clip · 提示词与分镜结果横向对比</title>
  <style>
    :root {{ --bg:#090b0f; --panel:#11151b; --panel2:#171c23; --ink:#f4f1e9; --muted:#aeb4bd; --line:rgba(255,255,255,.11); --gold:#f1ad48; --cyan:#5ad6e8; --green:#69d29a; }}
    *{{box-sizing:border-box}} html{{scroll-behavior:auto;overflow-x:hidden}} body{{margin:0;background:radial-gradient(circle at 80% 0,rgba(90,214,232,.1),transparent 30rem),radial-gradient(circle at 0 18%,rgba(241,173,72,.1),transparent 32rem),var(--bg);color:var(--ink);font-family:"Segoe UI","Microsoft YaHei UI","PingFang SC",system-ui,sans-serif;line-height:1.65;overflow-x:hidden}} a{{color:inherit;text-decoration:none}} code,pre{{font-family:"Cascadia Code",Consolas,monospace}} .shell{{width:min(1180px,calc(100% - 40px));margin:auto}}
    nav{{position:sticky;top:0;z-index:20;background:rgba(9,11,15,.86);backdrop-filter:blur(18px);border-bottom:1px solid var(--line)}} .nav-inner{{height:58px;display:flex;align-items:center;justify-content:space-between;gap:20px}} .brand{{font-weight:800}} .nav-links{{display:flex;gap:16px;color:var(--muted);font-size:12px}} .nav-links a:hover{{color:white}}
    .hero{{padding:80px 0 52px}} .eyebrow{{color:var(--cyan);font:800 12px/1 monospace;letter-spacing:.12em;text-transform:uppercase}} h1{{margin:20px 0 18px;max-width:950px;font-size:clamp(46px,7vw,88px);line-height:1.01;letter-spacing:-.06em}} h1 em{{font-style:normal;color:var(--gold)}} .lead{{max-width:790px;margin:0;color:#c6c9cf;font-size:19px}} .hero-facts{{display:flex;flex-wrap:wrap;gap:10px;margin-top:28px}} .hero-facts span{{padding:8px 11px;border:1px solid var(--line);border-radius:999px;color:var(--muted);font-size:12px}} .hero-facts b{{color:var(--ink)}}
    section{{padding:64px 0;border-top:1px solid var(--line)}} .section-head{{display:grid;grid-template-columns:250px 1fr;gap:35px;margin-bottom:28px}} .section-head span{{color:var(--gold);font:800 11px/1 monospace;letter-spacing:.12em}} .section-head h2{{margin:8px 0 0;font-size:40px;line-height:1.06;letter-spacing:-.045em}} .section-head p{{margin:2px 0 0;color:var(--muted);font-size:16px}}
    .clip-card{{display:grid;grid-template-columns:360px 1fr;border:1px solid var(--line);border-radius:24px;overflow:hidden;background:var(--panel);box-shadow:0 30px 100px rgba(0,0,0,.3)}} .scene-image{{position:relative;min-height:610px;background:#0d1117}} .scene-image img{{width:100%;height:100%;object-fit:cover}} .scene-image::after{{content:"唯一场景锚点";position:absolute;left:18px;bottom:18px;padding:7px 10px;background:rgba(0,0,0,.72);border:1px solid rgba(255,255,255,.2);border-radius:999px;font-size:11px}} .clip-copy{{padding:30px}} .clip-meta{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:18px;color:var(--muted);font-size:12px}} .clip-copy>p{{white-space:pre-wrap;color:#d3d5d9;font-size:15px;margin:0;padding:20px;border:1px solid var(--line);border-radius:16px;background:rgba(0,0,0,.15)}} .source-lines{{margin-top:18px}} .source-line{{display:grid;grid-template-columns:165px 1fr;gap:15px;padding:11px 0;border-bottom:1px solid var(--line)}} .source-line b{{font-size:12px;color:var(--gold)}} .source-line p{{margin:0;color:#c8cbd0;font-size:13px}} .source-line.action b{{color:var(--cyan)}}
    .overview{{overflow-x:auto;border:1px solid var(--line);border-radius:20px;background:var(--panel)}} table{{border-collapse:collapse;width:100%}} th,td{{padding:14px 15px;border-bottom:1px solid var(--line);text-align:left;font-size:12px}} th{{color:var(--muted);font-weight:600;background:rgba(255,255,255,.025)}} tbody tr:last-child td{{border-bottom:0}} .overview td:first-child{{border-left:3px solid var(--row)}} .overview td a{{display:flex;flex-direction:column}} .overview td span{{color:var(--muted)}} .overview strong{{font-size:17px}} .yes{{color:var(--green)}} .no{{color:#606873}}
    .note{{margin-top:14px;padding:15px 18px;border-left:3px solid var(--cyan);background:rgba(90,214,232,.07);color:#cbdadd;border-radius:0 13px 13px 0;font-size:13px}}
    .candidate-section{{padding-top:82px}} .candidate-heading{{display:flex;justify-content:space-between;align-items:end;gap:30px}} .version{{color:var(--candidate);font:800 12px/1 monospace;letter-spacing:.12em}} .candidate-heading h2{{margin:10px 0 0;font-size:clamp(38px,5vw,62px);letter-spacing:-.055em;line-height:1}} .candidate-short{{margin:10px 0 0;color:var(--muted);font-size:17px}} .score-orb{{flex:0 0 180px;height:145px;padding:22px;border:1px solid color-mix(in srgb,var(--candidate) 55%,transparent);border-radius:22px;background:linear-gradient(145deg,color-mix(in srgb,var(--candidate) 14%,transparent),rgba(255,255,255,.02))}} .score-orb small,.score-orb span{{display:block;color:var(--muted);font-size:11px}} .score-orb strong{{display:block;margin:8px 0 2px;font:850 35px/1 monospace;color:var(--candidate)}}
    .candidate-summary{{display:grid;grid-template-columns:1fr 1fr;margin-top:22px;border:1px solid var(--line);border-radius:20px;overflow:hidden;background:var(--panel)}} .candidate-summary>div{{padding:20px;border-right:1px solid var(--line)}} .candidate-summary>div:nth-child(2){{border-right:0}} .candidate-summary label{{display:block;color:var(--candidate);font-size:11px;font-weight:800;letter-spacing:.08em}} .candidate-summary p{{margin:8px 0 0;color:#c8cbd0}} .candidate-facts{{grid-column:1/-1!important;display:flex;gap:28px;border-top:1px solid var(--line);border-right:0!important;color:var(--muted);font-size:12px}} .candidate-facts b{{color:var(--ink);font-size:19px}}
    .prompt-panel{{margin-top:18px;border:1px solid var(--line);border-radius:20px;overflow:hidden;background:#0b0e12}} .prompt-title{{display:flex;justify-content:space-between;gap:20px;align-items:center;padding:18px 20px;border-bottom:1px solid var(--line)}} .prompt-title span{{display:block;color:var(--candidate);font-size:11px;font-weight:800}} .prompt-title b{{font-size:15px}} .prompt-title code{{color:var(--muted);font-size:10px}} details{{border-top:1px solid var(--line)}} details:first-of-type{{border-top:0}} summary{{cursor:pointer;padding:14px 20px;color:#d8dadd;font-size:13px;font-weight:700;list-style:none}} summary::before{{content:"＋";color:var(--candidate);margin-right:9px}} details[open]>summary::before{{content:"−"}} pre{{margin:0;padding:20px;max-height:650px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;background:#07090c;color:#c7d1d2;font-size:11px;line-height:1.65;border-top:1px solid var(--line)}}
    .storyboard-title{{display:flex;justify-content:space-between;align-items:end;gap:20px;margin:34px 0 13px}} .storyboard-title span{{font-size:20px;font-weight:800}} .storyboard-title small{{max-width:650px;color:var(--muted)}} .shots{{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}} .shot-card{{min-width:0;padding:20px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(150deg,color-mix(in srgb,var(--candidate) 5%,var(--panel)),var(--panel))}} .shot-head{{display:flex;justify-content:space-between;gap:15px;align-items:start}} .shot-head>div{{display:flex;align-items:center;gap:12px}} .shot-index{{color:var(--candidate);font:800 11px/1 monospace}} .shot-head strong{{font:850 24px/1 monospace}} .shot-size{{color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.07em}} .shot-card h4{{margin:20px 0 13px;font-size:17px;line-height:1.35}} .shot-grid{{display:grid;grid-template-columns:1fr 1fr;gap:11px}} .shot-grid>div{{padding:11px;border:1px solid var(--line);border-radius:11px;background:rgba(0,0,0,.12)}} .shot-grid label{{display:block;color:var(--candidate);font-size:10px;font-weight:800}} .shot-grid p{{margin:5px 0 0;color:#bfc3c9;font-size:11px}} .dialogue-list{{margin-top:12px}} .dialogue{{display:grid;grid-template-columns:75px 1fr auto;gap:10px;padding:9px 0;border-top:1px solid var(--line);font-size:11px}} .dialogue b{{color:var(--gold)}} .dialogue span{{color:#d1d3d7}} .dialogue i{{color:var(--muted)}} .silent{{display:block;padding:9px 0;color:var(--muted);font-size:11px}} .micro{{margin-top:8px}} .micro summary{{padding:10px 0;color:var(--muted);font-size:10px}} .micro p{{color:var(--muted);font-size:10px}}
    .score-review{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:16px}} .score-review>div{{padding:20px;border:1px solid var(--line);border-radius:18px;background:var(--panel)}} .score-review h3{{margin:0 0 14px;font-size:17px}} .mini-score{{display:grid;grid-template-columns:115px 1fr 30px;gap:10px;align-items:center;margin:10px 0;font-size:10px}} .mini-score span{{color:var(--muted)}} .mini-track{{height:7px;background:rgba(255,255,255,.06);border-radius:999px;overflow:hidden}} .mini-track i{{display:block;height:100%;background:var(--candidate)}} .mini-score b{{text-align:right;font-family:monospace}} .defects{{margin:0;padding-left:20px}} .defects li{{margin:11px 0;color:var(--candidate)}} .defects b{{display:block;font-size:11px}} .defects span{{display:block;color:var(--muted);font-size:10px}} .full-review,.raw-output{{margin-top:10px;border:1px solid var(--line);border-radius:14px;overflow:hidden}} .full-review table td:nth-child(2){{width:70px}} .full-review table td:last-child{{color:var(--muted)}}
    .takeaway{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} .take-card{{padding:25px;border:1px solid var(--line);border-radius:20px;background:var(--panel)}} .take-card b{{display:block;color:var(--gold);font-size:12px}} .take-card strong{{display:block;margin:10px 0;font-size:27px}} .take-card p{{margin:0;color:var(--muted)}} footer{{padding:34px 0;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}
    @media(max-width:850px){{.nav-links{{display:none}} .section-head{{grid-template-columns:1fr}} .clip-card{{grid-template-columns:1fr}} .scene-image{{min-height:420px}} .candidate-heading{{align-items:start}} .candidate-summary,.shots,.score-review,.takeaway{{grid-template-columns:1fr}} .candidate-summary>div{{border-right:0;border-bottom:1px solid var(--line)}} .candidate-facts{{flex-wrap:wrap}}}}
    @media(max-width:560px){{.shell{{width:calc(100% - 24px)}} .hero{{padding-top:56px}} h1{{font-size:44px}} .candidate-heading{{display:block}} .score-orb{{margin-top:20px;width:100%;height:auto}} .clip-copy{{padding:18px}} .scene-image{{min-height:330px}} .source-line{{grid-template-columns:1fr;gap:3px}} .shot-grid{{grid-template-columns:1fr}} .prompt-title,.storyboard-title{{display:block}} .prompt-title code{{display:block;margin-top:8px;overflow-wrap:anywhere}} .dialogue{{grid-template-columns:1fr}}}}
    @media print{{nav{{display:none}} body{{background:white;color:#161616}} :root{{--bg:#fff;--panel:#f5f5f3;--ink:#161616;--muted:#555;--line:rgba(0,0,0,.15)}} .candidate-section{{break-before:page}} details:not([open])>*:not(summary){{display:block}}}}
  </style>
</head>
<body>
  <nav><div class="shell nav-inner"><div class="brand">AutoDrama · Prompt / Shot Compare</div><div class="nav-links"><a href="#clip">Clip 原文</a><a href="#overview">总对比</a>{''.join(f'<a href="#candidate-{item["id"]}">{item["version"]}</a>' for item in candidates)}</div></div></nav>
  <header class="hero"><div class="shell"><div class="eyebrow">ONE CLIP · FIVE ACTUAL PROMPTS · FIVE SCORED OUTPUTS</div><h1>同一段原文，<br><em>提示词到底改变了什么？</em></h1><p class="lead">固定 clip、场景图、时长预算与模型路由，只替换提示词版本或其实际输入形态。下面直接看每一版实际送入 API 的 prompt、最终分镜和 rubric 分数。</p><div class="hero-facts"><span>配置 <b>saodi.yaml</b></span><span>模型 <b>aibox:gemini-3.6-flash</b></span><span>预算 <b>{case['available_seconds']} 秒</b></span><span>相邻 shot <b>默认切镜</b></span><span>单镜 <b>1–15 秒</b></span></div></div></header>

  <section id="clip"><div class="shell"><div class="section-head"><div><span>01 · FIXED SOURCE</span><h2>先看这一段原文</h2></div><p>这是所有候选共同收到的同一段 clip。难点不在“有没有镜头术语”，而在长解释期间是否还看得见听者状态、手势如何自然把注意力转到机关物件。</p></div><div class="clip-card"><div class="scene-image"><img src="{h(source_image)}" alt="九尾狐庭院场景锚点"></div><div class="clip-copy"><div class="clip-meta"><span>CASE · {h(CASE_ID)}</span><span>SCENE · {h(case['scene_id'])}</span><span>DIALOGUE · 4</span><span>REQUIRED BEATS · {len(case['required_beats'])}</span></div><p>{h(case['clip_text'])}</p><div class="source-lines">{clip_dialogues(case)}</div></div></div></div></section>

  <section id="overview"><div class="shell"><div class="section-head"><div><span>02 · AT A GLANCE</span><h2>五版提示词总览</h2></div><p>分数全部针对上面同一个 clip。绿色圆点表示该版明确拥有相应能力；得分为同一套 6 个硬门槛 + 14 个加权维度的匿名绝对评分。</p></div><div class="overview"><table><thead><tr><th>版本</th><th>专业切镜</th><th>lean 接口</th><th>长解释听者</th><th>精简输入</th><th>镜头数</th><th>总时长</th><th>得分</th></tr></thead><tbody>{overview_rows(candidates)}</tbody></table></div><div class="note"><b>这一个 clip 的最高分是 {best['version']} / {best['id']}：{best['score']['gated_score']:.4f}。</b>最终生产选择 {final['version']} / G，并不是因为它在每个单案都第一，而是它在完整 4+1 案例集上综合 9.18、零硬失败；这能避免用单一漂亮样片选出脆弱模板。</div></div></section>

  {sections}

  <section><div class="shell"><div class="section-head"><div><span>03 · VIEWER TAKEAWAY</span><h2>只看成片逻辑，怎么选？</h2></div><p>这份报告把工程过程收起来，只保留导演层面的可见差异。</p></div><div class="takeaway"><div class="take-card"><b>本 clip 最丰富的版本</b><strong>D · 9.6133</strong><p>4 镜：把长解释放进“九韶过肩看江未晞”的关系镜里，随后给机关 insert，再用 3 秒反应镜收束威胁感。</p></div><div class="take-card"><b>最终生产默认</b><strong>G · 9.3133</strong><p>3 镜：去掉额外反应收尾，用“双人状态 → 解释/手势 → 机关特写”完成更精炼的编辑结构；跨案例稳定性更高。</p></div></div></div></section>
  <footer><div class="shell">数据全部来自 <code>.tmp/clip-to-shots-template-evolution-20260809</code> 的真实 prompt、API 输出与匿名 rubric 评分；有效调用配置为仓库根目录 <code>saodi.yaml</code>。</div></footer>
  <script>
    const requestedView = new URLSearchParams(location.search).get("view");
    if (requestedView) {{
      const target = document.getElementById(requestedView);
      if (target) {{
        document.querySelectorAll("body > nav, body > header, body > section, body > footer").forEach((element) => {{
          if (element !== target) element.remove();
        }});
        target.style.paddingTop = "42px";
      }}
    }}
  </script>
</body></html>
"""


def main() -> int:
    cases, case = load_case()
    candidates = [load_candidate(spec) for spec in CANDIDATES]
    html_text = build_html(cases, case, candidates)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(html_text, encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "report": str(REPORT_PATH),
                "case": CASE_ID,
                "candidates": {
                    item["id"]: {
                        "score": item["score"]["gated_score"],
                        "shots": len(item["shots"]),
                        "duration": item["result"]["deterministic_audit"]["duration_sum"],
                    }
                    for item in candidates
                },
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
