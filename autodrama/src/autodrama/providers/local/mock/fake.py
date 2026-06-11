from __future__ import annotations

import base64
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from autodrama.core.schemas import (
    AmbientEntityOutput,
    BGMDesignOutput,
    DirectorPrepOutput,
    KeyVisionPromptOutput,
    LayoutDedupeReviewOutput,
    LayoutDesignOutput,
    LayoutExtractOutput,
    PropDesignOutput,
    PropExtractOutput,
    RefFrameSpatialPlan,
    RoleDuplicateAuditReviewOutput,
    RoleEpisodeKeyAuditReviewOutput,
    RoleExtractOutput,
    RoleboardPromptModelOutput,
    SafeImagePromptRewriteOutput,
    ScriptDetailExpandOutput,
    ScriptNovelExtractBatchOutput,
    ScriptNovelEpisodeOutput,
    ScriptOutlineOutput,
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


def _extract_markdown_section(prompt: str, heading: str, next_heading: str | None = None) -> str:
    marker = f"## {heading}"
    if marker not in prompt:
        return ""
    text = prompt.split(marker, 1)[1].strip()
    if next_heading:
        next_marker = f"## {next_heading}"
        if next_marker in text:
            text = text.split(next_marker, 1)[0].strip()
    return text.strip()


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
        elif schema is ScriptDetailExpandOutput or node_name == "script_detail_expand":
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            source_script = _extract_markdown_section(prompt, "输入剧本", "输出要求")
            source_script = source_script or "第一集：\n1-1：室内-日-内\n人物：角色\n△角色站在原地。"
            expanded_script = (
                source_script.rstrip()
                + "\n△细节补强：空气里有细微浮尘，光线从场景边缘斜切进来，"
                + "角色的视线短暂停在关键物件上后才继续动作。"
            )
            data = {
                "episode_key": episode_key,
                "expanded_script": expanded_script,
                "source_char_count": len(source_script),
                "expanded_char_count": len(expanded_script),
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
        elif schema is DirectorPrepOutput or node_name == "director_prep":
            expected_keys = metadata.get("expected_keys") or episode_keys
            director_episode_keys = [str(key) for key in expected_keys]
            data = {
                "story_core": "林舟在被合同调包陷害后，从隐忍调查转向公开反击。",
                "worldview": "现代职场悬疑短剧，证据链、会议权力关系和雨夜空间氛围推动戏剧张力。",
                "visual_tone": "克制写实的短剧摄影，冷白办公光、雨夜反射、低饱和色彩和稳定推进镜头。",
                "immutable_rules": [
                    "合同关键页被调包是核心证据，不得改成其他案件。",
                    "林舟的反击建立在邮件截图和合同细节上，不是凭空爆发。",
                ],
                "character_locks": [
                    {
                        "name": "林舟",
                        "identity": "被陷害的职场青年，调查合同调包并准备反击。",
                        "arc": "从沉默观察到冷静确认证据。",
                        "visual_invariants": ["短发", "深灰职场衬衫", "疲惫但克制的眼神"],
                        "performance_invariants": ["语气压低", "动作克制", "先观察再行动"],
                        "must_not_change": ["不能变成冲动鲁莽的复仇者"],
                    },
                    {
                        "name": "苏晚",
                        "identity": "提供旧邮件截图的协助者。",
                        "arc": "以理性证据帮助林舟稳住局面。",
                        "visual_invariants": ["清冷理性", "数据分析气质"],
                        "performance_invariants": ["递交证据时简洁果断"],
                        "must_not_change": ["不能替代林舟完成正面反击"],
                    },
                ],
                "scene_locks": [
                    {
                        "name": "雨夜办公室",
                        "description": "林舟发现合同异常和邮件证据的深夜办公空间。",
                        "spatial_facts": ["办公桌承载合同和电脑屏幕", "窗外有雨痕和城市霓虹反射"],
                        "lighting_mood": "冷白顶灯和电脑冷蓝光压低环境。",
                        "must_not_change": ["不要改成白天明亮空间"],
                    },
                    {
                        "name": "会议室",
                        "description": "后续公开对峙和证据展示空间。",
                        "spatial_facts": ["长会议桌", "投影屏", "玻璃墙雨痕"],
                        "lighting_mood": "投影冷光与窗外蓝色雨光形成压迫感。",
                        "must_not_change": ["不要移除投影屏和会议桌"],
                    },
                ],
                "episodes": [
                    {
                        "episode_key": key,
                        "story_function": "建立合同调包疑点，并让林舟完成从忍耐到准备反击的情绪转向。",
                        "emotional_curve": ["疲惫压抑", "发现异常", "证据确认", "决定反击"],
                        "shot_beats": [
                            {
                                "beat_index": 1,
                                "title": "雨夜办公室建立",
                                "source_anchor": f"{key}，雨夜办公室的灯只剩下一排。",
                                "dramatic_intent": "建立压抑空间和调查氛围。",
                                "what_to_shoot": "林舟独自在雨夜办公室检查合同。",
                                "camera_language": "低位近景，缓慢推近合同和人物侧脸。",
                                "emotion": "疲惫压抑",
                                "must_keep": ["雨夜办公室", "合同在桌上"],
                                "must_not_change": ["不要提前进入会议室"],
                            },
                            {
                                "beat_index": 2,
                                "title": "合同异常确认",
                                "source_anchor": "发现关键页纸张颜色比其他页浅了半分，装订孔也错开了一线。",
                                "dramatic_intent": "揭示案件核心证据。",
                                "what_to_shoot": "合同色差和装订孔错位被林舟发现。",
                                "camera_language": "合同特写到林舟眼神反应。",
                                "emotion": "警觉确认",
                                "must_keep": ["纸张色差", "装订孔错位"],
                                "must_not_change": ["不要把证据改成口头传闻"],
                            },
                            {
                                "beat_index": 3,
                                "title": "决定不再退让",
                                "source_anchor": "第一次决定不再退让。",
                                "dramatic_intent": "完成本集情绪转折。",
                                "what_to_shoot": "林舟拍下证据并抬眼看向屏幕。",
                                "camera_language": "稳定近景停在半侧脸和证据同框。",
                                "emotion": "克制反击",
                                "must_keep": ["拍下证据", "雨声压低环境"],
                                "must_not_change": ["不要写成外放怒吼"],
                            },
                        ],
                        "must_keep": ["合同调包", "旧邮件截图", "林舟冷静确认证据"],
                        "must_not_change": ["不要新增无关案件", "不要删除苏晚提供证据的功能"],
                    }
                    for key in director_episode_keys
                ],
            }
        elif schema is KeyVisionPromptOutput or node_name == "design_key_vision_prompt":
            data = {
                "prompt": (
                    "真人电影质感，短剧主视觉原图，9:16 竖版海报式构图，雨夜现代办公室与玻璃会议室空间交叠。"
                    "林舟站在画面中央偏前，深灰职场衬衫，神情疲惫但克制，手中压着被调包的合同关键页；"
                    "苏晚位于左后方冷蓝电脑光边缘，递出旧邮件截图；赵启在右侧会议桌阴影里后撤，形成三角对峙。"
                    "前景是纸张色差、错位装订孔和半杯冷咖啡，背景窗玻璃有雨痕和城市霓虹反射，投影冷光与顶灯冷白光压低环境。"
                    "低饱和蓝灰色调，克制悬疑张力，稳定电影镜头感，细节清晰，无可读文字、字幕、水印、logo和无关人物。"
                )
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
        elif schema is RoleboardPromptModelOutput or node_name == "roleboard_prompt":
            role_name = str(metadata.get("role_name") or "林舟").strip() or "林舟"
            data = {
                "roleboard_prompt": (
                    f"真人剧角色身份板，{role_name}，同一角色的正面全身、侧面全身、背面全身、头部近景、"
                    "表情组、常用动作姿态、服装材质细节和随身配饰细节；统一年龄感、脸型、五官、发型、"
                    "服装、体型比例和材质，干净设计板背景，边缘保留小号角色名和视图标签，"
                    "无字幕、水印、logo 或其他无关文字。"
                ),
                "roleboard_negative_prompt": "变脸，换衣服，年龄漂移，多角色混入，字幕，水印，logo，除指定角色名和视图标签外的文字",
                "voice_profile_prompt": f"{role_name}的常规音色，真人短剧对白质感，语速自然，咬字清晰，情绪克制。",
                "design_notes": "fake provider roleboard prompt fixture",
            }
        elif schema is VoiceSelectShortlistOutput or node_name == "role_voice_select_shortlist":
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
        elif schema is RefFrameSpatialPlan or node_name == "ref_frame_spatial_planning":
            previous_shot_id = metadata.get("previous_shot_id")
            reference_ids = [str(previous_shot_id)] if previous_shot_id else []
            data = {
                "same_physical_space_as_previous": bool(previous_shot_id),
                "confidence": 0.82 if previous_shot_id else 0.72,
                "continuity_mode": "previous_shot" if previous_shot_id else "new_space",
                "physical_space_key": "layout_fake::main_area",
                "physical_space_note": "Fake layout main area",
                "reference_shot_ids": reference_ids,
                "spatial_structure_summary": (
                    "Keep the protagonist and key props on the same relative left/right and foreground/background axes."
                ),
                "spatial_constraints": [
                    "Do not flip the protagonist to the other side of key props unless the script says they moved.",
                    "Keep background crowd bands in roughly the same area and density.",
                ],
                "movement_allowed": False,
                "movement_reason": None,
            }
        elif schema is SafeImagePromptRewriteOutput or node_name == "image_prompt_safety_rewrite":
            original_prompt = _extract_markdown_section(prompt, "原始图像生成 prompt", "## 安全失败信息")
            original_prompt = original_prompt or prompt
            data = {
                "prompt": (
                    original_prompt
                    + "\n安全改写：弱化任何可能触发安全策略的血腥、伤口、恐怖、裸露、仇恨或危险细节；"
                    "用尘土、旧污渍、暗色纹理、紧张神情、破损衣料和低风险 CG 动画表现替代。"
                ),
                "notes": "fake safety rewrite",
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
