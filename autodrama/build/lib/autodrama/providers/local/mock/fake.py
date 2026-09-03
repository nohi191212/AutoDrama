from __future__ import annotations

import base64
from io import BytesIO
import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from autodrama.core.schemas import (
    BGMDesignOutput,
    ClipToShotsModelOutput,
    ClipSegmentOutput,
    KeyVisionPromptOutput,
    LayoutBackgroundPromptModelOutput,
    LayoutDedupeReviewOutput,
    LayoutExtractOutput,
    LayoutPromptOutput,
    LayoutPropBoundaryReviewOutput,
    PropDedupeOutput,
    PropDesignOutput,
    PropExtractOutput,
    PropPromptOutput,
    RoleExtractOutput,
    RoleFinalizeAuditReviewOutput,
    RoleVoiceRequirements,
    RoleSubjectVideoIntroTextOutput,
    RoleboardPromptModelOutput,
    SafeImagePromptRewriteOutput,
    ShotKeyframePromptModelOutput,
    ScriptDetailExpandOutput,
    ScriptImportOutput,
    ScriptNovelExtractModelOutput,
    ScriptNovelEpisodeOutput,
    ScriptOutlineOutput,
    ScriptWorldviewExtractOutput,
    ShotManifestEpisodeOutput,
)
from autodrama.core.voice_catalog import (
    VoiceCatalogProfile,
    VoiceSelectAudioJudgeOutput,
    VoiceSelectShortlistOutput,
)
from autodrama.postgen.schemas import PostgenEditPlan, PostgenFinalAuditReport, PostgenSourceAuditReport
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

        if schema is RoleVoiceRequirements or node_name == "role_voice_requirements":
            fixture_requirements = {
                "role-fake-female": {
                    "gender_presentation": "female",
                    "age_impression": "young_adult",
                },
                "role-fake-mature": {
                    "gender_presentation": "male",
                    "age_impression": "mature",
                },
            }
            fixture = fixture_requirements.get(str(metadata.get("role_id") or ""), {})
            data = {
                "schema_version": 2,
                "language": "zh",
                "gender_presentation": fixture.get("gender_presentation", "unspecified"),
                "age_impression": fixture.get("age_impression", "unspecified"),
                "performance_traits": [],
                "baseline_emotion": None,
                "hard_constraints": [],
                "provenance": {
                    "source": "model",
                    "evidence": ["fake structured voice requirement fixture"],
                    "confidence": 1.0,
                    "model": "fake-text",
                },
            }
        elif schema is ScriptImportOutput or node_name == "script_import":
            source_section = prompt.partition("原始剧本文本：")[2]
            source_section = source_section.partition("返回由系统提供结构约束的纯 JSON")[0]
            source_excerpt = next(
                (line.strip() for line in source_section.splitlines() if line.strip()),
                "原始剧本",
            )
            data = {
                "outline": "江未晞在破败殿宇中醒来，遇见由银白光点凝聚成形的乐园AI管家九韶，得知自己被乐园令牌选中，并可通过运营密室夺回被掠夺的气运与人生。九韶演示山海经主题新手区，九尾狐密室的真实触感和狐爪机关让江未晞第一次贡献恐惧与惊喜能量，也激起她开启密室的欲望。",
                "episode_outlines": [
                    "原始剧本中的角色围绕核心冲突推进事件，并在结尾保留后续悬念。",
                ],
                "roles": [],
                "props": [],
                "layouts": [],
                "facts": {
                    "events": [
                        {
                            "summary": "原始剧本中的核心事件推进。",
                            "time_period": None,
                            "participant_names": [],
                            "prop_names": [],
                            "layout_names": [],
                            "precondition": None,
                            "result": None,
                            "evidence_quotes": [source_excerpt],
                        }
                    ],
                    "entity_mentions": [
                        {
                            "name": "叙事片段",
                            "entity_type": "group",
                            "time_period": None,
                            "evidence_quotes": [source_excerpt],
                        }
                    ],
                    "prop_observations": [],
                },
                "notes": "fake script_import output",
            }
        elif schema is ScriptOutlineOutput or node_name == "script_outline":
            data = {
                "outline": "林舟被赵启陷害丢掉晋升机会，苏晚提醒他查看旧邮件。林舟逐步发现合同被调包的证据，并在会议上反击。",
                "episode_outlines": {
                    key: f"第{index}集：林舟围绕合同调包事件推进调查与反击，冲突逐步升级。"
                    for index, key in enumerate(episode_keys, start=1)
                },
            }
        elif schema is ScriptDetailExpandOutput or node_name == "script_detail_expand":
            source_script = _extract_markdown_section(prompt, "输入剧本", "绝对禁止")
            source_script = source_script or "第一集：\n1-1：室内-日-内\n人物：角色\n△角色站在原地。"
            expanded_script = (
                source_script.rstrip()
                + "\n△细节补强：空气里有细微浮尘，光线从场景边缘斜切进来，"
                + "角色的视线短暂停在关键物件上后才继续动作。"
            )
            data = {
                "expanded_script": expanded_script,
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
        elif schema is ScriptWorldviewExtractOutput or node_name == "script_worldview_extract":
            data = {
                "script_type": (
                    "Eastern cultivation fantasy centered on immortal sects, mountain sanctuaries, "
                    "spiritual energy, hierarchical martial traditions, and supernatural ascension."
                )
            }
        elif schema is KeyVisionPromptOutput or node_name == "key_vision_prompt":
            data = {
                "shot_contract": (
                    "Establish one canonical cultivation-world mountain sanctuary in a cinematic medium-wide frame: "
                    "two generic world-native figures occupy the middle distance, one three-quarter front while "
                    "playing a qin and one in clear side profile while looking across the valley. Keep the layered "
                    "gate, inner terraces, distant peaks, sky, and vegetation in one continuous near/mid/far "
                    "perspective system, with the gate as the primary environmental anchor."
                ),
                "scene_style_contract": (
                    "Use distinct silhouettes and costume language for the two figures, cool mountain haze, restrained "
                    "jade and stone tones, warm low sunlight on timber and carved stone, matte cloth, weathered rock, "
                    "clear atmospheric depth, and premium polished 3D CG rendering without glossy spectacle."
                ),
                "prompt": (
                    "A premium stylized 3D CG xuanhuan world-establishing medium-wide frame: on a high stone terrace "
                    "above a cultivation-sect valley, a seated world-native musician in layered jade-and-ink robes "
                    "plays a qin in three-quarter front view while a second figure in a distinct dark ceremonial "
                    "silhouette stands in clear side profile looking toward the peaks. Place the two figures at a "
                    "readable middle distance, not as tiny scale markers. Use a nearby carved stone balustrade and "
                    "weathered timber in the foreground, a monumental gate, terraced roofs, and suspended bridges in "
                    "the middle ground, and misty peaks, sky, and mountain vegetation in the far distance. Keep one "
                    "continuous spatial system, polished anatomy, strong silhouette separation, warm directional light "
                    "on stone and cloth, cool atmospheric fill, nuanced jade and charcoal color design, believable "
                    "materials, and refined atmospheric perspective. No screenplay scene, named character, plot event, "
                    "title, readable text, subtitle, logo, watermark, collage, split scene, crowd, or poster symmetry."
                )
            }
        elif schema is ScriptNovelExtractModelOutput or node_name == "script_novel_extract":
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            data = {
                "script_novel_extract": (
                    f"{episode_key}，时间是雨夜到次日会议前后，地点在公司办公室和会议室。"
                    "林舟发现合同关键页被调包，苏晚递来旧邮件截图作为证据。"
                    "赵启持续施压，林舟保留证据并准备在会议上反击。"
                )
            }
        elif schema is ClipSegmentOutput or node_name == "clip_segment":
            segment_seconds = int(metadata.get("segment_seconds") or 15)
            duration = int(metadata.get("episode_duration_seconds") or episode_duration_seconds or 30)
            episode_key = str(metadata.get("episode_key") or episode_keys[0])
            scene_ids = re.findall(r"^(layout_[^:\s]+):", prompt, flags=re.MULTILINE)
            if not scene_ids:
                scene_ids = ["layout_雨夜办公室", "layout_会议室"]
            data = {
                str(index): {
                    "text": (
                        f"{episode_key} 林舟在雨夜办公室发现合同关键页异常，苏晚递来旧邮件截图，证据链逐步清晰。"
                        if index == 1
                        else f"{episode_key} 林舟带着合同和邮件截图进入会议室，赵启的压迫被证据反向逼退。"
                    ),
                    "role_names": ["林舟", "苏晚"] if index == 1 else ["林舟", "赵启"],
                    "prop_names": ["被调包的合同", "邮件截图"],
                    "scene_id": scene_ids[min(index - 1, len(scene_ids) - 1)],
                }
                for index in range(1, max(1, (duration + segment_seconds - 1) // segment_seconds) + 1)
            }
        elif schema is RoleExtractOutput or node_name in {            "role_extract_primary",
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
                            "appearance_assets": [
                                {
                                    "name": "base",
                                    "asset_role": "base",
                                    "episode_keys": episode_keys,
                                    "source_chapters": ["第1章-第2章"],
                                    "brief": "林舟稳定基础身份造型。",
                                    "clothing": "简洁深色通勤装",
                                    "visual_features": "青年男性，短发，身形偏瘦，眼神疲惫但冷静",
                                    "appearance_desc": "青年男性，短发，身形偏瘦，简洁深色通勤装。",
                                    "identity_invariants": ["青年男性", "短发", "身形偏瘦"],
                                    "wardrobe": ["简洁深色通勤装"],
                                    "provenance": {
                                        "source": "model",
                                        "evidence": ["第1章-第2章角色设定"],
                                        "confidence": 0.95,
                                        "model": "fake-text",
                                    },
                                },
                                {
                                    "name": "雨夜办公室",
                                    "asset_role": "variant",
                                    "reference_asset_name": "base",
                                    "episode_keys": episode_keys,
                                    "source_chapters": ["第1章-第2章"],
                                    "brief": "雨夜办公室场景造型。",
                                    "clothing": "被雨水打湿的深色衬衫和外套",
                                    "visual_features": "保持林舟同一脸、短发和偏瘦身形，衣料微湿",
                                    "appearance_desc": "同一林舟，短发偏瘦，深色衬衫外套被雨水打湿。",
                                    "identity_invariants": ["青年男性", "短发", "身形偏瘦"],
                                    "wardrobe": ["深色衬衫", "深色外套"],
                                    "valid_from_event": "雨夜办公室开始",
                                    "valid_to_event": "雨夜办公室结束",
                                    "provenance": {
                                        "source": "model",
                                        "evidence": ["第1章-第2章雨夜办公室事件"],
                                        "confidence": 0.9,
                                        "model": "fake-text",
                                    },
                                },
                            ],
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
                            "appearance_assets": [
                                {
                                    "name": "base",
                                    "asset_role": "base",
                                    "episode_keys": episode_keys,
                                    "source_chapters": ["第1章-第2章"],
                                    "brief": "苏晚稳定基础身份造型。",
                                    "visual_features": "青年女性，气质清冷，身形修长",
                                    "identity_invariants": ["青年女性", "身形修长"],
                                    "wardrobe": ["简洁通勤装"],
                                    "provenance": {
                                        "source": "model",
                                        "evidence": ["第1章-第2章角色设定"],
                                        "confidence": 0.9,
                                        "model": "fake-text",
                                    },
                                }
                            ],
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
                            "appearance_assets": [
                                {
                                    "name": "base",
                                    "asset_role": "base",
                                    "episode_keys": episode_keys,
                                    "source_chapters": ["第1章-第2章"],
                                    "brief": "赵启稳定基础身份造型。",
                                    "visual_features": "成熟男性，体型中等偏壮，神情强势",
                                    "identity_invariants": ["成熟男性", "体型中等偏壮"],
                                    "wardrobe": ["管理层商务装"],
                                    "provenance": {
                                        "source": "model",
                                        "evidence": ["第1章-第2章角色设定"],
                                        "confidence": 0.9,
                                        "model": "fake-text",
                                    },
                                }
                            ],
                            "has_dialogue": True,
                            "visual_reuse_required": True,
                        },
                    ]
                }
        elif schema is RoleFinalizeAuditReviewOutput or node_name == "role_finalize":
            data = {"episode_updates": [], "duplicate_groups": [], "drop_roles": [], "notes": []}
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
        elif schema is ClipToShotsModelOutput or node_name == "clip_to_shots":
            available_ids = re.findall(r"^([a-zA-Z0-9_\-\u4e00-\u9fff]+):", prompt, flags=re.MULTILINE)
            scene_match = re.search(r"Exact scene ID:\s*`([^`]+)`", prompt)
            scene_id = scene_match.group(1) if scene_match else "layout_雨夜办公室"
            duration_match = re.search(
                r"approximately\s+([0-9]+(?:\.[0-9]+)?)\s+seconds\s+available",
                prompt,
                flags=re.IGNORECASE,
            )
            available_seconds = max(3, int(round(float(duration_match.group(1))))) if duration_match else 30
            shot_count = max(1, (available_seconds + 14) // 15)
            duration_base, duration_remainder = divmod(available_seconds, shot_count)
            shot_durations = [
                duration_base + (1 if shot_index < duration_remainder else 0)
                for shot_index in range(shot_count)
            ]
            roleboard_rows = re.findall(
                r"^([^:\s]+):.*?\[role_id=([^;\]]+);\s*appearance_id=([^\]]+)",
                prompt,
                flags=re.MULTILINE,
            )
            roleboard_by_role = {
                role_id: (asset_id, appearance_id)
                for asset_id, role_id, appearance_id in roleboard_rows
            }
            role_ids = [asset_id for asset_id, _appearance_id in roleboard_by_role.values()]
            entity_bindings = [
                (role_id, appearance_id)
                for role_id, (_asset_id, appearance_id) in roleboard_by_role.items()
            ]
            prop_ids = re.findall(r"\[prop_id=([^\]]+)", prompt)
            data = {
                f"shot_{shot_index}": {
                    "scene_id": scene_id,
                    "shot_description": "Lin Zhou reviews the contract while Su Wan presents the email evidence beside the office table.",
                    "narrative_angle": "Observe the evidence exchange at eye level from Su Wan's side of the table.",
                    "opening_state": "The contract lies open while the email screenshot is held just above the table edge.",
                    "character_placements": [
                        {
                            "role_id": role_id,
                            "scene_position": (
                                "0.8 m left of the evidence table beside the window-side chair"
                                if index == 1
                                else "at the near edge of the evidence table beside the aisle"
                            ),
                            "screen_position": "left third" if index == 1 else "center-right",
                            "depth_layer": "foreground" if index == 1 else "midground",
                            "body_facing": "toward the evidence table and the other character",
                            "gaze_target": "the contract on the evidence table",
                            "pose": "standing and leaning slightly toward the evidence",
                        }
                        for index, (role_id, _appearance_id) in enumerate(entity_bindings[:2], start=1)
                    ],
                    "camera": {
                        "scene_position": (
                            "in the aisle 1.5 m from the evidence table on its south-east side"
                            if shot_index % 2 == 1
                            else "beside the window-side chair 1.8 m north-west of the evidence table"
                        ),
                        "target": "the center of the evidence table between the two characters",
                        "shooting_angle": (
                            "eye-level three-quarter two-shot"
                            if shot_index % 2 == 1
                            else "eye-level reverse three-quarter two-shot"
                        ),
                        "shot_size": "medium two-shot",
                        "camera_height_m": 1.6,
                        "pitch_degrees": -3.0,
                        "field_of_view_degrees": 54.0,
                        "focal_length_mm": 35.0,
                        "movement": "locked-off",
                    },
                    "ref_ids": role_ids[:2],
                    "video_prompt": "In a locked medium two-shot, Lin Zhou reviews the contract as Su Wan slides the old email screenshot toward him.",
                    "duration_seconds": shot_durations[shot_index - 1],
                    "entity_states": [
                        {
                            "schema_version": 1,
                            "entity_id": role_id,
                            "appearance_id": appearance_id,
                            "pose": "standing beside the office evidence table",
                            "emotion": "focused",
                            "injury": None,
                            "held_props": [],
                            "energy_state": None,
                            "event_refs": [],
                        }
                        for role_id, appearance_id in entity_bindings[:2]
                    ],
                    "dialogue_lines": [],
                    "overlay_text_spec": None,
                    "allowed_props": prop_ids[:1],
                }
                for shot_index in range(1, shot_count + 1)
            }
        elif schema is LayoutBackgroundPromptModelOutput or node_name == "layout_to_background_prompt":
            indices = [int(value) for value in re.findall(r'"index"\s*:\s*(\d+)', prompt)] or [1]
            data = {
                f"background_{output_index}": {
                    "prompt_content": "An empty cinematic office background from the specified eye-level three-quarter camera, preserving the evidence table, rain-streaked windows, aisle geometry, cool ceiling lights, and fixed set dressing.",
                    "shot_index": shot_index,
                    "description": "An empty office plate aligned to the planned evidence-table two-shot.",
                }
                for output_index, shot_index in enumerate(indices, start=1)
            }
        elif schema is ShotKeyframePromptModelOutput or node_name == "shot_keyframe_prompt":
            data = {
                "prompt_content": "Keep Image 1's office background, eye-level camera, table perspective, and rainy-night lighting unchanged. Place Lin Zhou from Image 2 in the left foreground reviewing the contract, and place Su Wan from Image 3 in the midground beside the table presenting the email screenshot; direct both gazes toward the evidence on the table."
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
                        "aliases": ["异常合同", "合同证据"],
                        "intro": "林舟发现合同关键页异常的核心证据道具。",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "owner_role_name": None,
                        "assets": [
                            {
                                "name": "base",
                                "asset_role": "base",
                                "status": "normal",
                                "reference_asset_name": None,
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "A4商务合同，装订整齐，关键页纸张颜色略浅，页码和边缘纹理与其他页不一致。",
                                "visual_features": "A4纸张、商务合同装订、关键页浅色差、纸张边缘纹理",
                                "state_change": "",
                                "prompt_hint": "锁定合同装订与关键页色差。",
                            },
                            {
                                "name": "破损",
                                "asset_role": "variant",
                                "status": "damaged",
                                "reference_asset_name": "base",
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "合同被撕扯后的状态。",
                                "visual_features": "",
                                "state_change": "边角撕裂，纸面增加折痕、灰尘和轻微污渍。",
                                "prompt_hint": "基于 base 图编辑，只改变破损和污渍状态。",
                            },
                        ],
                    },
                    {
                        "name": "邮件截图",
                        "aliases": ["旧邮件截图"],
                        "intro": "苏晚提供的旧邮件附件时间线证据，以屏幕截图形式在镜头中展示。",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "owner_role_name": None,
                        "assets": [
                            {
                                "name": "base",
                                "asset_role": "base",
                                "status": "normal",
                                "reference_asset_name": None,
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "电脑或手机屏幕上的旧邮件截图，界面中有附件时间线和邮件列表版式。",
                                "visual_features": "屏幕界面、邮件列表版式、附件时间线色块、冷蓝屏幕光",
                                "state_change": "",
                                "prompt_hint": "不要依赖可读小字，用版式和色块表达证据属性。",
                            }
                        ],
                    },
                ],
                "notes": ["fake provider prop extract fixture"],
            }
        elif schema is PropDedupeOutput or node_name == "prop_finalize":
            data = {
                "props": [
                    {
                        "name": "被调包的合同",
                        "aliases": ["异常合同", "合同证据"],
                        "intro": "林舟发现合同关键页异常的核心证据道具。",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "owner_role_name": None,
                        "assets": [
                            {
                                "name": "base",
                                "asset_role": "base",
                                "status": "normal",
                                "reference_asset_name": None,
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "A4商务合同，装订整齐，关键页纸张颜色略浅，页码和边缘纹理与其他页不一致。",
                                "visual_features": "A4纸张、商务合同装订、关键页浅色差、纸张边缘纹理",
                                "state_change": "",
                                "prompt_hint": "锁定合同装订与关键页色差。",
                            },
                            {
                                "name": "破损",
                                "asset_role": "variant",
                                "status": "damaged",
                                "reference_asset_name": "base",
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "合同被撕扯后的状态。",
                                "visual_features": "",
                                "state_change": "边角撕裂，纸面增加折痕、灰尘和轻微污渍。",
                                "prompt_hint": "基于 base 图编辑，只改变破损和污渍状态。",
                            },
                        ],
                    },
                    {
                        "name": "邮件截图",
                        "aliases": ["旧邮件截图"],
                        "intro": "苏晚提供的旧邮件附件时间线证据，以屏幕截图形式在镜头中展示。",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "owner_role_name": None,
                        "assets": [
                            {
                                "name": "base",
                                "asset_role": "base",
                                "status": "normal",
                                "reference_asset_name": None,
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "电脑或手机屏幕上的旧邮件截图，界面中有附件时间线和邮件列表版式。",
                                "visual_features": "屏幕界面、邮件列表版式、附件时间线色块、冷蓝屏幕光",
                                "state_change": "",
                                "prompt_hint": "不要依赖可读小字，用版式和色块表达证据属性。",
                            }
                        ],
                    },
                ],
                "merge_notes": ["fake provider prop dedupe fixture"],
            }
        elif schema is PropPromptOutput or node_name == "prop_prompt":
            data = {
                "prop_asset_prompts": [
                    {
                        "prop_name": "被调包的合同",
                        "asset_name": "base",
                        "prompt_type": "text_to_image",
                        "reference_asset_name": None,
                        "prompt": "真人电影质感，无人物道具参考图，A4商务合同平放在干净深色办公桌面，装订整齐，关键页纸张颜色略浅，纸张边缘纹理差异清楚，冷白台灯从左上照射，背景简洁，无可读大段文字、无水印。",
                    },
                    {
                        "prop_name": "被调包的合同",
                        "asset_name": "破损",
                        "prompt_type": "image_edit",
                        "reference_asset_name": "base",
                        "prompt": "以 base 对应的基准合同图为参考，保持合同页数、装订位置、纸张材质、关键页色差和桌面接触关系不变，只将边角改为撕裂破损状态，增加折痕、灰尘和轻微污渍，无人物、无手持、无水印。",
                    },
                    {
                        "prop_name": "邮件截图",
                        "asset_name": "base",
                        "prompt_type": "text_to_image",
                        "reference_asset_name": None,
                        "prompt": "真人电影质感，无人物道具参考图，电脑屏幕或手机屏幕上的旧邮件截图特写，附件时间线和界面布局可见但不生成可读小字，冷蓝屏幕光和桌面反射自然，背景干净。",
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
                    }
                ]
            }
            if prop_name:
                selected_props = [prop for prop in data["props"] if prop.get("name") == prop_name]
                data["props"] = selected_props or data["props"][:1]
        elif schema is LayoutExtractOutput or node_name == "layout_extract":
            data = {
                "layouts": [
                    {
                        "name": "办公室",
                        "group": "办公室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "现代公司办公室，工位、玻璃窗、文件桌和入口通道构成稳定调查空间。",
                        "space_features": ["现代办公室", "工位区", "玻璃窗", "文件桌", "入口通道"],
                        "state_delta": "",
                    },
                    {
                        "name": "办公室_雨夜",
                        "group": "办公室",
                        "asset_role": "variant",
                        "reference_asset_name": "办公室",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "保持办公室空间结构不变，只改为深夜冷白灯与窗外雨光交织的悬疑调查状态。",
                        "space_features": [],
                        "state_delta": "深夜冷白灯与窗外雨光交织，玻璃窗有雨痕反射。",
                    },
                    {
                        "name": "会议室",
                        "group": "会议室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "现代公司玻璃会议室，长桌、投影屏、玻璃墙、座椅通道和冷色顶灯稳定可复用。",
                        "space_features": ["玻璃会议室", "长桌", "投影屏", "玻璃墙", "座椅通道"],
                        "state_delta": "",
                    },
                ],
                "notes": ["fake provider layout extract fixture"],
            }
        elif schema is LayoutPromptOutput or node_name == "layout_prompt":
            data = {
                "layout_prompts": [
                    {
                        "name": "办公室",
                        "group": "办公室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "prompt_type": "text_to_image",
                        "prompt": "A 2:3 spatial-anchor sheet with two vertically stacked complementary high-angle isometric views of the same empty modern office. Preserve identical workstations, windows, evidence table, entrance, scale, north orientation, materials, lighting, and circulation. No people, camera overlays, readable text, logo, or watermark.",
                    },
                    {
                        "name": "办公室_雨夜",
                        "group": "办公室",
                        "asset_role": "variant",
                        "reference_asset_name": "办公室",
                        "prompt_type": "image_edit",
                        "prompt": "Edit the office spatial-anchor sheet while preserving both views, topology, workstations, windows, evidence table, entrance, scale, north orientation, materials, and circulation. Change only the state to a rainy night with cool white interior light, reflected exterior rain light, and rain streaks on the glass. No people, camera overlays, readable text, logo, or watermark.",
                    },
                    {
                        "name": "会议室",
                        "group": "会议室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "prompt_type": "text_to_image",
                        "prompt": "A 2:3 spatial-anchor sheet with two vertically stacked complementary high-angle isometric views of the same empty modern meeting room. Preserve identical table, glass walls, entrances, aisle, scale, north orientation, materials, and fixed lighting. No people, camera overlays, readable text, logo, or watermark.",
                    },
                ]
            }
        elif schema is LayoutDedupeReviewOutput or node_name == "layout_finalize":
            data = {
                "layouts": [
                    {
                        "name": "办公室",
                        "group": "办公室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "现代公司办公室，工位、玻璃窗、文件桌和入口通道构成稳定调查空间。",
                        "space_features": ["现代办公室", "工位区", "玻璃窗", "文件桌", "入口通道"],
                        "state_delta": "",
                    },
                    {
                        "name": "办公室_雨夜",
                        "group": "办公室",
                        "asset_role": "variant",
                        "reference_asset_name": "办公室",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "保持办公室空间结构不变，只改为深夜冷白灯与窗外雨光交织的悬疑调查状态。",
                        "space_features": [],
                        "state_delta": "深夜冷白灯与窗外雨光交织，玻璃窗有雨痕反射。",
                    },
                    {
                        "name": "会议室",
                        "group": "会议室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "现代公司玻璃会议室，长桌、投影屏、玻璃墙、座椅通道和冷色顶灯稳定可复用。",
                        "space_features": ["玻璃会议室", "长桌", "投影屏", "玻璃墙", "座椅通道"],
                        "state_delta": "",
                    },
                ],
                "merge_notes": ["未发现需要合并的重复场景。"],
            }
        elif schema is LayoutPropBoundaryReviewOutput or node_name == "layout_prop_boundary_review":
            data = {
                "props": [
                    {
                        "name": "被调包的合同",
                        "aliases": ["异常合同", "合同证据"],
                        "intro": "林舟发现合同关键页异常的核心证据道具。",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "owner_role_name": None,
                        "assets": [
                            {
                                "name": "base",
                                "asset_role": "base",
                                "status": "normal",
                                "reference_asset_name": None,
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "A4商务合同，装订整齐，关键页纸张颜色略浅，页码和边缘纹理与其他页不一致。",
                                "visual_features": "A4纸张、商务合同装订、关键页浅色差、纸张边缘纹理",
                                "state_change": "",
                                "prompt_hint": "锁定合同装订与关键页色差。",
                            },
                            {
                                "name": "破损",
                                "asset_role": "variant",
                                "status": "damaged",
                                "reference_asset_name": "base",
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "合同被撕扯后的状态。",
                                "visual_features": "",
                                "state_change": "边角撕裂，纸面增加折痕、灰尘和轻微污渍。",
                                "prompt_hint": "基于 base 图编辑，只改变破损和污渍状态。",
                            },
                        ],
                    },
                    {
                        "name": "邮件截图",
                        "aliases": ["旧邮件截图"],
                        "intro": "苏晚提供的旧邮件附件时间线证据，以屏幕截图形式在镜头中展示。",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "owner_role_name": None,
                        "assets": [
                            {
                                "name": "base",
                                "asset_role": "base",
                                "status": "normal",
                                "reference_asset_name": None,
                                "episode_keys": episode_keys,
                                "source_chapters": [],
                                "desc": "电脑或手机屏幕上的旧邮件截图，界面中有附件时间线和邮件列表版式。",
                                "visual_features": "屏幕界面、邮件列表版式、附件时间线色块、冷蓝屏幕光",
                                "state_change": "",
                                "prompt_hint": "不要依赖可读小字，用版式和色块表达证据属性。",
                            }
                        ],
                    },
                ],
                "layouts": [
                    {
                        "name": "办公室",
                        "group": "办公室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "现代公司办公室，工位、玻璃窗、文件桌和入口通道构成稳定调查空间。",
                        "space_features": ["现代办公室", "工位区", "玻璃窗", "文件桌", "入口通道"],
                        "state_delta": "",
                    },
                    {
                        "name": "办公室_雨夜",
                        "group": "办公室",
                        "asset_role": "variant",
                        "reference_asset_name": "办公室",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "保持办公室空间结构不变，只改为深夜冷白灯与窗外雨光交织的悬疑调查状态。",
                        "space_features": [],
                        "state_delta": "深夜冷白灯与窗外雨光交织，玻璃窗有雨痕反射。",
                    },
                    {
                        "name": "会议室",
                        "group": "会议室",
                        "asset_role": "base",
                        "reference_asset_name": "",
                        "episode_keys": episode_keys,
                        "source_chapters": [],
                        "brief": "现代公司玻璃会议室，长桌、投影屏、玻璃墙、座椅通道和冷色顶灯稳定可复用。",
                        "space_features": ["玻璃会议室", "长桌", "投影屏", "玻璃墙", "座椅通道"],
                        "state_delta": "",
                    },
                ],
                "review_notes": ["fake provider layout/prop boundary review fixture"],
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
        elif schema is PostgenSourceAuditReport or node_name == "postgen_source_audit":
            episode_key = str(metadata.get("episode_key") or "episode_001")
            source_items = _extract_json_after_label(prompt, "素材") or []
            data = {
                "episode_key": episode_key,
                "clips": [
                    {
                        "shot_id": str(item.get("shot_id")),
                        "verdict": "pass",
                        "usable_start": 0.0,
                        "usable_end": float(item.get("duration_seconds") or 1.0),
                        "issues": [],
                        "edit_guidance": "fake audit: preserve usable range",
                    }
                    for item in source_items
                ],
                "continuity_notes": [],
                "overall_notes": "fake source audit passed",
            }
        elif schema is PostgenFinalAuditReport or node_name == "postgen_final_audit":
            data = {
                "episode_key": str(metadata.get("episode_key") or "episode_001"),
                "verdict": "pass",
                "score": 95,
                "issues": [],
                "repair_actions": [],
                "summary": "fake final audit passed",
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
                        "dialogue_lines": list(clip.get("dialogue_lines") or []),
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
                    "audio": True,
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
        elif schema.__name__ in {"ImageAuditDecision", "VideoAuditDecision"}:
            data = {
                "approved": True,
                "issues": [],
                "revised_prompt": "",
                "rationale": "fake provider accepted the deterministic audit fixture",
            }
        elif schema is ShotManifestEpisodeOutput:
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
                        "dialogue_lines": [],
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
                        "dialogue_lines": [
                            {
                                "schema_version": 1,
                                "line_index": 1,
                                "speaker_role_id": "role_林舟",
                                "speaker_name": "林舟",
                                "text": "这份合同被换过，时间线就在这里。",
                                "emotion": "normal",
                                "intensity": 0.6,
                                "delivery_mode": "on_screen",
                                "source_text": "林舟：这份合同被换过，时间线就在这里。",
                                "provenance": {
                                    "schema_version": 1,
                                    "source": "model",
                                    "evidence": ["林舟：这份合同被换过，时间线就在这里。"],
                                    "confidence": 1.0,
                                    "model": "fake",
                                },
                            }
                        ],
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
    size = "1024x1536"
    supports_reference_images = True

    @staticmethod
    def _png_bytes(*, asset_id: str, prompt: str, metadata: dict[str, Any]) -> bytes:
        from PIL import Image, ImageDraw

        asset_type = str(metadata.get("asset_type") or "")
        if asset_type == "contact_sheet":
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
            fixture_gender = {
                "fake_male_voice": "male",
                "fake_female_voice": "female",
                "fake_mature_voice": "male",
            }
            gender = fixture_gender.get(voice_type, "unspecified")
            data = {
                "summary": (
                    f"{voice_label} 的声线在 fake 评测中呈现出干净稳定的中频轮廓，像一块被打磨过的温润木片，"
                    "边缘没有尖锐毛刺，气息推进均匀，咬字颗粒清楚而不过分用力。它的情绪底色偏克制、可靠，"
                    "带一点贴近现实对白的松弛感，闭眼时容易联想到一个说话有分寸、反应清醒的短剧人物。"
                    "这种声音不追求夸张的戏剧爆点，更适合职场、悬疑或生活流场景里需要长期复用的角色配音。"
                ),
                "language": "zh",
                "gender_presentation": gender,
                "age_impression": "young_adult",
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
                "field_sources": {
                    "language": "audio_judge",
                    "gender_presentation": "audio_judge",
                    "age_impression": "audio_judge",
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
        voice_requirements: dict[str, Any] | None = None,
    ) -> str:
        del role_name
        requirements = voice_requirements if isinstance(voice_requirements, dict) else {}
        if requirements.get("gender_presentation") == "female":
            return "fake_female_voice"
        if requirements.get("age_impression") in {"mature", "elderly"}:
            return "fake_mature_voice"
        fixture_voices = {
            "role-fake-female": "fake_female_voice",
            "role-fake-mature": "fake_mature_voice",
        }
        if role_id in fixture_voices:
            return fixture_voices[role_id]
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
