from __future__ import annotations

import base64
from io import BytesIO
import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from autodrama.core.schemas import (
    AmbientEntityOutput,
    BGMDesignOutput,
    ClipPromptModelOutput,
    ClipSegmentOutput,
    KeyVisionPromptOutput,
    LayoutDedupeReviewOutput,
    LayoutExtractOutput,
    LayoutPromptOutput,
    PropDedupeOutput,
    PropDesignOutput,
    PropExtractOutput,
    PropPromptOutput,
    RoleDuplicateAuditReviewOutput,
    RoleEpisodeKeyAuditReviewOutput,
    RoleExtractOutput,
    RoleSubjectVideoIntroTextOutput,
    RoleboardPromptModelOutput,
    SafeImagePromptRewriteOutput,
    ScriptDetailExpandOutput,
    ScriptNovelExtractBatchOutput,
    ScriptNovelEpisodeOutput,
    ScriptOutlineOutput,
    StoryboardEpisodeOutput,
    StoryboardPromptOutput,
)
from autodrama.core.voice_catalog import (
    VoiceCatalogProfile,
    VoiceSelectAudioJudgeOutput,
    VoiceSelectShortlistOutput,
)
from autodrama.postgen.schemas import PostgenEditPlan
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


def _extract_json_after_label(prompt: str, label: str) -> Any:
    marker = f"{label}："
    start = prompt.find(marker)
    if start < 0:
        marker = f"{label}:"
        start = prompt.find(marker)
    if start < 0:
        return None
    text = prompt[start + len(marker) :].strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(text[index:])
        except Exception:
            continue
        return parsed
    return None


def _episode_keys(episode_count: int) -> list[str]:
    return [f"episode_{index:03d}" for index in range(1, episode_count + 1)]


class FakeTextProvider:
    name = "fake"

    async def generate_json(
        self,
        prompt: str,
        schema: type[T],
        *,
        temperature: float = 0.7,
        metadata: dict[str, Any] | None = None,
        refs: list[AssetRef] | None = None,
    ) -> T:
        del temperature, refs
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
                "outline": "林舟被赵启陷害丢掉晋升机会，苏晚提醒他查看旧邮件。林舟逐步发现合同被调包的证据，并在会议上反击。",
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
            data = {
                "novel_full": (
                    f"{episode_key}，雨夜办公室的灯只剩下一排。林舟摊开合同，"
                    "发现关键页纸张颜色比其他页浅了半分，装订孔也错开了一线。"
                    "苏晚把旧邮件截图推到他面前，附件时间像一枚钉子，把赵启的谎言钉在屏幕上。"
                    "林舟没有立刻说话，他把证据一页页拍下，听着窗外雨声，第一次决定不再退让。"
                ),
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
        elif schema is ClipSegmentOutput or node_name == "clip_segment":
            segment_seconds = int(metadata.get("segment_seconds") or 15)
            duration = int(metadata.get("episode_duration_seconds") or episode_duration_seconds or 30)
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            data = {
                str(index): {
                    "text": (
                        f"{episode_key} 林舟在雨夜办公室发现合同关键页异常，苏晚递来旧邮件截图，证据链逐步清晰。"
                        if index == 1
                        else f"{episode_key} 林舟带着合同和邮件截图进入会议室，赵启的压迫被证据反向逼退。"
                    ),
                    "role_names": ["林舟", "苏晚"] if index == 1 else ["林舟", "赵启"],
                    "prop_names": ["被调包的合同", "邮件截图"],
                    "layout_names": ["雨夜办公室"] if index == 1 else ["会议室"],
                }
                for index in range(1, max(1, (duration + segment_seconds - 1) // segment_seconds) + 1)
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
        elif schema is RoleSubjectVideoIntroTextOutput or node_name == "role_subject_video_intro_text":
            role_name = str(metadata.get("role_name") or "林舟").strip() or "林舟"
            data = {"intro_text": f"我是{role_name}，我会记住这一刻。"}
        elif schema is ClipPromptModelOutput or node_name == "clip_prompt":
            data = {
                "clip_prompt": (
                    "镜头1（0-3秒）：\n"
                    "景别运镜：50mm 斜侧近景，轻微手持推进。\n"
                    "画面内容：雨夜办公室冷白顶灯下，林舟半侧脸低头检查合同页码，"
                    "手指沿着纸张边缘缓慢滑过，发现关键页纸色异常。\n"
                    "声音设计：窗外雨声、空调低频和纸张摩擦声。\n\n"
                    "镜头2（3-8秒）：\n"
                    "景别运镜：低机位桌面跟拍，轨道车缓慢横移。\n"
                    "画面内容：林舟把邮件截图推向桌面中央，视线压向对面的空位，"
                    "低声说：“这份合同被换过，时间线就在这里。”口型清晰匹配台词。\n"
                    "声音设计：文件轻响、远处雷声和一句克制的对白。"
                ),
                "target_duration_seconds": 8,
            }
        elif schema is StoryboardPromptOutput or node_name == "storyboard_prompt":
            expected_keys = metadata.get("expected_keys") or episode_keys
            storyboard_episode_keys = [str(key) for key in expected_keys]
            expected_clip_counts = metadata.get("expected_clip_counts")
            if not isinstance(expected_clip_counts, dict):
                expected_clip_counts = {}
            fallback_clip_count = int(metadata.get("shot_count") or metadata.get("clip_count") or 2)

            def fake_panel_plan(index: int) -> dict[str, str]:
                return {
                    f"P{panel_index:02d}": (
                        f"P{panel_index:02d}（Camera Shot {1 if panel_index <= 4 else 2}）："
                        f"clip {index} 的关键动作阶段；"
                        + ("P04 与 P05 之间画醒目的红色斜杠 cut mark。" if panel_index == 4 else "")
                    )
                    for panel_index in range(1, 13)
                }

            def fake_video_prompt(index: int) -> str:
                return (
                    "内部 Camera Shots："
                    "Camera Shot 1（0-5秒）：雨夜办公室冷白顶灯下，50mm 斜侧近景贴着桌面建立合同，"
                    "林舟以半侧脸低头检查合同页码，雨声和空调低频压住空间。"
                    "Camera Shot 2（5-10秒）：轨道车轻微横移，林舟把邮件截图推向桌面中央，"
                    "说：“这份合同被换过，时间线就在这里。”他说话时口型清晰匹配台词。"
                    "十二宫格面板规划 P01-P12："
                    + " ".join(fake_panel_plan(index).values())
                    + "画面不出现字幕、对白气泡、可读文字、水印、logo、片段编号或无关商标。"
                )

            data = {
                "storyboards": [
                    {
                        "episode_key": key,
                        "clips": [
                            {
                                "clip_id": f"{key}_clip_{index:03d}",
                                "clip_title": f"Clip {index:03d}",
                                "clip_duration_hint": "10s",
                                "clip_text": f"{key} fake clip {index} text",
                                "duration_seconds": 10.0 if index % 2 else 12.0,
                                "role_ids": ["role_林舟", "role_赵启"] if index == 2 else ["role_林舟"],
                                "layout_ids": ["layout_会议室"] if index == 2 else ["layout_雨夜办公室"],
                                "prop_ids": ["prop_邮件截图"] if index == 2 else ["prop_被调包的合同"],
                                "camera_shots": [
                                    {
                                        "camera_shot_id": "Camera Shot 1",
                                        "time_range": "0-5秒",
                                        "description": "林舟在雨夜办公室检查合同，镜头连续推进。",
                                    },
                                    {
                                        "camera_shot_id": "Camera Shot 2",
                                        "time_range": "5-10秒",
                                        "description": "邮件截图被推到桌面中央，证据被揭示。",
                                    },
                                ],
                                "panel_plan": fake_panel_plan(index),
                                "video_prompt": fake_video_prompt(index),
                                "negative_prompt": "无字幕、无水印、无logo、无分格最终画面。",
                            }
                            for index in range(1, int(expected_clip_counts.get(key, fallback_clip_count)) + 1)
                        ],
                    }
                    for key in storyboard_episode_keys
                ]
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
                "generated_prop_intro": {
                    "被调包的合同": "林舟发现合同关键页异常的核心证据道具，A4商务合同中关键页纸张颜色略浅。",
                    "被调包的合同_破损": "相比原合同，边角被撕裂并带有明显折痕和污渍。",
                    "邮件截图": "苏晚提供的旧邮件附件时间线证据，以屏幕截图形式在镜头中展示。",
                },
                "notes": ["fake provider prop extract fixture"],
            }
        elif schema is PropDedupeOutput or node_name == "prop_dedupe":
            data = {
                "generated_prop_intro": {
                    "被调包的合同": "林舟发现合同关键页异常的核心证据道具，A4商务合同中关键页纸张颜色略浅。",
                    "被调包的合同_破损": "相比原合同，边角被撕裂并带有明显折痕和污渍。",
                    "邮件截图": "苏晚提供的旧邮件附件时间线证据，以屏幕截图形式在镜头中展示。",
                },
                "merge_notes": ["fake provider prop dedupe fixture"],
            }
        elif schema is PropPromptOutput or node_name == "prop_prompt":
            data = {
                "prop_prompts": {
                    "被调包的合同": "真人电影质感，无人物道具参考图，A4商务合同平放在深色办公桌上，关键页纸张颜色略浅，页码和纸张边缘纹理差异清楚，冷白台灯从左上照射，背景干净虚化，无可读大段文字、无水印。",
                    "被调包的合同_破损": "基于参考图保持合同页数、装订位置、纸张材质和关键页色差不变，只将边角改为撕裂破损状态，增加折痕、灰尘和轻微污渍，无人物、无手持、无水印。",
                    "邮件截图": "真人电影质感，无人物道具参考图，电脑屏幕或手机屏幕上的旧邮件截图特写，附件时间线和界面布局可见但不生成可读小字，冷蓝屏幕光和桌面反射自然，背景干净。",
                }
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
                "generated_layout_intro": {
                    "雨夜办公室": "林舟发现合同异常并与苏晚核对证据的深夜悬疑调查空间，冷白灯和窗外雨光交织。",
                    "会议室": "林舟公开投屏证据并反击赵启的玻璃会议室对峙空间，长桌、投影屏和冷色顶灯稳定可复用。",
                },
                "notes": ["fake provider layout extract fixture"],
            }
        elif schema is LayoutPromptOutput or node_name == "layout_prompt":
            data = {
                "layout_prompts": {
                    "雨夜办公室": "深夜现代办公室空场景，冷白办公灯与窗外雨光交织，桌面散落合同和打开的电脑，玻璃窗有雨痕反射，入口、工位通道和可取景区域清晰，无人物、无可读文字、无水印。",
                    "会议室": "现代公司玻璃会议室空场景，长桌、投影屏、玻璃墙和冷色顶灯构成公开对峙空间，桌面可放合同证据，入口、座椅通道和投屏背景清晰，无人物、无可读文字、无水印。",
                }
            }
        elif schema is LayoutDedupeReviewOutput or node_name == "layout_dedupe_review":
            data = {
                "generated_layout_intro": {
                    "雨夜办公室": "林舟发现合同异常并与苏晚核对证据的深夜悬疑调查空间，冷白灯和窗外雨光交织。",
                    "会议室": "林舟公开投屏证据并反击赵启的玻璃会议室对峙空间，长桌、投影屏和冷色顶灯稳定可复用。",
                },
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
        elif schema is PostgenEditPlan or node_name == "postgen_edit_plan_generation":
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            clips = _extract_json_after_label(prompt, "输入镜头 JSON")
            if not isinstance(clips, list) or not clips:
                clips = [
                    {
                        "shot_id": f"{episode_key}_shot_001",
                        "shot_index": 1,
                        "source_path": "assets/videos/shots/fake_shot_001.mp4",
                        "duration_seconds": 2.0,
                    }
                ]
            normalized_clips = []
            timeline = []
            for index, clip in enumerate(clips[:9], start=1):
                if not isinstance(clip, dict):
                    continue
                shot_id = str(clip.get("shot_id") or f"{episode_key}_shot_{index:03d}")
                duration = max(0.25, float(clip.get("duration_seconds") or 2.0))
                source_in = min(0.15, duration * 0.1)
                source_out = max(source_in + 0.25, duration - min(0.15, duration * 0.1))
                source_path = str(clip.get("source_path") or clip.get("path") or f"assets/videos/shots/{shot_id}.mp4")
                normalized_clips.append(
                    {
                        "episode_key": episode_key,
                        "shot_id": shot_id,
                        "shot_index": int(clip.get("shot_index") or clip.get("index") or index),
                        "title": clip.get("title"),
                        "source_path": source_path,
                        "duration_seconds": duration,
                        "dialogue_lines": list(clip.get("dialogue_lines") or clip.get("dialogue") or []),
                        "role_ids": list(clip.get("role_ids") or []),
                        "prop_ids": list(clip.get("prop_ids") or []),
                        "content": clip.get("content"),
                        "video_prompt": clip.get("video_prompt"),
                        "camera_movement": clip.get("camera_movement"),
                        "transition_hint": clip.get("transition_hint"),
                    }
                )
                timeline.append(
                    {
                        "clip_id": f"{episode_key}_cut_{index:03d}",
                        "shot_id": shot_id,
                        "source_in": round(source_in, 3),
                        "source_out": round(source_out, 3),
                        "speed": 1.0,
                        "transition_after": {"type": "cut"},
                        "rationale": "fake provider trims short handles for postgen smoke.",
                    }
                )
            output_path_match = re.search(r"output_path=([^,\n]+)", prompt)
            output_path = output_path_match.group(1).strip() if output_path_match else f"outputs/videos/{episode_key}_postgen.mp4"
            data = {
                "schema_version": "autodrama.postgen.edit_plan.v1",
                "episode_key": episode_key,
                "source_clips": normalized_clips,
                "timeline": timeline,
                "output": {
                    "path": output_path,
                    "width": 720,
                    "height": 1280,
                    "fps": 25,
                    "burn_subtitles": True,
                    "audio": False,
                },
                "warnings": [],
                "metadata": {"provider": "fake", "model": "fake"},
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
        elif schema is StoryboardEpisodeOutput:
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

    @staticmethod
    def _png_bytes(*, asset_id: str, prompt: str, metadata: dict[str, Any]) -> bytes:
        from PIL import Image, ImageDraw

        asset_type = str(metadata.get("asset_type") or "")
        if asset_type == "storyboard":
            width, height = 1200, 1600
            image = Image.new("RGB", (width, height), "white")
            draw = ImageDraw.Draw(image)
            panel_count = max(1, int(metadata.get("panel_count") or 12))
            columns = max(1, int(panel_count**0.5))
            if panel_count > columns * columns:
                columns += 1
            rows = max(1, (panel_count + columns - 1) // columns)
            margin = 24
            gutter = 17
            panel_width = (width - margin * 2 - gutter * (columns - 1)) // columns
            panel_height = (height - margin * 2 - gutter * (rows - 1)) // rows
            for index in range(1, panel_count + 1):
                row = (index - 1) // columns
                column = (index - 1) % columns
                x_min = margin + column * (panel_width + gutter)
                y_min = margin + row * (panel_height + gutter)
                x_max = x_min + panel_width
                y_max = y_min + panel_height
                draw.rectangle((x_min, y_min, x_max, y_max), outline="black", width=3)
                draw.text((x_min + 10, y_min + 8), str(index), fill="black")
                draw.line((x_min + 20, y_max - 35, x_max - 20, y_min + 55), fill="black", width=2)
                draw.ellipse((x_min + 70, y_min + 80, x_min + 150, y_min + 160), outline="black", width=2)
                draw.rectangle((x_max - 150, y_max - 130, x_max - 50, y_max - 60), outline="black", width=2)
        else:
            width, height = 768, 1024
            image = Image.new("RGB", (width, height), "white")
            draw = ImageDraw.Draw(image)
            draw.rectangle((24, 24, width - 24, height - 24), outline="black", width=3)
            draw.text((44, 44), f"fake image {asset_id}", fill="black")
            draw.line((80, height - 160, width - 80, 180), fill="black", width=3)
            draw.ellipse((width // 2 - 90, height // 2 - 130, width // 2 + 90, height // 2 + 50), outline="black", width=3)

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

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
        asset_id = str(metadata.get("asset_id", "asset"))
        image_bytes = self._png_bytes(asset_id=asset_id, prompt=prompt, metadata=metadata)
        return ImageGenerationResult(
            provider=self.name,
            model=self.model,
            image_data=[base64.b64encode(image_bytes).decode("ascii")],
            request_id=f"fake-image-request-{asset_id}",
            usage={"image_count": 1},
            raw_response={
                "output": {"image": "<base64 image omitted>"},
                "request_id": f"fake-image-request-{asset_id}",
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
    max_reference_images = 4
    max_reference_audio = 1
    max_reference_videos = 2
    max_reference_video_total_duration_seconds = 15.2

    def __init__(self) -> None:
        self._submitted_tasks: dict[str, dict[str, Any]] = {}

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
        output_metadata = {
            "ref_count": len(refs or []),
            "ref_asset_types": [
                str((ref.metadata or {}).get("asset_type") or ref.type)
                for ref in refs or []
            ],
            "ref_ids": [str(ref.id or ref.path or ref.url or "") for ref in refs or []],
            "prompt": prompt,
        }
        self._submitted_tasks[task_id] = output_metadata
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
                    **output_metadata,
                }
            },
        )

    async def query_video_task(self, task_id: str) -> VideoGenerationResult:
        video_bytes = f"fake video: {task_id}".encode("utf-8")
        output_metadata = self._submitted_tasks.get(task_id, {})
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
                    **output_metadata,
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
