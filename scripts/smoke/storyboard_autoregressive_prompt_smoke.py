from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.core.schemas import (  # noqa: E402
    Layout,
    ProjectState,
    Prop,
    Role,
    RoleAppearance,
    RoleAudio,
    ScriptBundle,
    StoryboardNextShotOutput,
    StoryboardShotDraft,
    StoryboardSourceCoverage,
)
from autodrama.providers.local.mock.fake import FakeTextProvider  # noqa: E402
from autodrama.services.storyboard_service import StoryboardService  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402

T = TypeVar("T", bound=BaseModel)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class RecordingFakeTextProvider(FakeTextProvider):
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        self.calls.append(
            {
                "schema": schema.__name__,
                "prompt": prompt,
                "metadata": dict(metadata or {}),
            }
        )
        return await super().generate_json(prompt, schema, temperature=temperature, metadata=metadata)


class AlwaysIncompleteTextProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _next_sentence(text: str, start: int) -> str:
        cursor = max(0, start)
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        sentence_end = text.find("。", cursor)
        if sentence_end < 0:
            raise AssertionError("Smoke source must contain another sentence")
        return text[cursor : sentence_end + 1]

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        del temperature
        metadata = dict(metadata or {})
        self.calls.append(
            {
                "schema": schema.__name__,
                "prompt": prompt,
                "metadata": metadata,
            }
        )
        source_text = str(metadata["current_novel_full"])
        start_text = str(metadata["current_shot_start_text"])
        start_offset = source_text.find(start_text)
        if start_offset < 0:
            raise AssertionError(f"current_shot_start_text not found in source: {start_text}")
        next_start_text = self._next_sentence(source_text, start_offset + len(start_text))
        return schema.model_validate(
            {
                "episode_key": metadata["episode_key"],
                "shot": {
                    "layout_id": "layout_雨夜办公室",
                    "title": f"未完成上限验证 {len(self.calls)}",
                    "source_coverage": {
                        "start_text": start_text,
                        "end_text": start_text,
                        "next_start_text": next_start_text,
                        "note": "covers one natural source chunk and keeps the chapter incomplete",
                    },
                    "duration_seconds": 4,
                    "transition": "结尾停在当前动作状态，可硬切到下一段。",
                    "dialogue": [],
                    "role_ids": [],
                    "role_appearance_ids": [],
                    "role_audio_ids": [],
                    "prop_ids": [],
                    "anchor_frame_prompt": "真人电影质感，雨夜办公室内的稳定中景，冷白灯照亮桌面。",
                    "video_prompt": "硬切进入雨夜办公室，镜头稳定推进，环境声保持在本片段内部，结尾停在可硬切状态。",
                },
                "is_chapter_complete": False,
                "completion_reason": "smoke provider intentionally leaves the chapter incomplete",
            }
        )


def build_state() -> ProjectState:
    return ProjectState(
        project_id="storyboard_autoregressive_prompt_smoke",
        title="Storyboard Autoregressive Prompt Smoke",
        raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
        script=ScriptBundle(
            raw_script="林舟发现合同被调包，并在会议室公开反击赵启。",
            novel_extract={
                "episode_001": (
                    "雨夜办公室，林舟发现合同关键页纸张颜色不对。苏晚递来旧邮件截图，"
                    "邮件附件时间线证明合同被调包。次日会议室，赵启试图压住议程，"
                    "林舟投屏证据并公开反击。"
                )
            }
        ),
        roles={
            "role_林舟": Role(
                id="role_林舟",
                name="林舟",
                intro="二十八岁职场青年，疲惫克制，关键时刻冷静锋利。",
                appearances={
                    "base": RoleAppearance(
                        id="role_林舟_appearance_base",
                        role_id="role_林舟",
                        name="base",
                        desc="短发，深灰衬衫，眼神疲惫但冷静。",
                        prompt="真人电影质感，中国青年男性，短发，深灰衬衫，神情克制。",
                    )
                },
                audio={
                    "normal": RoleAudio(
                        id="role_林舟_audio_normal",
                        role_id="role_林舟",
                        emotion="normal",
                        desc="青年男性音色，低沉克制。",
                    )
                },
            ),
            "role_苏晚": Role(
                id="role_苏晚",
                name="苏晚",
                intro="二十六岁数据分析师，理性冷静，善于发现证据。",
                appearances={
                    "base": RoleAppearance(
                        id="role_苏晚_appearance_base",
                        role_id="role_苏晚",
                        name="base",
                        desc="眉眼清冷，白衬衫，动作利落。",
                        prompt="真人电影质感，中国年轻女性，白衬衫，理性克制。",
                    )
                },
            ),
            "role_赵启": Role(
                id="role_赵启",
                name="赵启",
                intro="三十五岁部门主管，自负强势，证据曝光时会紧张失控。",
                appearances={
                    "base": RoleAppearance(
                        id="role_赵启_appearance_base",
                        role_id="role_赵启",
                        name="base",
                        desc="深色西装，表情自负，眼神有压迫感。",
                        prompt="真人电影质感，中国中年男性，深色西装，神情强势。",
                    )
                },
            ),
        },
        props={
            "prop_被调包的合同": Prop(
                id="prop_被调包的合同",
                name="被调包的合同",
                desc="关键页纸张颜色略浅，页码和装订孔有细微错位。",
                prompt="真人电影质感，商务合同特写，关键页色差和装订孔错位清楚。",
            ),
            "prop_邮件截图": Prop(
                id="prop_邮件截图",
                name="邮件截图",
                desc="旧邮件附件记录和时间线截图，是合同调包证据。",
                prompt="真人电影质感，电脑屏幕邮件截图，时间线和附件记录清楚。",
            ),
        },
        layouts={
            "layout_雨夜办公室": Layout(
                id="layout_雨夜办公室",
                name="雨夜办公室",
                desc="深夜现代办公室，冷白灯、电脑冷蓝光、窗外雨痕和城市霓虹反射。",
                prompt="真人电影质感，雨夜现代办公室，冷白灯，窗外雨痕，桌面合同和电脑。",
            ),
            "layout_会议室": Layout(
                id="layout_会议室",
                name="会议室",
                desc="玻璃会议室，长桌、投影屏、冷色顶灯和雨夜城市反射。",
                prompt="真人电影质感，现代玻璃会议室，长桌，投影屏，冷色顶灯。",
            ),
        },
        metadata={
            "episode_duration_seconds": 30,
        },
    )


async def main_async() -> int:
    provider = RecordingFakeTextProvider()
    service = StoryboardService(PromptStore())
    progress_snapshots: list[tuple[str, str, int]] = []
    state = build_state()
    require(
        StoryboardService.MAX_GENERATION_STEPS_PER_CHAPTER == 10,
        f"Storyboard max shot generation cap should be 10, got {StoryboardService.MAX_GENERATION_STEPS_PER_CHAPTER}",
    )
    shootable_story_text = (
        "雨夜办公室，林舟发现合同关键页纸张颜色不对。苏晚递来旧邮件截图，"
        "邮件附件时间线证明合同被调包。次日会议室，赵启试图压住议程，"
        "林舟投屏证据并公开反击。"
    )
    current_novel_full = (
        "源章节：第1章-第2章。\n\n"
        "《合同风暴》\n"
        "人物小传：\n"
        "林舟：二十八岁职场青年，长期被赵启压制。\n"
        "苏晚：数据分析师，后续会帮助林舟调查更多线索。\n\n"
        "第一集：\n"
        "1-1：雨夜办公室-夜-内\n"
        "人物：林舟、苏晚\n"
        f"{shootable_story_text}\n\n"
        "（未完待续）"
    )

    terminal_source = "源章节：第1章-第2章。\n\n雨夜办公室，林舟公开反击。\n\n（未完待续）"
    terminal_start_text, terminal_start_offset = service._source_anchor(terminal_source, 0)
    terminal_end_offset = service._story_text_end_offset(terminal_source)
    require(
        terminal_start_text.startswith("雨夜办公室"),
        f"Source anchor should skip source chapter metadata: {terminal_start_text}",
    )
    require(
        terminal_source[terminal_end_offset:].strip() == "（未完待续）",
        "Story end offset should stop before terminal metadata",
    )
    terminal_next = service._validate_source_coverage(
        StoryboardSourceCoverage(
            start_text="雨夜办公室，林舟公开反击。",
            end_text="雨夜办公室，林舟公开反击。",
            next_start_text=None,
            note="covers story text without the terminal marker",
        ),
        current_novel_full=terminal_source,
        current_shot_start_text=terminal_start_text,
        current_start_offset=terminal_start_offset,
        is_chapter_complete=True,
        source_end_offset=terminal_end_offset,
    )
    require(terminal_next is None, f"Completed terminal marker coverage should return None: {terminal_next}")

    whitespace_next_source = "前文结束。\n\n嗡——！\n\n天旋地转。\n\n后文继续。"
    whitespace_next = service._validate_source_coverage(
        StoryboardSourceCoverage(
            start_text="前文结束。",
            end_text="前文结束。",
            next_start_text="嗡——！天旋地转。",
            note="next anchor collapses paragraph whitespace",
        ),
        current_novel_full=whitespace_next_source,
        current_shot_start_text="前文结束。",
        current_start_offset=0,
        is_chapter_complete=False,
    )
    require(
        whitespace_next == whitespace_next_source.find("嗡——！"),
        f"Whitespace-normalized next_start_text resolved to wrong offset: {whitespace_next}",
    )

    whitespace_end_source = "甲。\n\n\n\n后文。\n\n后文。"
    whitespace_end = service._validate_source_coverage(
        StoryboardSourceCoverage(
            start_text="甲。后文。",
            end_text="甲。后文。",
            next_start_text="后文。",
            note="end anchor collapses whitespace before a repeated next anchor",
        ),
        current_novel_full=whitespace_end_source,
        current_shot_start_text="甲。后文。",
        current_start_offset=0,
        is_chapter_complete=False,
    )
    require(
        whitespace_end == whitespace_end_source.rfind("后文。"),
        f"Whitespace-normalized end_text did not advance past covered text: {whitespace_end}",
    )

    quote_variant_next_source = (
        "“铁牛哥，你再拍几次，我怕是进秘境之前就被你拍散架了。”韩默揉着肩膀苦笑道。\n\n"
        "铁牛咧嘴一乐，露出一口与粗犷外表不符的整齐白牙。他是唯一知道韩默底细的人。"
        "两个月前韩默被清虚门外门管事陷害，逃出宗门时身上只有一件破褂子，是铁牛路过，"
        "分了他半块粗饼。后来韩默凭着一手炼丹术在散修里渐渐站稳脚跟，"
        "铁牛也沾光得了些好处，两人就这么成了过命的交情。\n\n"
        "“愁啥？玉牌都到手了，难道还想退货？”铁牛从怀里掏出一张皱巴巴的羊皮地图。"
    )
    quote_variant_coverage = StoryboardSourceCoverage(
        start_text="“铁牛哥，你再拍几次，我怕是进秘境之前就被你拍散架了。”韩默揉着肩膀苦笑道。",
        end_text="“铁牛哥，你再拍几次，我怕是进秘境之前就被你拍散架了。”韩默揉着肩膀苦笑道。",
        next_start_text=(
            "铁牛咧嘴一乐，露出一口与粗犷外表不符的整齐白牙。他是唯一知道韩默底细的人。"
            "两个月前韩默被清虚门外门管事陷害，逃出宗门时身上只有一件破褂子，是铁牛路过，"
            "分了他半块粗饼。后来韩默凭着一手炼丹术在散修里渐渐站稳脚跟，"
            "铁牛也沾光得了些好处，两人就这么成了过命的交情。\n"
            '"愁啥？玉牌都到手了，难道还想退货？"铁牛从怀里掏出一张皱巴巴的羊皮地图。'
        ),
        note="next anchor tolerates straight quotes and is canonicalized to a short source cursor",
    )
    quote_variant_next = service._validate_source_coverage(
        quote_variant_coverage,
        current_novel_full=quote_variant_next_source,
        current_shot_start_text="“铁牛哥，你再拍几次，我怕是进秘境之前就被你拍散架了。”韩默揉着肩膀苦笑道。",
        current_start_offset=0,
        is_chapter_complete=False,
    )
    require(
        quote_variant_next == quote_variant_next_source.find("铁牛咧嘴一乐"),
        f"Quote-normalized next_start_text resolved to wrong offset: {quote_variant_next}",
    )
    require(
        quote_variant_coverage.next_start_text
        == "铁牛咧嘴一乐，露出一口与粗犷外表不符的整齐白牙。他是唯一知道韩默底细的人。",
        f"next_start_text should be canonicalized to a short source anchor: {quote_variant_coverage.next_start_text}",
    )

    leading_variant_source = (
        "韩默被竹枝逼到死角。\n\n"
        "他眼中闪过一抹狠戾。左手探入皮袋，摸到了那枚唯一的爆炎丹。"
        "这本来是准备对付筑基期妖兽的底牌，但眼下若不用，恐怕连用它的机会都没了。\n\n"
        "他不再犹豫，法力猛然注入丹丸。"
    )
    leading_variant_next = service._validate_source_coverage(
        StoryboardSourceCoverage(
            start_text="韩默被竹枝逼到死角。",
            end_text=(
                "韩默眼中闪过一抹狠戾。左手探入皮袋，摸到了那枚唯一的爆炎丹。"
                "这本来是准备对付筑基期妖兽的底牌，但眼下若不用，恐怕连用它的机会都没了。"
            ),
            next_start_text="他不再犹豫，法力猛然注入丹丸。",
            note="end anchor allows a short leading role-name/pronoun variant",
        ),
        current_novel_full=leading_variant_source,
        current_shot_start_text="韩默被竹枝逼到死角。",
        current_start_offset=0,
        is_chapter_complete=False,
    )
    require(
        leading_variant_next == leading_variant_source.find("他不再犹豫"),
        f"Leading-variant end_text did not resolve the next source cursor: {leading_variant_next}",
    )

    bad_end_good_next_source = (
        "韩默缓缓站起身，尽量不发出任何声响。\n\n"
        "他强迫自己冷静下来，先运功查看体内状况。传送带来的紊乱已经平复大半，"
        "经脉虽还有些酸痛，但法力流转已无大碍。而真正让他心头一跳的，是空气中那充沛到近乎粘稠的灵气——"
        "他甚至不用刻意运功，灵气就顺着全身毛孔往体内钻，在经脉里欢快地游走。\n\n"
        "韩默心中一喜。如此浓郁的灵气，若是能吸纳炼化，别说十天，只需打坐半日。"
    )
    bad_end_good_next = service._validate_source_coverage(
        StoryboardSourceCoverage(
            start_text="韩默缓缓站起身，尽量不发出任何声响。",
            end_text=(
                "他强迫自己冷静下来，先运功查看体内状况。传送带来的紊乱已经平复大半，"
                "经脉虽还有些酸痛，但法力流转已无大碍，而真正让他心头一跳的，是空气中那充沛到近乎粘稠的灵气。"
            ),
            next_start_text="韩默心中一喜。如此浓郁的灵气，若是能吸纳炼化，别说十天，只需打坐半日。",
            note="incomplete shot should use next_start_text as the authoritative cursor",
        ),
        current_novel_full=bad_end_good_next_source,
        current_shot_start_text="韩默缓缓站起身，尽量不发出任何声响。",
        current_start_offset=0,
        is_chapter_complete=False,
    )
    require(
        bad_end_good_next == bad_end_good_next_source.find("韩默心中一喜"),
        f"Incomplete coverage should tolerate a bad end_text when next_start_text is valid: {bad_end_good_next}",
    )

    repaired_dialogue_shot = service._normalize_generated_shot(
        StoryboardShotDraft(
            layout_id="layout_会议室",
            title="对白补全验证",
            source_coverage=StoryboardSourceCoverage(
                start_text="林舟公开反击。",
                end_text="林舟公开反击。",
                next_start_text=None,
                note="verifies deterministic video_prompt dialogue completion",
            ),
            duration_seconds=8,
            transition="结尾可硬切。",
            dialogue=["林舟：这份合同被换过，时间线就在这里。"],
            role_ids=["role_林舟"],
            role_appearance_ids=["role_林舟_appearance_base"],
            role_audio_ids=["role_林舟_audio_normal"],
            prop_ids=["prop_邮件截图"],
            anchor_frame_prompt="真人电影质感，会议室中林舟站在投影屏旁。",
            video_prompt="林舟站在投影屏旁开口，赵启坐在阴影里听他说话。",
        ),
        episode_key="episode_001",
        shot_index=99,
    )
    require(
        "林舟：这份合同被换过，时间线就在这里。" in repaired_dialogue_shot.video_prompt,
        "Normalized video_prompt should include every missing dialogue line verbatim",
    )
    require("参考图只用于锁定" in repaired_dialogue_shot.video_prompt, "video_prompt missing reference image guidance")
    require("参考视频只用于锁定" in repaired_dialogue_shot.video_prompt, "video_prompt missing reference video guidance")
    require("参考音频或对白音频" in repaired_dialogue_shot.video_prompt, "video_prompt missing reference audio guidance")

    def record_progress(episode, shot) -> None:
        progress_snapshots.append((episode.episode_key, shot.shot_id, len(episode.shots)))

    output = await service.storyboard_episode(
        state,
        provider,
        episode_key="episode_001",
        novel_extract_all=dict(state.script.novel_extract),
        current_novel_full=current_novel_full,
        on_shot_generated=record_progress,
    )

    require(output.episode_key == "episode_001", f"Unexpected episode_key: {output.episode_key}")
    require(len(output.shots) == 3, f"Expected 3 autoregressive shots, got {len(output.shots)}")
    require(service.last_text_call_count == 3, f"Expected 3 text calls, got {service.last_text_call_count}")
    require(len(provider.calls) == 3, f"Expected 3 provider calls, got {len(provider.calls)}")
    for index, shot in enumerate(output.shots, start=1):
        require(shot.index == index, f"Shot index was not normalized: {shot.index}")
        require(shot.shot_id == f"episode_001_shot_{index:03d}", f"Unexpected shot_id: {shot.shot_id}")
        require(" 秒：" in shot.video_prompt, f"Shot {index} video_prompt missing official-style timed segment")
        require(
            any(marker in shot.video_prompt for marker in ("全程不切镜", "可硬切", "J-Cut 式", "硬切到")),
            f"Shot {index} video_prompt missing natural cut/continuity wording",
        )
        require(shot.source_coverage is not None, f"Shot {index} missing source coverage")
    require(
        progress_snapshots == [
            ("episode_001", "episode_001_shot_001", 1),
            ("episode_001", "episode_001_shot_002", 2),
            ("episode_001", "episode_001_shot_003", 3),
        ],
        f"Unexpected progress snapshots: {progress_snapshots}",
    )

    schema_names = {call["schema"] for call in provider.calls}
    require(schema_names == {StoryboardNextShotOutput.__name__}, f"Unexpected schemas: {schema_names}")
    require("已经生成并已覆盖的分镜" in provider.calls[0]["prompt"], "Prompt missing covered storyboard guidance")
    require("当前 shot 起点原文" in provider.calls[0]["prompt"], "Prompt missing next shot source cursor")
    require("当前是本集第一镜，没有上一镜头视频可承接" in provider.calls[0]["prompt"], "First prompt missing no-previous-shot guidance")
    require("0-2秒取上一镜视频最后2秒作为开场预滚" not in provider.calls[0]["prompt"], "First prompt should not request previous-shot preroll")
    require("0-3 秒：" in provider.calls[0]["prompt"], "Prompt missing official-style timed video_prompt guidance/example")
    require("方括号标签" in provider.calls[0]["prompt"], "Prompt missing guidance against old bracketed style")
    require("硬切到" in provider.calls[0]["prompt"], "Prompt missing natural explicit cut guidance/example")
    require("每一句对白" in provider.calls[0]["prompt"], "Prompt missing dialogue-in-video_prompt requirement")
    require("参考音频" in provider.calls[0]["prompt"], "Prompt missing audio reference guidance")
    require("首尾衔接要求" in provider.calls[0]["prompt"], "Prompt missing hard-cut boundary guidance")
    require("运镜协调性要求" in provider.calls[0]["prompt"], "Prompt missing camera movement coordination guidance")
    require("人物、场景和空间关系都相对固定" in provider.calls[0]["prompt"], "Prompt missing stable-scene camera restraint guidance")
    require("快速拉近后又快速拉远" in provider.calls[0]["prompt"], "Prompt missing abrupt push-pull camera warning")
    require("新进入一个大场景" in provider.calls[0]["prompt"], "Prompt missing large-scene camera exception")
    require("人物战斗、奔跑、追逐" in provider.calls[0]["prompt"], "Prompt missing action camera exception")
    require("内部 J-Cut" in provider.calls[0]["prompt"], "Prompt missing internal-only J-Cut guidance")
    require("可演性预算" in provider.calls[0]["prompt"], "Prompt missing performability budget guidance")
    require("4-10 秒" in provider.calls[0]["prompt"], "Prompt missing 10-second shot budget")
    require("最多承载 1 个说话角色" in provider.calls[0]["prompt"], "Prompt missing single-speaker limit")
    require("最多 2 句完整对白" in provider.calls[0]["prompt"], "Prompt missing dialogue density limit")
    require("不超过 50 个中文字符" in provider.calls[0]["prompt"], "Prompt missing 50-char dialogue limit")
    require("画面中央人物角色身份板 + 场景参考图" in provider.calls[0]["prompt"], "Prompt missing downstream image reference limit")
    require("上一镜头视频和一个说话角色音频" in provider.calls[0]["prompt"], "Prompt missing downstream reference limit")
    require("画面主体" in provider.calls[0]["prompt"], "Prompt missing VO visual-subject guidance")
    require("B（VO）：台词" in provider.calls[0]["prompt"], "Prompt missing explicit VO dialogue guidance")
    require("不张嘴、不对口型" in provider.calls[0]["prompt"], "Prompt missing onscreen/offscreen voice separation guidance")
    require("犹豫时，切短一点" in provider.calls[0]["prompt"], "Prompt missing low-density split preference")
    require("context_only" in provider.calls[0]["prompt"], "Prompt missing mature-screenplay context-only guidance")
    require("不是文本句子" in provider.calls[0]["prompt"], "Prompt missing beat-based shot unit guidance")
    require("max_shots_per_chapter" not in provider.calls[0]["prompt"], "Prompt should not expose shot count cap")
    require("remaining_shot_slots" not in provider.calls[0]["prompt"], "Prompt should not expose remaining shot slots")
    require(
        "max_shots_per_chapter" not in provider.calls[0]["metadata"],
        "Provider metadata should not expose shot count cap",
    )
    require(
        "remaining_shot_slots" not in provider.calls[0]["metadata"],
        "Provider metadata should not expose remaining shot slots",
    )
    require(
        provider.calls[0]["metadata"]["current_shot_start_text"].startswith("1-1：雨夜办公室"),
        f"First storyboard cursor should skip mature screenplay header: {provider.calls[0]['metadata']}",
    )
    first_cursor_section = provider.calls[0]["prompt"].split("当前 shot 起点原文：", 1)[1].split("## 资产与风格", 1)[0]
    require("源章节" not in first_cursor_section, f"First cursor section should not include metadata: {first_cursor_section}")
    require("人物小传" not in first_cursor_section, f"First cursor section should not include character bios: {first_cursor_section}")
    require('"source_coverage"' in provider.calls[1]["prompt"], "Second prompt missing first shot source coverage")
    require("episode_001_shot_001" in provider.calls[1]["prompt"], "Second prompt missing first shot id")
    require("当前配置不使用上一镜视频预滚" in provider.calls[1]["prompt"], "Second prompt missing disabled-preroll guidance")
    require("0-2秒取上一镜视频最后2秒作为开场预滚" not in provider.calls[1]["prompt"], "Second prompt should not request previous-shot preroll by default")
    require("episode_001_shot_002" in provider.calls[2]["prompt"], "Third prompt missing two generated shots context")

    capped_max_shots = 4
    capped_provider = AlwaysIncompleteTextProvider()
    capped_service = StoryboardService(PromptStore())
    capped_story = "".join(
        f"第{index:02d}段里角色沿着长廊观察灵光变化并保持警惕，动作连续但还没有抵达终点。"
        for index in range(1, 13)
    )
    capped_output = await capped_service.storyboard_episode(
        state,
        capped_provider,
        episode_key="episode_001",
        novel_extract_all=dict(state.script.novel_extract),
        current_novel_full=capped_story,
        max_shots=capped_max_shots,
    )
    require(
        len(capped_output.shots) == capped_max_shots,
        f"Storyboard code cap should return at most {capped_max_shots} shots, got {len(capped_output.shots)}",
    )
    require(
        len(capped_provider.calls) == capped_max_shots,
        f"Storyboard code cap should call provider at most {capped_max_shots} times, got {len(capped_provider.calls)}",
    )
    require(
        all(not call["metadata"].get("max_shots_per_chapter") for call in capped_provider.calls),
        "Shot count cap should stay outside provider-visible metadata",
    )
    require(
        all("max_shots" not in call["metadata"] for call in capped_provider.calls),
        "CLI/config shot count cap should stay outside provider-visible metadata",
    )

    output_dir = ROOT_DIR / ".tmp" / "smoke" / "storyboard_autoregressive_prompt"
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (output_dir / f"episode_{stamp}.json").write_text(
        output.model_dump_json(indent=2),
        encoding="utf-8",
    )
    for index, call in enumerate(provider.calls, start=1):
        (output_dir / f"prompt_{index:03d}_{stamp}.md").write_text(call["prompt"], encoding="utf-8")

    print("storyboard_autoregressive_prompt_smoke=ok")
    print(f"shots={len(output.shots)} text_calls={service.last_text_call_count}")
    print(f"output_dir={output_dir}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
