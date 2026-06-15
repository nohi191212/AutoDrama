from __future__ import annotations

import json

from autodrama.postgen.schemas import POSTGEN_EDIT_PLAN_SCHEMA_VERSION, PostgenSourceClip


def build_postgen_edit_plan_prompt(
    *,
    project_title: str,
    episode_key: str,
    source_clips: list[PostgenSourceClip],
    output_path: str,
    width: int,
    height: int,
    fps: int,
) -> str:
    clip_payload = [
        {
            "shot_id": clip.shot_id,
            "shot_index": clip.shot_index,
            "title": clip.title,
            "duration_seconds": clip.duration_seconds,
            "dialogue_lines": clip.dialogue_lines,
            "content": clip.content,
            "camera_movement": clip.camera_movement,
            "transition_hint": clip.transition_hint,
            "source_path": clip.source_path,
        }
        for clip in source_clips
    ]
    return (
        "你是短剧后期剪辑师。请根据输入镜头给出标准化剪辑计划，只输出严格 JSON，"
        "不要输出 Markdown、解释、注释或多余字段。\n\n"
        "剪辑目标：提升短剧节奏，删除空镜头冗余，保留剧情因果、关键动作和台词完整性。"
        "除非必要，保持原始镜头顺序。第一版只允许硬切转场。\n\n"
        f"项目标题：{project_title}\n"
        f"剧集：{episode_key}\n"
        f"输出规格：{width}x{height}, {fps}fps, output_path={output_path}\n\n"
        "输入镜头 JSON：\n"
        f"{json.dumps(clip_payload, ensure_ascii=False, indent=2)}\n\n"
        "返回 JSON 必须符合以下结构：\n"
        "{\n"
        f'  "schema_version": "{POSTGEN_EDIT_PLAN_SCHEMA_VERSION}",\n'
        f'  "episode_key": "{episode_key}",\n'
        '  "source_clips": [输入镜头对象，必须保留 shot_id/source_path/duration_seconds],\n'
        '  "timeline": [\n'
        '    {"clip_id": "episode_001_cut_001", "shot_id": "episode_001_shot_001", '
        '"source_in": 0.0, "source_out": 2.4, "speed": 1.0, '
        '"transition_after": {"type": "cut"}, "rationale": "剪辑原因"}\n'
        "  ],\n"
        f'  "output": {{"path": "{output_path}", "width": {width}, "height": {height}, "fps": {fps}, '
        '"burn_subtitles": true, "audio": true},\n'
        '  "warnings": []\n'
        "}\n\n"
        "硬性约束：\n"
        "- source_clips 数量必须是 1-9。\n"
        "- timeline 每项 shot_id 必须来自 source_clips。\n"
        "- 0 <= source_in < source_out <= 对应 source clip 的 duration_seconds。\n"
        "- speed 范围 0.5 到 2.0。\n"
        '- transition_after.type 只能是 "cut"。\n'
        "- 不要改写 output.path。\n"
    )


__all__ = ["build_postgen_edit_plan_prompt"]
