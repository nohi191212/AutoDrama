from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.utils.video_prompts import (  # noqa: E402
    VIDEO_PROMPT_STYLE_PREFIX,
    VideoDialogueCue,
    VideoRoleBinding,
    classify_video_prompt_profile,
    compile_kling_video_prompt,
    is_prop_transfer_action,
    sanitize_video_prompt_text,
)
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.core.errors import ProviderError  # noqa: E402
from autodrama.providers.kling.video.omni import KlingOmniVideoProvider  # noqa: E402
from autodrama.workflows.generation import GenerationWorkflow  # noqa: E402
from autodrama.workflows.nodes.video_audit_node import ShotVideoAuditNode  # noqa: E402


OUTPUT_DIR = ROOT / ".tmp" / "video-prompt-workflow-solidification"
SAODI_MANIFEST = ROOT / "outputs" / "saodi_0803" / "shots" / "episode_001.json"


def require(value: bool, message: str) -> None:
    if not value:
        raise AssertionError(message)


def compile_case(
    *,
    duration: float,
    text: str | None,
    action: str,
    camera: str = "locked_off",
    has_props: bool = False,
    delivery_mode: str = "on_screen",
    max_characters: int | None = None,
) -> tuple[str, str]:
    cues = (
        [
            VideoDialogueCue(
                speaker="叶凡",
                role_token="@role_1",
                text=text,
                delivery_mode=delivery_mode,
                emotion="normal",
            )
        ]
        if text
        else []
    )
    profile = classify_video_prompt_profile(cues, duration_seconds=duration)
    prompt = compile_kling_video_prompt(
        f"Action: {action}. Camera: medium close; {camera}.",
        duration_seconds=duration,
        dialogue_cues=cues,
        role_bindings=[VideoRoleBinding(role_token="@role_1", name="叶凡")],
        action=action,
        camera_movement=camera,
        has_props=has_props,
        max_characters=max_characters,
    )
    require("Audio: human voices and SFX only" in prompt, "compiled video prompt must keep the voice/SFX-only contract")
    require("no BGM" in prompt, "compiled video prompt must explicitly forbid BGM")
    return profile, prompt


def role_bindings_from_manifest(shot: dict[str, Any]) -> tuple[list[VideoRoleBinding], dict[str, str]]:
    names = {
        str(item.get("role_id") or ""): str(item.get("role_name") or "")
        for item in shot.get("video_inputs") or []
        if item.get("asset_type") == "roleboard"
    }
    bindings: list[VideoRoleBinding] = []
    tokens: dict[str, str] = {}
    for index, role_id in enumerate(shot.get("role_ids") or [], start=1):
        token = f"@role_{index}"
        tokens[str(role_id)] = token
        bindings.append(VideoRoleBinding(role_token=token, name=names.get(str(role_id)) or str(role_id)))
    return bindings, tokens


def compile_manifest_shot(shot: dict[str, Any]) -> tuple[str, str]:
    bindings, tokens = role_bindings_from_manifest(shot)
    cues = [
        VideoDialogueCue(
            speaker=str(line.get("speaker_name") or line.get("speaker_role_id") or "未指定说话人"),
            role_token=tokens.get(str(line.get("speaker_role_id") or "")),
            text=str(line.get("text") or ""),
            delivery_mode=str(line.get("delivery_mode") or "on_screen"),
            emotion=str(line.get("emotion") or "normal"),
        )
        for line in shot.get("dialogue_lines") or []
    ]
    profile = classify_video_prompt_profile(cues, duration_seconds=float(shot["duration_seconds"]))
    prompt = compile_kling_video_prompt(
        shot["video_prompt"],
        duration_seconds=float(shot["duration_seconds"]),
        dialogue_cues=cues,
        role_bindings=bindings,
        action=shot.get("shot_description") or shot.get("content") or shot.get("title") or "",
        camera_movement=shot.get("camera_movement") or "",
        has_props=bool(shot.get("prop_ids")),
        max_characters=3072,
    )
    require("Audio: human voices and SFX only" in prompt, "manifest video prompt must keep the voice/SFX-only contract")
    require("no BGM" in prompt, "manifest video prompt must explicitly forbid BGM")
    return profile, prompt


def main() -> None:
    cases: dict[str, dict[str, str]] = {}

    prompt_template_paths = [
        ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "clip_to_shots" / "default.md",
        ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "video_asset_audit" / "default.md",
    ]
    prompt_template_text = "\n".join(path.read_text(encoding="utf-8") for path in prompt_template_paths)
    for forbidden_story_detail in ("叶凡", "李德海", "柳菡烟", "赤金小炉", "蹲下", "看向远方"):
        require(
            forbidden_story_detail not in prompt_template_text,
            f"production prompt template must not contain story-specific detail: {forbidden_story_detail}",
        )
    clip_plan_prompt = PromptStore().render(
        "clip_to_shots",
        scene_description="A supplied scene anchor.",
        previous_context="None.",
        clip_text="The supplied story event.",
        next_context="None.",
        entity_index="No named fixture.",
        foreground_reference_budget=3,
    )
    require("{{" not in clip_plan_prompt, "clip_to_shots template must render without unresolved variables")
    require("开始画面" in clip_plan_prompt, "clip planner must define the opening-frame state")
    require("按正常播放速度" in clip_plan_prompt, "clip planner must estimate duration from performed visual beats")
    require("当前片段的内容完整性和自然节奏高于外部时长" in clip_plan_prompt, "external duration must not steer shot design")
    cases["template_scope_guard"] = {
        "profile": "prompt_style",
        "prompt": "templates contain no experiment characters, props, or example story actions",
    }

    short_profile, short_prompt = compile_case(
        duration=4,
        text="要一个回答。",
        action="Ye Fan turns his gaze toward the gate after answering",
    )
    require(short_profile == "short_dialogue", "short line must use the explicit timeline profile")
    require("Dialogue, 0.0–" in short_prompt and "closes the mouth" in short_prompt, "short prompt needs speech and settle partitions")
    require("0.0–1.3" in short_prompt, "five-unit short line must use the validated M07 timing pressure")
    require("first audible unit" in short_prompt and "without a silent setup or an internal pause" in short_prompt, "short prompt must prevent the isolated-first-word failure")
    require("Other visible people remain silent with closed mouths" in short_prompt, "single-speaker prompt must keep listeners silent")
    require(short_prompt.index("要一个回答") < short_prompt.index("Shot:"), "dialogue must precede visible acting")
    compiled_style = short_prompt.split("\nShot:", 1)[0].casefold()
    for skill_term in ("contract", "scene plan", "pantomime", "gaze", "breath", "lean", "gesture", "footfall", "staff", "cane", "robe"):
        require(skill_term not in compiled_style, f"compiler style must not inject example action: {skill_term}")
    cases["short_dialogue"] = {"profile": short_profile, "prompt": short_prompt}

    m05_profile, m05_prompt = compile_case(
        duration=4,
        text="我还有个执念。",
        action="Ye Fan lowers his eyes briefly toward the planted staff after speaking, then settles on Li Dehai",
    )
    require(m05_profile == "short_dialogue", "M05 line must use the short-dialogue profile")
    require("0.0–1.5" in m05_prompt, "six-unit M05 line must use the validated compact phrase window")
    require("one connected natural phrase" in m05_prompt, "M05 prompt must resist an internal pause after the first word")
    cases["short_dialogue_m05_connected_phrase"] = {"profile": m05_profile, "prompt": m05_prompt}

    medium_profile, medium_prompt = compile_case(
        duration=5,
        text="什么执念，比命还重要？",
        action="Li Dehai studies Ye Fan while asking the question",
        camera="a subtle push toward Li Dehai",
    )
    require(medium_profile == "medium_dialogue", "medium line must use dialogue-first camera profile")
    require("supplied camera movement unfold during the words" in medium_prompt, "medium camera movement must stay under dialogue")
    require(medium_prompt.index("什么执念") < medium_prompt.index("Shot:"), "medium dialogue must come first")
    cases["medium_dialogue"] = {"profile": medium_profile, "prompt": medium_prompt}

    long_profile, long_prompt = compile_case(
        duration=11,
        text="叶凡哥哥，只要你正式拜入宗门，哪怕只是外门，菡烟就嫁给你。",
        action="Liu Hanyan makes a sincere promise and holds Ye Fan's gaze",
    )
    require(long_profile == "long_dialogue", "long line must use the minimal start-deadline profile")
    require("begins within the first 0.8 seconds" in long_prompt, "long dialogue needs the validated start deadline")
    require("no separate silent opening" in long_prompt, "long dialogue must avoid a pre-speech setup")
    cases["long_dialogue"] = {"profile": long_profile, "prompt": long_prompt}

    silent_profile, silent_prompt = compile_case(
        duration=7,
        text=None,
        action="Ye Fan hands the sealed copper vessel to Liu Hanyan",
        has_props=True,
    )
    require(silent_profile == "silent_action", "silent handoff must use the silent action profile")
    require("contact establishes control" in silent_prompt, "handoff prompt needs a concise tactile style")
    require("supported weight transfer precedes release" in silent_prompt, "handoff style must encode gravity and release")
    require(not any(term in silent_prompt.casefold() for term in ("flame", "ember", "glow")), "handoff prompt must not activate forbidden heat vocabulary")
    require(compile_kling_video_prompt(silent_prompt, duration_seconds=7) == silent_prompt, "compiler must be idempotent")
    cases["silent_prop_handoff"] = {"profile": silent_profile, "prompt": silent_prompt}

    locomotion_profile, locomotion_prompt = compile_case(
        duration=6,
        text=None,
        action="Elderly Ye Fan takes one step across the threshold and plants the carved staff before his weight follows",
        has_props=True,
        camera="a restrained forward drift during the step",
    )
    require(locomotion_profile == "silent_action", "silent threshold crossing must use the silent-action profile")
    require(not is_prop_transfer_action("Ye Fan takes one step"), "locomotion phrase must not be misclassified as a prop handoff")
    require("without adding another action" in locomotion_prompt, "silent style must forbid invented action")
    require("ends with it" in locomotion_prompt, "silent camera motion must stop with the described action")
    require("balance, support and weight transfer remain believable" in locomotion_prompt, "locomotion prompt needs grounded physical style")
    for injected_detail in ("heel", "sole", "staff or cane", "robe and loose fabric"):
        require(injected_detail not in locomotion_prompt.casefold(), f"locomotion style must not inject story detail: {injected_detail}")
    cases["silent_grounded_locomotion"] = {"profile": locomotion_profile, "prompt": locomotion_prompt}

    for duration, text in ((1.0, "答。"), (2.0, "回答。")):
        boundary_profile, boundary_prompt = compile_case(
            duration=duration,
            text=text,
            action="Ye Fan answers immediately and settles",
        )
        require(boundary_profile == "short_dialogue", f"{duration}s boundary must stay on short-dialogue timeline")
        intervals = re.findall(r"(\d+\.\d)–(\d+\.\d)", boundary_prompt)
        require(bool(intervals), f"{duration}s boundary prompt needs explicit intervals")
        require(
            all(float(start) <= float(end) <= duration for start, end in intervals),
            f"{duration}s boundary prompt must not schedule action beyond shot duration",
        )
        cases[f"short_dialogue_{int(duration)}s_boundary"] = {
            "profile": boundary_profile,
            "prompt": boundary_prompt,
        }

    offscreen_profile, offscreen_prompt = compile_case(
        duration=5,
        text="门外有人。",
        action="Ye Fan hears the warning and turns toward the closed gate",
        delivery_mode="offscreen",
    )
    require(offscreen_profile == "offscreen_dialogue", "single offscreen line needs a dedicated profile")
    require("voice remains outside the frame" in offscreen_prompt, "offscreen source must remain outside frame")
    require("all visible mouths stay closed" in offscreen_prompt, "offscreen dialogue must not activate visible lips")
    require("gaze" not in offscreen_prompt, "offscreen prompt must not invent visible acting")
    cases["offscreen_dialogue"] = {"profile": offscreen_profile, "prompt": offscreen_prompt}

    mixed_cues = [
        VideoDialogueCue(speaker="叶凡", role_token="@role_1", text="谁？", delivery_mode="on_screen"),
        VideoDialogueCue(speaker="门外人", text="是我。", delivery_mode="offscreen"),
        VideoDialogueCue(speaker="旁白", text="夜色更深。", delivery_mode="voiceover"),
    ]
    mixed_profile = classify_video_prompt_profile(mixed_cues, duration_seconds=8)
    mixed_prompt = compile_kling_video_prompt(
        "Ye Fan faces the closed gate while the room darkens.",
        duration_seconds=8,
        dialogue_cues=mixed_cues,
        role_bindings=[VideoRoleBinding(role_token="@role_1", name="叶凡")],
    )
    require(mixed_profile == "multi_dialogue", "mixed delivery modes need ordered multi-dialogue handling")
    require(mixed_prompt.index("谁？") < mixed_prompt.index("是我。") < mixed_prompt.index("夜色更深。"), "mixed dialogue order must be exact")
    require("only the named speaker moves the lips" in mixed_prompt, "on-screen line needs exclusive lip ownership")
    require("Offscreen and voiceover lines never activate a visible mouth" in mixed_prompt, "non-visible voices must not activate visible lip sync")
    cases["mixed_multi_dialogue"] = {"profile": mixed_profile, "prompt": mixed_prompt}

    bounded_profile, bounded_prompt = compile_case(
        duration=5,
        text="什么执念，比命还重要？",
        action="A figure follows the supplied scene action. " * 54,
        camera="a subtle push toward the speaker",
        max_characters=3072,
    )
    require(bounded_profile == "medium_dialogue", "bounded case must preserve dialogue profile")
    require(len(bounded_prompt) <= 3072, "official v3 prompt must fit its real provider limit")
    require("什么执念，比命还重要？" in bounded_prompt, "provider budgeting must never truncate exact dialogue")
    require("A figure follows the supplied scene action. " * 54 in bounded_prompt, "provider budgeting must preserve supplied story content verbatim")
    require("…" not in bounded_prompt, "provider budgeting must not shorten supplied story content")
    cases["official_v3_bounded_prompt"] = {"profile": bounded_profile, "prompt": bounded_prompt}

    try:
        compile_case(
            duration=15,
            text="字" * 3000,
            action="The speaker delivers the exact supplied line",
            max_characters=3072,
        )
    except ValueError as exc:
        require("essential video prompt content" in str(exc), "oversized exact dialogue must fail with an actionable error")
        cases["oversized_exact_dialogue"] = {"profile": "rejected", "prompt": str(exc)}
    else:
        raise AssertionError("oversized exact dialogue must never be silently truncated")

    official_provider = object.__new__(KlingOmniVideoProvider)
    official_provider.api_schema = "official_v3"
    legacy_provider = object.__new__(KlingOmniVideoProvider)
    legacy_provider.api_schema = "legacy"
    require(official_provider.max_prompt_characters == 3072, "official v3 provider limit must be exposed")
    require(legacy_provider.max_prompt_characters == 2500, "legacy provider limit must be exposed")
    for provider, over_limit in ((official_provider, 3073), (legacy_provider, 2501)):
        try:
            provider._validated_prompt("x" * over_limit)
        except ProviderError:
            pass
        else:
            raise AssertionError(f"{provider.api_schema} provider must reject instead of truncate overlong prompts")

    integrated_shot = SimpleNamespace(
        role_ids=["role_叶凡"],
        dialogue_lines=[
            SimpleNamespace(
                speaker_role_id="role_叶凡",
                speaker_name="叶凡",
                text="要一个回答。",
                delivery_mode="on_screen",
                emotion="normal",
            )
        ],
        video_prompt="Ye Fan turns toward the gate after answering",
        shot_description="Ye Fan turns toward the gate after answering",
        content=None,
        title="answer",
        camera=SimpleNamespace(movement="locked_off"),
        camera_movement="locked_off",
        duration_seconds=4.0,
        prop_ids=["prop_木杖"],
    )
    integrated_prompt = GenerationWorkflow._kling_native_prompt(
        None,
        "Opening and visible action plan. " * 20,
        SimpleNamespace(roles={"role_叶凡": SimpleNamespace(name="叶凡")}),
        integrated_shot,
        SimpleNamespace(supports_kling_omni_placeholders=True, max_prompt_characters=2500),
    )
    require(integrated_prompt.startswith(VIDEO_PROMPT_STYLE_PREFIX), "workflow submission path must call the current compiler")
    require(len(integrated_prompt) <= 2500, "workflow submission path must compile to the legacy provider budget")
    require(integrated_prompt.index("要一个回答") < integrated_prompt.index("Shot:"), "workflow integration must keep dialogue first")
    require("contract" not in integrated_prompt.casefold(), "submitted video prompt must read like a prompt, not a skill contract")
    audit_prompt = PromptStore().render(
        "video_asset_audit",
        shot_name="integration_shot",
        expectation="对白必须完整且立即开始。",
        current_prompt=integrated_prompt,
    )
    require("首次开口时间" in audit_prompt, "video audit template must check dialogue start timing")
    require("接触、支撑、重量转移和释放关系" in audit_prompt, "video audit template must check physical object motion")
    require("既有主动作完成时间" in audit_prompt, "video audit template must measure existing silent-action pacing")
    require("随其落点结束" in audit_prompt, "video audit template must reject camera drift through the cut tail")
    require("不得要求输入中没有的新动作" in audit_prompt, "video audit template must not invent repair content")
    offscreen_expectation = ShotVideoAuditNode._expectation(
        SimpleNamespace(
            dialogue_lines=[
                SimpleNamespace(
                    speaker_name="门外人",
                    speaker_role_id=None,
                    text="门外有人。",
                    delivery_mode="offscreen",
                    emotion="tense",
                )
            ],
            duration_seconds=5.0,
            video_prompt="Ye Fan turns toward the closed gate",
            shot_description="Ye Fan turns toward the closed gate",
            content=None,
            title="offscreen warning",
            narrative_angle="The warning redirects Ye Fan's attention.",
            prop_ids=[],
        )
    )
    require("声源始终在画外" in offscreen_expectation, "video audit expectation must preserve an offscreen source")
    require("所有画中人物始终闭嘴" in offscreen_expectation, "offscreen audit must reject visible lip activation")
    locomotion_expectation = ShotVideoAuditNode._expectation(
        SimpleNamespace(
            dialogue_lines=[],
            duration_seconds=6.0,
            video_prompt="Elderly Ye Fan takes one step across the threshold with his staff",
            shot_description="Elderly Ye Fan takes one step across the threshold with his staff",
            content=None,
            title="threshold crossing",
            narrative_angle="He enters with grounded resolve.",
            prop_ids=["prop_staff"],
        )
    )
    require("记录输入中已有主动作的完成时间" in locomotion_expectation, "silent audit expectation must measure existing pacing phases")
    require("可信的平衡、支撑和重量转移" in locomotion_expectation, "locomotion audit must check grounded physical style")
    require("不得增加未描述的位移动作" in locomotion_expectation, "locomotion audit must not invent action")

    replay: list[dict[str, Any]] = []
    if SAODI_MANIFEST.is_file():
        manifest = json.loads(SAODI_MANIFEST.read_text(encoding="utf-8"))
        for shot in manifest.get("shots") or []:
            profile, prompt = compile_manifest_shot(shot)
            require(prompt.startswith(VIDEO_PROMPT_STYLE_PREFIX), f"missing current prompt style for {shot['shot_id']}")
            require(sanitize_video_prompt_text(shot["video_prompt"]) in prompt, f"missing intact source shot prompt for {shot['shot_id']}")
            require(len(prompt) <= 3072, f"official v3 prompt budget exceeded for {shot['shot_id']}")
            replay.append({"shot_id": shot["shot_id"], "profile": profile, "prompt": prompt})

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / "video_prompt_compiler_smoke.json"
    output_path.write_text(
        json.dumps({"cases": cases, "saodi0803_replay": replay}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({"status": "passed", "cases": len(cases), "replayed_shots": len(replay), "output": str(output_path)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
