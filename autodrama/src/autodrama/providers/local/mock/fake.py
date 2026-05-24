from __future__ import annotations

import base64
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from autodrama.core.schemas import (
    AmbientEntityOutput,
    BGMDesignOutput,
    LayoutDedupeReviewOutput,
    LayoutDesignOutput,
    LayoutExtractOutput,
    PropDesignOutput,
    PropExtractOutput,
    RoleAppearanceDesignOutput,
    RoleDesignOutput,
    RoleDuplicateAuditReviewOutput,
    RoleEpisodeKeyAuditReviewOutput,
    RoleExtractOutput,
    RoleVoiceDesignOutput,
    ScriptNovelExtractBatchOutput,
    ScriptNovelEpisodeOutput,
    ScriptOutlineOutput,
    ShotBGMSoundDesignOutput,
    StoryboardEpisodeOutput,
    StoryboardNextShotOutput,
    StoryboardShotGenerationOutput,
)
from autodrama.core.voice_catalog import (
    VoiceCatalogProfile,
    VoiceSelectAudioJudgeOutput,
    VoiceSelectShortlistOutput,
)
from autodrama.providers.base import (
    AssetRef,
    ImageGenerationResult,
    MusicGenerationResult,
    VideoGenerationResult,
    VoiceDesignResult,
    VoiceSynthesisResult,
)

T = TypeVar("T", bound=BaseModel)


def _extract_prompt_int(prompt: str, label: str, default: int) -> int:
    match = re.search(rf"{re.escape(label)}\s*[：:]\s*(\d+)", prompt)
    if match:
        return int(match.group(1))
    return default


def _episode_keys(episode_count: int) -> list[str]:
    return [f"episode_{index:03d}" for index in range(1, episode_count + 1)]


def _fake_source_anchor(text: str, start_offset: int, *, limit: int = 96) -> str:
    cursor = max(0, start_offset)
    while cursor < len(text) and text[cursor].isspace():
        cursor += 1
    end_offset = min(len(text), cursor + limit)
    for index in range(min(len(text), cursor + 24), end_offset):
        if text[index] in "。！？；\n":
            end_offset = index + 1
            break
    return text[cursor:end_offset].strip()


def _fake_sentence_end(text: str, start_offset: int) -> int:
    for index in range(max(0, start_offset), len(text)):
        if text[index] in "。！？；\n":
            return index + 1
    return len(text.rstrip())


def _fake_source_coverage(source_text: str, start_text: str, generation_step: int) -> tuple[dict[str, Any], bool]:
    start_anchor = start_text.strip() or _fake_source_anchor(source_text, 0)
    start_offset = source_text.find(start_anchor)
    if start_offset < 0:
        start_offset = 0
        start_anchor = _fake_source_anchor(source_text, start_offset)

    source_end = len(source_text.rstrip())
    end_offset = source_end if generation_step >= 3 else _fake_sentence_end(source_text, start_offset)
    next_offset = end_offset
    while next_offset < source_end and source_text[next_offset].isspace():
        next_offset += 1
    is_complete = end_offset >= source_end or next_offset >= source_end
    next_start_text = None if is_complete else _fake_source_anchor(source_text, next_offset)
    end_text = source_text[max(start_offset, end_offset - 64):end_offset].strip()
    coverage = {
        "start_text": start_anchor,
        "end_text": end_text or start_anchor,
        "next_start_text": next_start_text,
        "note": f"fake provider covers source text from the supplied cursor at step {generation_step}.",
    }
    return coverage, is_complete


class FakeTextProvider:
    name = "fake"

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        del temperature
        metadata = metadata or {}
        node_name = metadata.get("node_name")

        episode_count = _extract_prompt_int(prompt, "目标集数", 1)
        episode_duration_seconds = _extract_prompt_int(prompt, "单集目标时长", 30)
        episode_keys = _episode_keys(episode_count)
        expected_keys = metadata.get("expected_keys")
        if isinstance(expected_keys, list) and expected_keys:
            episode_keys = [str(key) for key in expected_keys]
            episode_count = len(episode_keys)

        if schema is ScriptOutlineOutput or node_name == "script_outline":
            data = {
                "logline": "落魄青年在雨夜发现被调包的合同，决定当众反击。",
                "outline": "林舟被赵启陷害丢掉晋升机会，苏晚提醒他查看旧邮件。林舟逐步发现合同被调包的证据，并在会议上反击。",
                "episode_count": episode_count,
                "target_duration_seconds": episode_duration_seconds,
                "episode_outlines": {
                    key: f"第{index}集：林舟围绕合同调包事件推进调查与反击，冲突逐步升级。"
                    for index, key in enumerate(episode_keys, start=1)
                },
            }
        elif schema is ScriptNovelEpisodeOutput or node_name == "script_novel_episode":
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            target_char_count = int(metadata.get("target_char_count") or _extract_prompt_int(prompt, "当前集目标字数", 1800))
            data = {
                "episode_key": episode_key,
                "target_char_count": target_char_count,
                "novel_full": (
                    f"{episode_key}，雨夜办公室的灯只剩下一排。林舟摊开合同，"
                    "发现关键页纸张颜色比其他页浅了半分，装订孔也错开了一线。"
                    "苏晚把旧邮件截图推到他面前，附件时间像一枚钉子，把赵启的谎言钉在屏幕上。"
                    "林舟没有立刻说话，他把证据一页页拍下，听着窗外雨声，第一次决定不再退让。"
                ),
            }
        elif schema is ScriptNovelExtractBatchOutput or node_name == "script_novel_extract":
            batch_episode_keys = metadata.get("batch_episode_keys") or expected_keys or episode_keys
            batch_keys = [str(key) for key in batch_episode_keys]
            data = {
                "novel_extract": {
                    key: (
                        f"第{index}集，时间是雨夜到次日会议前后，地点在公司办公室和会议室。"
                        "林舟发现合同关键页被调包，苏晚递来旧邮件截图作为证据。"
                        "赵启持续施压，林舟保留证据并准备在会议上反击。"
                    )
                    for index, key in enumerate(batch_keys, start=1)
                }
            }
        elif schema is RoleExtractOutput or node_name in {
            "role_extract",
            "role_extract_primary",
            "role_extract_functional",
        }:
            if node_name == "role_extract_functional":
                data = {"roles": []}
            else:
                data = {
                    "roles": [
                        {
                            "name": "林舟",
                            "role_tier": "primary",
                            "aliases": ["男主"],
                            "importance": "lead",
                            "episode_keys": episode_keys,
                            "source_chapters": ["第1章-第2章"],
                            "brief": "二十八岁职场青年，合同调包事件中的被陷害者和反击者。",
                            "appearance_notes": ["青年男性", "短发", "身形偏瘦", "眼神疲惫但冷静"],
                            "has_dialogue": True,
                            "visual_reuse_required": True,
                        },
                        {
                            "name": "苏晚",
                            "role_tier": "primary",
                            "aliases": ["女主"],
                            "importance": "main",
                            "episode_keys": episode_keys,
                            "source_chapters": ["第1章-第2章"],
                            "brief": "二十六岁数据分析师，提供证据线索并帮助林舟理清时间线。",
                            "appearance_notes": ["青年女性", "气质清冷", "身形修长"],
                            "has_dialogue": True,
                            "visual_reuse_required": True,
                        },
                        {
                            "name": "赵启",
                            "role_tier": "primary",
                            "aliases": ["反派"],
                            "importance": "main",
                            "episode_keys": episode_keys,
                            "source_chapters": ["第1章-第2章"],
                            "brief": "三十五岁部门主管，操控会议节奏并压制林舟。",
                            "appearance_notes": ["成熟男性", "体型中等偏壮", "神情强势"],
                            "has_dialogue": True,
                            "visual_reuse_required": True,
                        },
                    ]
                }
        elif schema is RoleEpisodeKeyAuditReviewOutput or node_name == "role_episode_key_audit":
            role_name = str(metadata.get("role_name") or "角色")
            data = {
                "role_name": role_name,
                "missing_episode_keys": [],
                "missing_source_chapters": [],
                "evidence": "fake provider found no missing role episode keys.",
                "confidence": 1.0,
            }
        elif schema is RoleDuplicateAuditReviewOutput or node_name == "role_duplicate_audit":
            data = {"duplicate_groups": []}
        elif schema is AmbientEntityOutput or node_name == "ambient_entity_extract":
            data = {"entities": []}
        elif schema is RoleDesignOutput or node_name == "role_design":
            if "爱死机写实CG风" in prompt or "爱死机风格写实CG" in prompt or "CG动画电影风" in prompt or "cg_animation" in prompt:
                appearance_style = "爱死机写实CG风"
            elif "3D动漫" in prompt or "anime_3d" in prompt:
                appearance_style = "3D动漫"
            elif "2D动漫" in prompt or "anime_2d" in prompt:
                appearance_style = "2D动漫"
            else:
                appearance_style = "真人电影质感"
            view_requirement = (
                "正面、侧面、背面三视图角色设定图，character turnaround sheet，同一角色并排，统一身高比例和服装细节，干净背景，无其他人物"
                if "三视图" in prompt
                else "半身角色设定图，干净背景，无其他人物"
            )
            data = {
                "roles": [
                    {
                        "name": "林舟",
                        "intro": "二十八岁职场青年，长期被压制但观察细致，外表疲惫，关键时刻冷静锋利。",
                        "personality": "隐忍、敏锐、爆发力强",
                        "aliases": ["男主"],
                        "role_tier": "primary",
                        "has_dialogue": True,
                        "visual_reuse_required": True,
                        "importance": "lead",
                        "episode_keys": episode_keys,
                        "source_chapters": ["第1章-第2章"],
                        "relationships": [
                            {
                                "target_role_name": "苏晚",
                                "relation": "同事与协助者",
                                "dynamic": "林舟负责承受正面压力，苏晚用证据协助他反击。",
                                "evidence": "苏晚把旧邮件截图推到他面前。",
                            },
                            {
                                "target_role_name": "赵启",
                                "relation": "被压制者与主管对手",
                                "dynamic": "赵启持续施压，林舟寻找证据反击。",
                                "evidence": "赵启在合同调包事件中操控会议节奏。",
                            },
                        ],
                        "appearances": [
                            {
                                "role_name": "林舟",
                                "name": "base",
                                "desc": "二十八岁职场青年，身形偏瘦，短发，眼下有轻微疲惫感，五官清秀但神情克制。",
                                "prompt": (
                                    f"{appearance_style}，左侧为二十八岁中国职场青年男性形象，短发，身形偏瘦，"
                                    f"五官清秀，眼神疲惫但冷静，{view_requirement}；右侧为黑色录音笔和合同夹设计图，"
                                    "与左侧人物保持同一比例尺单独陈列，用左侧人物身高和大腿高度直接参照大小，不出现手部细节。"
                                ),
                                "role_bound_props": [
                                    {
                                        "name": "黑色录音笔",
                                        "desc": "林舟随身携带的证据记录工具，细长黑色金属外壳，约15厘米长。",
                                        "prompt": "黑色录音笔设计图，细长金属外壳，按钮和拾音孔清晰，与左侧人物保持同一比例尺单独陈列，靠近合同夹边缘显示真实大小，不出现手掌或手部持握姿态。",
                                        "status": "normal",
                                        "scale_relation": "与左侧人物同一比例尺，长度约为人物身高的十二分之一，可放入西装内袋",
                                        "usage": "林舟常放在合同旁或随身携带记录证据。",
                                    }
                                ],
                                "intro_video_prompt": "参考图片1中的林舟外观和黑色录音笔设计，林舟站在洁净、亮度适中的虚空圆台上；0-2 秒：圆台缓慢转动，他低头整理袖口，保持疲惫但冷静的神情；2-5 秒：他拿起黑色录音笔，展示右手握持方式，衣料和金属按键有轻微声响；5-8 秒：镜头轻微推近到稳定识别角度，他抬眼看向镜头，背景干净抽象，无其他人物、字幕或水印。",
                            }
                        ],
                        "voices": [
                            {
                                "role_name": "林舟",
                                "emotion": "normal",
                                "voice_name": "云舟 2.0",
                                "voice_type": "zh_male_m191_uranus_bigtts",
                                "voice_resource_id": "seed-tts-2.0",
                                "voice_selection_reason": "青年男性音色，表达克制清晰，适合职场男主的冷静和疲惫感。",
                                "desc": "二十八岁青年男声，低沉克制，略带疲惫感，语速中等，咬字清晰。",
                                "sample_text": "我是林舟，一个总在办公室熬到深夜的普通职员。我不擅长争辩，只习惯把每个细节记在心里。最近的风向不太对，但我相信只要冷静下来，总能找到问题的源头。",
                            },
                            {
                                "role_name": "林舟",
                                "emotion": "tense",
                                "voice_name": "云舟 2.0",
                                "voice_type": "zh_male_m191_uranus_bigtts",
                                "voice_resource_id": "seed-tts-2.0",
                                "voice_selection_reason": "青年男性音色，表达克制清晰，适合职场男主的冷静和疲惫感。",
                                "desc": "同一青年男声，压低音量，呼吸略紧，语尾收住，表现强忍怒意。",
                                "sample_text": "我是林舟，一个被压力推到角落的职员。我知道现在每句话都可能被误解，所以只能把情绪压住。越是混乱的时候，我越要盯紧那些不该被忽略的细节。",
                            },
                        ],
                    },
                    {
                        "name": "苏晚",
                        "intro": "二十六岁数据分析师，理性克制，善于发现证据，是男主反击的关键助力。",
                        "personality": "冷静、聪明、行动果断",
                        "aliases": ["女主"],
                        "role_tier": "primary",
                        "has_dialogue": True,
                        "visual_reuse_required": True,
                        "importance": "main",
                        "episode_keys": episode_keys,
                        "source_chapters": ["第1章-第2章"],
                        "relationships": [
                            {
                                "target_role_name": "林舟",
                                "relation": "同事与协助者",
                                "dynamic": "她用数据和邮件证据帮助林舟稳住局面。",
                                "evidence": "苏晚递出旧邮件截图。",
                            }
                        ],
                        "appearances": [
                            {
                                "role_name": "苏晚",
                                "name": "base",
                                "desc": "二十六岁数据分析师，身形修长，眉眼清冷，气质理性克制。",
                                "prompt": (
                                    f"{appearance_style}，左侧为二十六岁中国女性数据分析师形象，身形修长，"
                                    f"眉眼清冷，气质理性克制，{view_requirement}；右侧为细框眼镜和数据平板设计图，"
                                    "与左侧人物保持同一比例尺单独陈列，用左侧人物肩宽和前臂长度直接参照大小，不出现手部细节。"
                                ),
                                "role_bound_props": [],
                                "intro_video_prompt": "参考图片1中的苏晚外观和数据平板设计，苏晚站在洁净、亮度适中的虚空圆台上；0-2 秒：圆台缓慢转动，她推正细框眼镜，神情清冷克制；2-5 秒：她单手划过数据平板，展示持握和操作方式，动作准确利落；5-8 秒：镜头轻微推近到稳定识别角度，背景干净抽象，无其他人物、字幕或水印。",
                            }
                        ],
                        "voices": [
                            {
                                "role_name": "苏晚",
                                "emotion": "normal",
                                "voice_name": "小何 2.0",
                                "voice_type": "zh_female_xiaohe_uranus_bigtts",
                                "voice_resource_id": "seed-tts-2.0",
                                "voice_selection_reason": "女性音色干净稳定，带情绪变化能力，适合理性克制的数据分析师。",
                                "desc": "二十六岁女性声音，清冷理性，音色干净，语速稳定。",
                                "sample_text": "我是苏晚，负责数据分析，也习惯用证据说话。很多人只看结果，我更在意过程里那些微小的偏差。只要线索还在，我就不会轻易下结论。",
                            }
                        ],
                    },
                    {
                        "name": "赵启",
                        "intro": "三十五岁部门主管，精致强势，擅长操控会议节奏，害怕证据曝光。",
                        "personality": "自负、控制欲强、心虚时急躁",
                        "aliases": ["反派"],
                        "role_tier": "primary",
                        "has_dialogue": True,
                        "visual_reuse_required": True,
                        "importance": "main",
                        "episode_keys": episode_keys,
                        "source_chapters": ["第1章-第2章"],
                        "relationships": [
                            {
                                "target_role_name": "林舟",
                                "relation": "主管与对手",
                                "dynamic": "赵启用职位和会议压力压制林舟。",
                                "evidence": "赵启持续施压并操控会议节奏。",
                            }
                        ],
                        "appearances": [
                            {
                                "role_name": "赵启",
                                "name": "base",
                                "desc": "三十五岁部门主管，体型中等偏壮，五官锐利，神情自负，压迫感强。",
                                "prompt": (
                                    f"{appearance_style}，左侧为三十五岁中国男性部门主管形象，体型中等偏壮，"
                                    f"五官锐利，神情自负，{view_requirement}；右侧为深色文件夹和钢笔设计图，"
                                    "与左侧人物保持同一比例尺单独陈列，用左侧人物身高和西装口袋位置直接参照大小，不出现手部细节。"
                                ),
                                "role_bound_props": [],
                                "intro_video_prompt": "参考图片1中的赵启外观和深色文件夹设计，赵启站在洁净、亮度适中的虚空圆台上；0-2 秒：圆台缓慢转动，他整理西装下摆，维持强势站姿；2-5 秒：他夹起深色文件夹，展示文件夹与手臂的比例和使用方式；5-8 秒：短暂停顿后他抬眼露出压迫感强的目光，背景干净抽象，无其他人物、字幕或水印。",
                            }
                        ],
                        "voices": [
                            {
                                "role_name": "赵启",
                                "emotion": "normal",
                                "voice_name": "霸气青叔 2.0",
                                "voice_type": "zh_male_baqiqingshu_uranus_bigtts",
                                "voice_resource_id": "seed-tts-2.0",
                                "voice_selection_reason": "成熟男性音色有压迫感，适合主管角色的强势和控制欲。",
                                "desc": "三十五岁男性声音，成熟强势，语气带压迫感，习惯短暂停顿后下判断。",
                                "sample_text": "我是赵启，这个部门的负责人。会议室里的节奏必须由我来掌控，任何失误都要有人承担。一个团队想往上走，就不能让犹豫和软弱拖慢脚步。",
                            },
                            {
                                "role_name": "赵启",
                                "emotion": "tense",
                                "voice_name": "霸气青叔 2.0",
                                "voice_type": "zh_male_baqiqingshu_uranus_bigtts",
                                "voice_resource_id": "seed-tts-2.0",
                                "voice_selection_reason": "成熟男性音色有压迫感，适合主管角色的强势和控制欲。",
                                "desc": "同一成熟男声，音量变虚，语速变快，带掩饰慌张的强硬。",
                                "sample_text": "我是赵启，我必须让所有事情看起来仍在掌控之中。越有人追问，我越不能露出破绽。只要会议还没结束，局面就还有被我拉回来的机会。",
                            },
                        ],
                    },
                ]
            }
            role_name = str(metadata.get("role_name") or "").strip()
            if role_name:
                selected_roles = [role for role in data["roles"] if role.get("name") == role_name]
                data["roles"] = selected_roles or data["roles"][:1]
        elif schema is RoleAppearanceDesignOutput or node_name == "role_appearance_design":
            if "爱死机写实CG风" in prompt or "爱死机风格写实CG" in prompt or "CG动画电影风" in prompt or "cg_animation" in prompt:
                appearance_style = "爱死机写实CG风"
            elif "3D动漫" in prompt or "anime_3d" in prompt:
                appearance_style = "3D动漫"
            elif "2D动漫" in prompt or "anime_2d" in prompt:
                appearance_style = "2D动漫"
            else:
                appearance_style = "真人电影质感"
            view_requirement = (
                "正面、侧面、背面三视图角色设定图，character turnaround sheet，同一角色并排，统一身高比例和服装细节，干净背景，无其他人物"
                if "三视图" in prompt
                else "半身角色设定图，干净背景，无其他人物"
            )
            data = {
                "appearances": [
                    {
                        "role_name": "林舟",
                        "name": "base",
                        "desc": "二十八岁职场青年，身形偏瘦，短发，眼下有轻微疲惫感，五官清秀但神情克制。",
                        "prompt": (
                            f"{appearance_style}，左侧为二十八岁中国职场青年男性形象，短发，身形偏瘦，"
                            f"五官清秀，眼神疲惫但冷静，{view_requirement}；右侧为黑色录音笔和合同夹设计图，"
                            "与左侧人物保持同一比例尺单独陈列，用左侧人物身高和大腿高度直接参照大小，不出现手部细节。"
                        ),
                        "role_bound_props": [
                            {
                                "name": "黑色录音笔",
                                "desc": "林舟随身携带的证据记录工具，细长黑色金属外壳，约15厘米长。",
                                "prompt": "黑色录音笔设计图，细长金属外壳，按钮和拾音孔清晰，与左侧人物保持同一比例尺单独陈列，靠近合同夹边缘显示真实大小，不出现手掌或手部持握姿态。",
                                "status": "normal",
                                "scale_relation": "与左侧人物同一比例尺，长度约为人物身高的十二分之一，可放入西装内袋",
                                "usage": "林舟常放在合同旁或随身携带记录证据。",
                            }
                        ],
                        "intro_video_prompt": "参考图片1中的林舟外观和黑色录音笔设计，林舟站在洁净、亮度适中的虚空圆台上；0-2 秒：圆台缓慢转动，他低头整理袖口，保持疲惫但冷静的神情；2-5 秒：他拿起黑色录音笔，展示右手握持方式，衣料和金属按键有轻微声响；5-8 秒：镜头轻微推近到稳定识别角度，他抬眼看向镜头，背景干净抽象，无其他人物、字幕或水印。",
                    },
                    {
                        "role_name": "苏晚",
                        "name": "base",
                        "desc": "二十六岁数据分析师，身形修长，眉眼清冷，气质理性克制。",
                        "prompt": (
                            f"{appearance_style}，左侧为二十六岁中国女性数据分析师形象，身形修长，"
                            f"眉眼清冷，气质理性克制，{view_requirement}；右侧为细框眼镜和数据平板设计图，"
                            "与左侧人物保持同一比例尺单独陈列，用左侧人物肩宽和前臂长度直接参照大小，不出现手部细节。"
                        ),
                        "role_bound_props": [],
                        "intro_video_prompt": "参考图片1中的苏晚外观和数据平板设计，苏晚站在洁净、亮度适中的虚空圆台上；0-2 秒：圆台缓慢转动，她推正细框眼镜，神情清冷克制；2-5 秒：她单手划过数据平板，展示持握和操作方式，动作准确利落；5-8 秒：镜头轻微推近到稳定识别角度，背景干净抽象，无其他人物、字幕或水印。",
                    },
                    {
                        "role_name": "赵启",
                        "name": "base",
                        "desc": "三十五岁部门主管，体型中等偏壮，五官锐利，神情自负，压迫感强。",
                        "prompt": (
                            f"{appearance_style}，左侧为三十五岁中国男性部门主管形象，体型中等偏壮，"
                            f"五官锐利，神情自负，{view_requirement}；右侧为深色文件夹和钢笔设计图，"
                            "与左侧人物保持同一比例尺单独陈列，用左侧人物身高和西装口袋位置直接参照大小，不出现手部细节。"
                        ),
                        "role_bound_props": [],
                        "intro_video_prompt": "参考图片1中的赵启外观和深色文件夹设计，赵启站在洁净、亮度适中的虚空圆台上；0-2 秒：圆台缓慢转动，他整理西装下摆，维持强势站姿；2-5 秒：他夹起深色文件夹，展示文件夹与手臂的比例和使用方式；5-8 秒：短暂停顿后他抬眼露出压迫感强的目光，背景干净抽象，无其他人物、字幕或水印。",
                    },
                ]
            }
        elif schema is VoiceSelectShortlistOutput or node_name == "voice_select_shortlist":
            candidates = metadata.get("heuristic_candidates")
            if not isinstance(candidates, list) or not candidates:
                candidates = metadata.get("voice_profiles")
            if not isinstance(candidates, list):
                candidates = []
            data = {
                "candidates": [
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "voice_label": str(candidate.get("voice_label") or candidate.get("voice_type") or "Fake Voice"),
                        "voice_type": str(candidate.get("voice_type") or ""),
                        "score": (
                            candidate.get("score")
                            or candidate.get("local_score")
                            or candidate.get("heuristic_score")
                            or 8.0
                        ),
                        "reason": str(
                            candidate.get("reason")
                            or candidate.get("local_reason")
                            or candidate.get("heuristic_reason")
                            or "fake text shortlist selected this candidate."
                        ),
                    }
                    for candidate in candidates[: int(metadata.get("limit") or 5)]
                    if isinstance(candidate, dict) and candidate.get("voice_type")
                ]
            }
        elif schema is RoleVoiceDesignOutput or node_name == "role_voice_design":
            data = {
                "role_voices": [
                    {
                        "role_name": "林舟",
                        "emotion": "normal",
                        "voice_name": "云舟 2.0",
                        "voice_type": "zh_male_m191_uranus_bigtts",
                        "voice_resource_id": "seed-tts-2.0",
                        "voice_selection_reason": "青年男性音色，表达克制清晰，适合职场男主的冷静和疲惫感。",
                        "desc": "二十八岁青年男声，低沉克制，略带疲惫感，语速中等，咬字清晰。",
                        "sample_text": "我是林舟，一个总在办公室熬到深夜的普通职员。我不擅长争辩，只习惯把每个细节记在心里。最近的风向不太对，但我相信只要冷静下来，总能找到问题的源头。",
                    },
                    {
                        "role_name": "林舟",
                        "emotion": "tense",
                        "voice_name": "云舟 2.0",
                        "voice_type": "zh_male_m191_uranus_bigtts",
                        "voice_resource_id": "seed-tts-2.0",
                        "voice_selection_reason": "青年男性音色，表达克制清晰，适合职场男主的冷静和疲惫感。",
                        "desc": "同一青年男声，压低音量，呼吸略紧，语尾收住，表现强忍怒意。",
                        "sample_text": "我是林舟，一个被压力推到角落的职员。我知道现在每句话都可能被误解，所以只能把情绪压住。越是混乱的时候，我越要盯紧那些不该被忽略的细节。",
                    },
                    {
                        "role_name": "苏晚",
                        "emotion": "normal",
                        "voice_name": "小何 2.0",
                        "voice_type": "zh_female_xiaohe_uranus_bigtts",
                        "voice_resource_id": "seed-tts-2.0",
                        "voice_selection_reason": "女性音色干净稳定，带情绪变化能力，适合理性克制的数据分析师。",
                        "desc": "二十六岁女性声音，清冷理性，音色干净，语速稳定。",
                        "sample_text": "我是苏晚，负责数据分析，也习惯用证据说话。很多人只看结果，我更在意过程里那些微小的偏差。只要线索还在，我就不会轻易下结论。",
                    },
                    {
                        "role_name": "赵启",
                        "emotion": "normal",
                        "voice_name": "霸气青叔 2.0",
                        "voice_type": "zh_male_baqiqingshu_uranus_bigtts",
                        "voice_resource_id": "seed-tts-2.0",
                        "voice_selection_reason": "成熟男性音色有压迫感，适合主管角色的强势和控制欲。",
                        "desc": "三十五岁男性声音，成熟强势，语气带压迫感，习惯短暂停顿后下判断。",
                        "sample_text": "我是赵启，这个部门的负责人。会议室里的节奏必须由我来掌控，任何失误都要有人承担。一个团队想往上走，就不能让犹豫和软弱拖慢脚步。",
                    },
                    {
                        "role_name": "赵启",
                        "emotion": "tense",
                        "voice_name": "霸气青叔 2.0",
                        "voice_type": "zh_male_baqiqingshu_uranus_bigtts",
                        "voice_resource_id": "seed-tts-2.0",
                        "voice_selection_reason": "成熟男性音色有压迫感，适合主管角色的强势和控制欲。",
                        "desc": "同一成熟男声，音量变虚，语速变快，带掩饰慌张的强硬。",
                        "sample_text": "我是赵启，我必须让所有事情看起来仍在掌控之中。越有人追问，我越不能露出破绽。只要会议还没结束，局面就还有被我拉回来的机会。",
                    },
                ]
            }
        elif schema is PropExtractOutput or node_name == "prop_extract":
            data = {
                "props": [
                    {
                        "name": "被调包的合同",
                        "status": "normal",
                        "episode_keys": episode_keys,
                        "source_chapters": ["第1章-第2章"],
                        "brief": "林舟发现合同关键页异常的核心证据道具。",
                        "appearance_notes": ["A4商务合同", "关键页纸张颜色略浅", "页码和边缘纹理不一致"],
                    },
                    {
                        "name": "邮件截图",
                        "status": "normal",
                        "episode_keys": episode_keys,
                        "source_chapters": ["第1章-第2章"],
                        "brief": "苏晚提供的旧邮件附件时间线证据。",
                        "appearance_notes": ["电脑或手机屏幕截图", "附件时间线", "冷蓝屏幕光"],
                    },
                ]
            }
        elif schema is PropDesignOutput or node_name == "prop_design":
            prop_name = str(metadata.get("prop_name") or "").strip()
            prop_episode_keys = [str(key) for key in (metadata.get("episode_keys") or episode_keys)]
            data = {
                "props": [
                    {
                        "name": "被调包的合同",
                        "desc": "一份装订整齐的商务合同，关键页纸张颜色略浅，页码和边缘纹理与其他页不一致。",
                        "prompt": "真人电影质感，商务合同特写，装订整齐，关键页纸张颜色略浅，页码和纸张边缘细节清晰，办公室桌面，自然冷色光。",
                        "status": "normal",
                        "episode_keys": prop_episode_keys,
                    },
                    {
                        "name": "邮件截图",
                        "desc": "手机或电脑上的旧邮件截图，能看到时间线和附件记录，是反击证据。",
                        "prompt": "真人电影质感，电脑屏幕上的邮件截图特写，时间线和附件记录清晰但不过度曝光，办公室环境反光自然。",
                        "status": "normal",
                        "episode_keys": prop_episode_keys,
                    },
                ]
            }
            if prop_name:
                selected_props = [prop for prop in data["props"] if prop.get("name") == prop_name]
                data["props"] = selected_props or data["props"][:1]
        elif schema is LayoutExtractOutput or node_name == "layout_extract":
            data = {
                "layouts": [
                    {
                        "name": "雨夜办公室",
                        "episode_keys": episode_keys,
                        "source_chapters": ["第1章-第2章"],
                        "brief": "林舟发现合同异常并与苏晚核对证据的悬疑调查空间。",
                        "appearance_notes": ["深夜办公区", "窗外雨光", "冷白灯", "办公桌与电脑"],
                    },
                    {
                        "name": "会议室",
                        "episode_keys": episode_keys,
                        "source_chapters": ["第1章-第2章"],
                        "brief": "林舟公开投屏证据并反击赵启的对峙空间。",
                        "appearance_notes": ["玻璃会议室", "长桌", "投影屏", "冷色顶灯"],
                    },
                ]
            }
        elif schema is LayoutDesignOutput or node_name == "layout_design":
            data = {
                "layouts": [
                    {
                        "name": "雨夜办公室",
                        "desc": "深夜办公区，冷白灯和窗外雨光交织，桌面散落合同和电脑，适合悬疑调查氛围。",
                        "prompt": "真人电影质感，深夜现代办公室，窗外雨夜，冷白灯，桌面散落合同和打开的电脑，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                    {
                        "name": "会议室",
                        "desc": "玻璃会议室，长桌、投影屏和冷色顶灯，适合公开对峙和证据投屏。",
                        "prompt": "真人电影质感，现代公司玻璃会议室，长桌，投影屏，冷色顶灯，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                ]
            }
        elif schema is LayoutDedupeReviewOutput or node_name == "layout_dedupe_review":
            data = {
                "layouts": [
                    {
                        "name": "雨夜办公室",
                        "desc": "深夜办公区，冷白灯和窗外雨光交织，桌面散落合同和电脑，适合悬疑调查氛围。",
                        "prompt": "真人电影质感，深夜现代办公室，窗外雨夜，冷白灯，桌面散落合同和打开的电脑，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                    {
                        "name": "会议室",
                        "desc": "玻璃会议室，长桌、投影屏和冷色顶灯，适合公开对峙和证据投屏。",
                        "prompt": "真人电影质感，现代公司玻璃会议室，长桌，投影屏，冷色顶灯，空间真实，电影镜头，空场景，无人物。",
                        "episode_keys": episode_keys,
                    },
                ],
                "merge_notes": ["未发现需要合并的重复场景。"],
            }
        elif schema is BGMDesignOutput or node_name == "bgm_design":
            bgm_count = int(metadata.get("bgm_count") or _extract_prompt_int(prompt, "BGM数量", 3))
            bgm_templates = [
                {
                    "name": "暗线推进",
                    "mood": "紧张、克制、悬疑",
                    "prompt": "紧张克制的悬疑影视配乐，低频脉冲、轻微电子氛围、节奏逐步推进，适合办公室调查和证据发现。",
                    "usage_hint": "调查、发现线索、反击前铺垫。",
                },
                {
                    "name": "公开反击",
                    "mood": "压迫、爆发、胜负揭晓",
                    "prompt": "短剧高潮反击配乐，弦乐和电子鼓逐步增强，节奏果断，适合会议室投屏证据和反派失控。",
                    "usage_hint": "会议对峙和反击高潮。",
                },
                {
                    "name": "低谷独白",
                    "mood": "低落、克制、内心挣扎",
                    "prompt": "克制的情绪低谷影视配乐，柔和钢琴、稀疏弦乐和低频氛围，适合人物独白和信念动摇。",
                    "usage_hint": "角色独处、失落、犹豫。",
                },
                {
                    "name": "真相逼近",
                    "mood": "紧迫、疑云、逐步揭露",
                    "prompt": "紧迫的调查推进配乐，重复钢琴音型、轻电子节拍和悬疑弦乐，适合证据逐步串联。",
                    "usage_hint": "线索拼接、真相揭露前。",
                },
                {
                    "name": "余温收束",
                    "mood": "释然、温暖、收束",
                    "prompt": "温暖克制的结尾配乐，柔和钢琴与轻弦乐，节奏舒缓，适合冲突结束后的情绪回落。",
                    "usage_hint": "结尾、关系缓和、情绪收束。",
                },
            ]
            bgms = [
                bgm_templates[index] if index < len(bgm_templates) else {
                    "name": f"情绪铺底{index + 1}",
                    "mood": "补充情绪、氛围铺垫",
                    "prompt": "可复用的短剧氛围配乐，中速节奏、轻电子和弦乐铺底，适合补充转场和情绪延续。",
                    "usage_hint": "转场、补充铺垫。",
                }
                for index in range(bgm_count)
            ]
            data = {
                "bgms": bgms
            }
        elif schema is ShotBGMSoundDesignOutput or node_name == "shot_bgm_generation":
            duration = metadata.get("duration_seconds") or 6
            data = {
                "sound_description": (
                    "Instrumental cinematic background music and sound design, no vocals, no lyrics, no dialogue. "
                    f"[0.0-{float(duration) / 2:.1f}s] Sparse low drone, cold office air hum, distant rain texture, "
                    "subtle paper rustle as rhythmic detail [No cut]. "
                    f"[{float(duration) / 2:.1f}-{float(duration):.1f}s] Tension rises with muted pulses, glassy high "
                    "tones, restrained impact, and a short reverb tail for the next cut [Hard cut]. "
                    "Final mix: keep dialogue range clear and end with a controlled cinematic tail."
                )
            }
        elif schema in {StoryboardNextShotOutput, StoryboardShotGenerationOutput}:
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            try:
                generation_step = int(metadata.get("generation_step") or metadata.get("shot_index") or 1)
            except (TypeError, ValueError):
                generation_step = 1
            shot_templates = [
                {
                    "layout_id": "layout_雨夜办公室",
                    "title": "发现异常合同",
                    "duration_seconds": 6,
                    "transition": "结尾停在可硬切状态",
                    "start_frame_source": "new_reference_frame",
                    "start_frame_inheritance_reason": "本片段重新建立雨夜办公室调查空间，不继承前序片段尾帧。",
                    "dialogue": [],
                    "role_ids": ["role_林舟"],
                    "role_appearance_ids": ["role_林舟_appearance_base"],
                    "role_audio_ids": [],
                    "prop_ids": ["prop_被调包的合同"],
                    "anchor_frame_prompt": (
                        "真人电影质感，雨夜现代办公室，16:9 横屏近景，50mm 镜头从办公桌斜侧拍向林舟。"
                        "林舟穿深灰衬衫坐在桌前，肩背微弯，右手停在被调包的合同关键页边缘，目光落在纸张色差处。"
                        "桌面前景有合同装订线、浅色关键页、黑色签字笔和半杯冷咖啡，背景电脑屏幕发出冷蓝光，"
                        "窗外雨痕和城市霓虹反射在玻璃上。冷白顶灯压低环境，屏幕光打亮林舟疲惫侧脸，纸张纹理清楚；"
                        "单帧剧照，无字幕、水印、文字标识和无关人物。"
                    ),
                    "video_prompt": (
                        "当前片段从桌面斜侧 50mm 近景开始；0-3 秒：相机缓慢推近合同关键页，"
                        "让浅色纸张、错位页码和装订孔依次进入焦点；窗外雨痕持续下滑，电脑冷蓝光在桌面反射轻微闪动，"
                        "空办公室低频电流声和雨声保持压低，全程不切镜；3-6 秒：林舟的右手指尖沿纸边停住，呼吸变轻，"
                        "视线从合同页码移到电脑屏幕邮件附件时间，再回到纸面；冷掉的咖啡表面几乎不动，片段节奏从深夜疲惫转为警觉确认，"
                        "最后停在林舟抬眼看向屏幕的半侧脸和合同色差同框位置，可硬切到下一片段。"
                    ),
                },
                {
                    "layout_id": "layout_雨夜办公室",
                    "title": "邮件截图确认",
                    "duration_seconds": 6,
                    "transition": "硬切到证据近景",
                    "start_frame_source": "new_reference_frame",
                    "start_frame_inheritance_reason": "本片段转入电脑屏幕与证据细节，需要重新建立近景画面。",
                    "dialogue": [],
                    "role_ids": ["role_林舟", "role_苏晚"],
                    "role_appearance_ids": ["role_林舟_appearance_base", "role_苏晚_appearance_base"],
                    "role_audio_ids": [],
                    "prop_ids": ["prop_邮件截图", "prop_被调包的合同"],
                    "anchor_frame_prompt": (
                        "真人电影质感，雨夜办公室电脑屏幕近景，16:9 横屏，70mm 镜头压缩空间。"
                        "屏幕上旧邮件截图以冷蓝光显示，附件记录和时间线处于画面中心但不过曝，林舟的手停在键盘旁，"
                        "苏晚的手从画面右侧递来一份打印合同。桌面有散乱纸张、浅色合同关键页和低反光金属笔，"
                        "背景办公区虚化成冷白灯点与雨夜玻璃反射；单帧剧照，无字幕、水印、文字标识和无关人物。"
                    ),
                    "video_prompt": (
                        "当前片段以 70mm 近景锁在电脑屏幕与桌面证据之间；0-3 秒：镜头极慢推近邮件附件时间线，"
                        "电脑屏幕亮度轻微脉动，雨水在远处玻璃上形成竖向光痕，空办公室低频电流声持续铺底，全程不切镜；"
                        "3-6 秒：镜头轻微向右平移，让苏晚递来的打印合同进入前景；林舟的手没有立刻接过，"
                        "指尖在键盘旁停顿半秒，随后用拇指压住合同边角，眼神从屏幕冷光中抬起，纸张摩擦声贴近，"
                        "结尾停在邮件截图时间线与浅色合同页同框的位置，可硬切到下一片段。"
                    ),
                },
                {
                    "layout_id": "layout_会议室",
                    "title": "会议室反击",
                    "duration_seconds": 8,
                    "transition": "硬切到公开对峙",
                    "start_frame_source": "new_reference_frame",
                    "start_frame_inheritance_reason": "本片段从办公室调查跳到会议室反击，需要重新建立空间和人物站位。",
                    "dialogue": ["林舟：这份合同被换过，时间线就在这里。"],
                    "role_ids": ["role_林舟", "role_赵启"],
                    "role_appearance_ids": ["role_林舟_appearance_base", "role_赵启_appearance_base"],
                    "role_audio_ids": ["role_林舟_audio_normal"],
                    "prop_ids": ["prop_邮件截图"],
                    "anchor_frame_prompt": (
                        "真人电影质感，雨夜玻璃会议室，16:9 横屏中景，35mm 镜头从会议桌短边朝投影屏拍摄。"
                        "林舟站在投影屏左侧三分之一处，深灰衬衫袖口微皱，右手停在触控板旁，"
                        "赵启穿深色西装坐在长桌右侧阴影里，身体前倾但笑容僵住。投影屏上是邮件时间线和附件记录的冷白光块，"
                        "桌面前景有被调包的合同、会议水杯和几只沉默的手，落地玻璃上有雨痕和霓虹反射。"
                        "投影冷光打亮林舟侧脸，窗外蓝色雨光勾出赵启轮廓；单帧剧照，无字幕、水印、文字标识和无关人物。"
                    ),
                    "video_prompt": (
                        "当前片段中相机位于会议桌短边，35mm 中广角，贴着桌面低位观察；0-3 秒：镜头横移扫过被调包的合同、"
                        "会议水杯和几只停住的手，投影冷光在纸面轻微频闪，会议室空调低频和投影电流声压住环境，全程不切镜；"
                        "3-6.5 秒：林舟站在投影屏左侧，说出“这份合同被换过，时间线就在这里”时没有夸张手势，"
                        "只把邮件时间线拖到最大，目光压向赵启；对白声线克制靠前，房间混响短促，形成 J-Cut 式声音衔接；"
                        "6.5-8 秒：硬切到赵启坐在右侧阴影里的近景，他的身体从前倾慢慢后撤，喉结轻动，手指扣紧椅子扶手，"
                        "原本维持的笑意逐渐僵住，最后相机缓慢抬升停在赵启僵硬的眼神上，可硬切到下一片段。"
                    ),
                },
            ]
            shot = dict(shot_templates[min(max(generation_step, 1), len(shot_templates)) - 1])
            source_text = str(metadata.get("current_novel_full") or metadata.get("current_shot_start_text") or "").strip()
            coverage, is_complete = _fake_source_coverage(
                source_text,
                str(metadata.get("current_shot_start_text") or ""),
                generation_step,
            )
            shot["source_coverage"] = coverage
            if schema is StoryboardNextShotOutput:
                shot.pop("start_frame_source", None)
                shot.pop("start_frame_inheritance_reason", None)
                data = {
                    "episode_key": episode_key,
                    "shot": shot,
                    "is_chapter_complete": is_complete,
                    "completion_reason": "fake provider advanced the current novel source cursor.",
                }
            else:
                shot["shot_id"] = f"{episode_key}_shot_{generation_step:03d}"
                shot["index"] = generation_step
                shot["ref_frame_prompt"] = shot.pop("anchor_frame_prompt")
                data = {
                    "episode_key": episode_key,
                    "shot": shot,
                    "is_episode_complete": is_complete,
                    "completion_reason": "fake provider generated legacy storyboard shot output.",
                }
        elif schema is StoryboardEpisodeOutput or node_name == "storyboard_generation":
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            data = {
                "episode_key": episode_key,
                "shots": [
                    {
                        "shot_id": f"{episode_key}_shot_001",
                        "index": 1,
                        "layout_id": "layout_雨夜办公室",
                        "title": "发现异常合同",
                        "duration_seconds": 6,
                        "transition": "结尾停在可硬切状态",
                        "start_frame_source": "new_reference_frame",
                        "start_frame_inheritance_reason": "本片段重新建立雨夜办公室调查空间，不继承前序片段尾帧。",
                        "dialogue": [],
                        "role_ids": ["role_林舟"],
                        "role_appearance_ids": ["role_林舟_appearance_base"],
                        "role_audio_ids": [],
                        "prop_ids": ["prop_被调包的合同"],
                        "ref_frame_prompt": (
                            "真人电影质感，雨夜现代办公室，16:9 横屏近景，50mm 镜头从办公桌斜侧拍向林舟。"
                            "林舟穿深灰衬衫坐在桌前，肩背微弯，右手停在被调包的合同关键页边缘，目光落在纸张色差处。"
                            "桌面前景有合同装订线、浅色关键页、黑色签字笔和半杯冷咖啡，背景电脑屏幕发出冷蓝光，"
                            "窗外雨痕和城市霓虹反射在玻璃上。冷白顶灯压低环境，屏幕光打亮林舟疲惫侧脸，纸张纹理清楚；"
                            "单帧剧照，无字幕、水印、文字标识和无关人物。"
                        ),
                        "video_prompt": (
                            "当前片段从桌面斜侧 50mm 近景开始；0-3 秒：相机缓慢推近合同关键页，"
                            "让浅色纸张、错位页码和装订孔依次进入焦点；窗外雨痕持续下滑，电脑冷蓝光在桌面反射轻微闪动，"
                            "空办公室低频电流声和雨声保持压低，全程不切镜；3-6 秒：林舟的右手指尖沿纸边停住，呼吸变轻，"
                            "视线从合同页码移到电脑屏幕邮件附件时间，再回到纸面；冷掉的咖啡表面几乎不动，片段节奏从深夜疲惫转为警觉确认，"
                            "最后停在林舟抬眼看向屏幕的半侧脸和合同色差同框位置，可硬切到下一片段。"
                        ),
                    },
                    {
                        "shot_id": f"{episode_key}_shot_002",
                        "index": 2,
                        "layout_id": "layout_会议室",
                        "title": "会议室反击",
                        "duration_seconds": 8,
                        "transition": "硬切到公开对峙",
                        "start_frame_source": "new_reference_frame",
                        "start_frame_inheritance_reason": "本片段从办公室调查跳到会议室反击，需要重新建立空间和人物站位。",
                        "dialogue": ["林舟：这份合同被换过，时间线就在这里。"],
                        "role_ids": ["role_林舟", "role_赵启"],
                        "role_appearance_ids": ["role_林舟_appearance_base", "role_赵启_appearance_base"],
                        "role_audio_ids": ["role_林舟_audio_normal"],
                        "prop_ids": ["prop_邮件截图"],
                        "ref_frame_prompt": (
                            "真人电影质感，雨夜玻璃会议室，16:9 横屏中景，35mm 镜头从会议桌短边朝投影屏拍摄。"
                            "林舟站在投影屏左侧三分之一处，深灰衬衫袖口微皱，右手停在触控板旁，"
                            "赵启穿深色西装坐在长桌右侧阴影里，身体前倾但笑容僵住。投影屏上是邮件时间线和附件记录的冷白光块，"
                            "桌面前景有被调包的合同、会议水杯和几只沉默的手，落地玻璃上有雨痕和霓虹反射。"
                            "投影冷光打亮林舟侧脸，窗外蓝色雨光勾出赵启轮廓；单帧剧照，无字幕、水印、文字标识和无关人物。"
                        ),
                        "video_prompt": (
                            "当前片段中相机位于会议桌短边，35mm 中广角，贴着桌面低位观察；0-3 秒：镜头横移扫过被调包的合同、"
                            "会议水杯和几只停住的手，投影冷光在纸面轻微频闪，会议室空调低频和投影电流声压住环境，全程不切镜；"
                            "3-6.5 秒：林舟站在投影屏左侧，说出“这份合同被换过，时间线就在这里”时没有夸张手势，"
                            "只把邮件时间线拖到最大，目光压向赵启；对白声线克制靠前，房间混响短促，形成 J-Cut 式声音衔接；"
                            "6.5-8 秒：硬切到赵启坐在右侧阴影里的近景，他的身体从前倾慢慢后撤，喉结轻动，手指扣紧椅子扶手，"
                            "原本维持的笑意逐渐僵住，最后相机缓慢抬升停在赵启僵硬的眼神上，可硬切到下一片段。"
                        ),
                    },
                ],
            }
        else:
            raise ValueError(f"Fake provider has no fixture for schema {schema.__name__}")

        return schema.model_validate(data)


class FakeImageProvider:
    name = "fake"
    model = "fake-image"
    supports_reference_images = True

    async def generate_image(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        size: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> ImageGenerationResult:
        del refs, size
        metadata = metadata or {}
        image_bytes = f"fake image: {metadata.get('asset_id', 'asset')}: {prompt}".encode("utf-8")
        return ImageGenerationResult(
            provider=self.name,
            model=self.model,
            image_data=[base64.b64encode(image_bytes).decode("ascii")],
            request_id=f"fake-image-request-{metadata.get('asset_id', 'asset')}",
            usage={"image_count": 1},
            raw_response={
                "output": {"image": "<base64 image omitted>"},
                "request_id": f"fake-image-request-{metadata.get('asset_id', 'asset')}",
            },
        )


class FakeMusicProvider:
    name = "fake"
    model = "fake-music"

    async def generate_music(
        self,
        prompt: str,
        *,
        lyrics: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> MusicGenerationResult:
        metadata = metadata or {}
        audio_bytes = f"fake music: {metadata.get('asset_id', 'bgm')}: {prompt}".encode("utf-8")
        return MusicGenerationResult(
            provider=self.name,
            model=self.model,
            audio_id=f"fake_music_{metadata.get('asset_id', 'bgm')}",
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_format=str(metadata.get("format", "mp3")),
            duration_seconds=30,
            lyrics=lyrics,
            request_id=f"fake-music-request-{metadata.get('asset_id', 'bgm')}",
            usage={"duration": 30},
            raw_response={
                "output": {"audio": {"data": "<base64 audio omitted>"}},
                "request_id": f"fake-music-request-{metadata.get('asset_id', 'bgm')}",
            },
        )


class FakeVideoProvider:
    name = "fake"
    model = "fake-video"

    async def submit_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        metadata = metadata or {}
        task_id = f"fake-video-task-{metadata.get('asset_id', 'shot')}"
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            task_status="PENDING",
            request_id=f"fake-video-request-{metadata.get('asset_id', 'shot')}",
            usage={"duration": duration or 5},
            raw_response={
                "output": {
                    "task_id": task_id,
                    "task_status": "PENDING",
                    "ref_count": len(refs or []),
                    "prompt": prompt,
                }
            },
        )

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        video_bytes = f"fake video: {task_id}".encode("utf-8")
        return VideoGenerationResult(
            provider=self.name,
            model=self.model,
            task_id=task_id,
            task_status="SUCCEEDED",
            video_data=base64.b64encode(video_bytes).decode("ascii"),
            request_id=f"fake-video-request-{task_id}",
            raw_response={
                "output": {
                    "task_id": task_id,
                    "task_status": "SUCCEEDED",
                    "video": "<base64 video omitted>",
                }
            },
        )

    async def generate_video(
        self,
        prompt: str,
        refs: list[AssetRef] | None = None,
        *,
        duration: float | None = None,
        wait: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> VideoGenerationResult:
        result = await self.submit_video(prompt, refs, duration=duration, metadata=metadata)
        if wait:
            return await self.query_video_task(result.task_id or "fake_video_task")
        return result


class FakeAudioJudgeProvider:
    name = "fake"
    model = "fake-audio-judge"

    async def judge_audio_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        refs: list[AssetRef],
        temperature: float = 0.2,
        metadata: dict[str, Any] | None = None,
    ) -> T:
        del prompt, temperature
        metadata = metadata or {}
        if schema is VoiceCatalogProfile:
            voice_label = str(metadata.get("voice_label") or metadata.get("voice_type") or "Fake Voice")
            voice_type = str(metadata.get("voice_type") or "")
            gender = "female" if "female" in voice_type else ("male" if "male" in voice_type else None)
            data = {
                "summary": (
                    f"{voice_label} 的声线在 fake 评测中呈现出干净稳定的中频轮廓，像一块被打磨过的温润木片，"
                    "边缘没有尖锐毛刺，气息推进均匀，咬字颗粒清楚而不过分用力。它的情绪底色偏克制、可靠，"
                    "带一点贴近现实对白的松弛感，闭眼时容易联想到一个说话有分寸、反应清醒的短剧人物。"
                    "这种声音不追求夸张的戏剧爆点，更适合职场、悬疑或生活流场景里需要长期复用的角色配音。"
                ),
                "gender_presentation": gender,
                "age_impression": "young_adult_to_adult",
                "texture": ["clear", "stable"],
                "performance_style": ["natural", "restrained"],
                "strengths": ["普通对白自然", "多情绪样例稳定"],
                "weaknesses": ["fake provider 不代表真实听感"],
                "best_role_types": ["短剧对白角色"],
                "avoid_role_types": ["需要真实听感判断的最终生产选择"],
                "emotion_quality": {
                    str(ref.metadata.get("emotion") or ref.id or "normal"): 8.0
                    for ref in refs
                },
            }
        elif schema is VoiceSelectAudioJudgeOutput:
            candidates = metadata.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                candidates = [
                    {
                        "voice_label": str(metadata.get("selected_voice_label") or "Fake Voice"),
                        "voice_type": str(metadata.get("selected_voice_type") or "fake_voice"),
                        "score": 8.0,
                        "reason": "fake audio judge fallback",
                    }
                ]
            selected = dict(candidates[0])
            data = {
                "selected_candidate_id": selected.get("candidate_id"),
                "selected_voice_type": selected.get("voice_type"),
                "selected_voice_label": selected.get("voice_label") or selected.get("voice_type"),
                "selected_reason": selected.get("reason") or "fake audio judge selected the top candidate.",
                "ranked_candidates": candidates,
            }
        else:
            raise ValueError(f"Fake audio judge has no fixture for schema {schema.__name__}")
        return schema.model_validate(data)


class FakeVoiceDesignProvider:
    name = "fake"
    model = "fake-voice-design"
    clone_model = "fake-voice-clone"
    target_model = "fake-tts"
    resource_id = "fake-tts"
    _SPEAKERS = [
        {
            "name": "Fake Male",
            "voice_type": "fake_male_voice",
            "resource_id": "fake-tts",
            "model_family": "fake",
            "scene": "通用场景",
            "language": "中文",
            "gender": "male",
            "abilities": ["情感变化"],
            "emotion_capable": True,
            "supported_emotions": ["normal", "angry", "sad", "happy", "low"],
            "tags": ["clear", "restrained"],
        },
        {
            "name": "Fake Female",
            "voice_type": "fake_female_voice",
            "resource_id": "fake-tts",
            "model_family": "fake",
            "scene": "通用场景",
            "language": "中文",
            "gender": "female",
            "abilities": ["情感变化"],
            "emotion_capable": True,
            "supported_emotions": ["normal", "angry", "sad", "happy", "low"],
            "tags": ["clear", "warm"],
        },
        {
            "name": "Fake Mature",
            "voice_type": "fake_mature_voice",
            "resource_id": "fake-tts",
            "model_family": "fake",
            "scene": "剧情旁白与成熟角色",
            "language": "中文",
            "gender": "male",
            "abilities": ["情感变化"],
            "emotion_capable": True,
            "supported_emotions": ["normal", "angry", "sad", "happy", "low"],
            "tags": ["mature", "low", "stable"],
        },
    ]

    @classmethod
    def available_speakers(cls) -> list[dict[str, Any]]:
        return [dict(speaker) for speaker in cls._SPEAKERS]

    @classmethod
    def available_speakers_for_prompt(cls) -> list[dict[str, Any]]:
        return cls.available_speakers()

    def resolve_role_voice(
        self,
        *,
        role_id: str,
        role_name: str,
        role_intro: str | None = None,
        role_voice_summary: str | None = None,
        role_personality: str | None = None,
    ) -> str:
        del role_id
        hint = " ".join(
            item
            for item in (role_name, role_intro, role_voice_summary, role_personality)
            if item
        )
        if any(marker in hint for marker in ("女", "她", "母亲", "妻子", "姐姐", "妹妹")):
            return "fake_female_voice"
        if any(marker in hint for marker in ("成熟", "主管", "父亲", "反派", "强势")):
            return "fake_mature_voice"
        return "fake_male_voice"

    def resolve_voice_resource_id(self, voice_type: str | None) -> str:
        for speaker in self._SPEAKERS:
            if speaker["voice_type"] == voice_type:
                return str(speaker["resource_id"])
        return self.resource_id

    async def create_voice(
        self,
        *,
        voice_prompt: str,
        preview_text: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        metadata = metadata or {}
        audio_bytes = f"fake audio preview: {preview_text}".encode("utf-8")
        response_format = str(metadata.get("response_format", "wav"))
        sample_rate = int(metadata.get("sample_rate", 24000))
        voice = f"fake_{preferred_name}"
        return VoiceDesignResult(
            provider=self.name,
            model=self.model,
            voice=voice,
            target_model=self.target_model,
            preview_audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            preview_audio_sample_rate=sample_rate,
            preview_audio_format=response_format,
            request_id=f"fake-request-{preferred_name}",
            usage={"count": 1},
            raw_response={
                "output": {
                    "voice": voice,
                    "target_model": self.target_model,
                    "preview_audio": {
                        "data": "<base64 preview audio omitted>",
                        "sample_rate": sample_rate,
                        "response_format": response_format,
                    },
                },
                "usage": {"count": 1},
                "request_id": f"fake-request-{preferred_name}",
            },
        )

    async def clone_voice_from_audio(
        self,
        *,
        source_audio_path: str,
        preferred_name: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceDesignResult:
        metadata = metadata or {}
        voice = f"fake_clone_{preferred_name}"
        return VoiceDesignResult(
            provider=self.name,
            model=self.clone_model,
            voice=voice,
            target_model=self.target_model,
            request_id=f"fake-clone-request-{preferred_name}",
            usage={"count": 1},
            raw_response={
                "output": {
                    "voice": voice,
                    "target_model": self.target_model,
                    "source_audio_path": source_audio_path,
                },
                "usage": {"count": 1},
                "request_id": f"fake-clone-request-{preferred_name}",
                "metadata": metadata,
            },
        )

    async def synthesize_speech(
        self,
        *,
        voice: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> VoiceSynthesisResult:
        metadata = metadata or {}
        response_format = str(metadata.get("response_format", "wav"))
        sample_rate = int(metadata.get("sample_rate", 24000))
        audio_bytes = f"fake synthesized audio: {voice}: {text}".encode("utf-8")
        return VoiceSynthesisResult(
            provider=self.name,
            model=self.target_model,
            voice=voice,
            audio_data=base64.b64encode(audio_bytes).decode("ascii"),
            audio_sample_rate=sample_rate,
            audio_format=response_format,
            request_id=f"fake-synthesis-request-{voice}",
            usage={"count": 1},
            raw_response={
                "output": {
                    "audio": {
                        "data": "<base64 audio omitted>",
                        "sample_rate": sample_rate,
                        "response_format": response_format,
                    }
                },
                "usage": {"count": 1},
                "request_id": f"fake-synthesis-request-{voice}",
            },
        )
