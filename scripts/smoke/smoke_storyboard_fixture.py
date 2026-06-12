from __future__ import annotations

from pathlib import Path
from typing import Any

from autodrama.core.schemas import StoryboardEpisodeOutput, StoryboardShot


def fake_storyboard_episode(episode_key: str = "episode_001", *, shot_count: int = 2) -> StoryboardEpisodeOutput:
    templates: list[dict[str, Any]] = [
        {
            "layout_id": "layout_雨夜办公室",
            "title": "发现异常合同",
            "duration_seconds": 6,
            "transition": "结尾停在可硬切状态",
            "dialogue": [],
            "role_ids": ["role_林舟"],
            "role_appearance_ids": ["role_林舟_appearance_base"],
            "role_audio_ids": [],
            "prop_ids": ["prop_被调包的合同"],
            "video_prompt": (
                "当前片段从桌面斜侧 50mm 近景开始；相机缓慢推近合同关键页，"
                "让浅色纸张、错位页码和装订孔依次进入焦点；林舟的视线从合同页码移到电脑屏幕邮件附件时间，"
                "最后停在林舟抬眼看向屏幕的半侧脸和合同色差同框位置。"
            ),
        },
        {
            "layout_id": "layout_会议室",
            "title": "会议室反击",
            "duration_seconds": 8,
            "transition": "硬切到公开对峙",
            "dialogue": ["林舟：这份合同被换过，时间线就在这里。"],
            "role_ids": ["role_林舟", "role_赵启"],
            "role_appearance_ids": ["role_林舟_appearance_base", "role_赵启_appearance_base"],
            "role_audio_ids": ["role_林舟_audio_normal"],
            "prop_ids": ["prop_邮件截图"],
            "video_prompt": (
                "当前片段中相机位于会议桌短边，35mm 中广角，贴着桌面低位观察；"
                "镜头横移扫过被调包的合同和会议水杯；林舟站在投影屏左侧，说："
                "“这份合同被换过，时间线就在这里。”他说话时口型清晰匹配这句台词，"
                "赵启坐在右侧阴影里的身体从前倾慢慢后撤。"
            ),
        },
        {
            "layout_id": "layout_会议室",
            "title": "证据压近",
            "duration_seconds": 6,
            "transition": "硬切到证据特写",
            "dialogue": [],
            "role_ids": ["role_林舟"],
            "role_appearance_ids": ["role_林舟_appearance_base"],
            "role_audio_ids": [],
            "prop_ids": ["prop_邮件截图", "prop_被调包的合同"],
            "video_prompt": (
                "当前片段以合同和邮件截图为中心，镜头从林舟手边的合同关键页缓慢推近，"
                "让附件时间线、纸张色差和装订孔错位依次成为画面焦点。"
            ),
        },
    ]
    shots: list[StoryboardShot] = []
    for index, template in enumerate(templates[:shot_count], start=1):
        shots.append(
            StoryboardShot(
                shot_id=f"{episode_key}_shot_{index:03d}",
                index=index,
                start_frame_source="new_reference_frame",
                start_frame_inheritance_reason="本片段按当前剧情重新建立画面。",
                **template,
            )
        )
    return StoryboardEpisodeOutput(episode_key=episode_key, shots=shots)


def write_fake_storyboard_episode(workflow: Any, project_dir: Path, episode_key: str, *, shot_count: int = 2) -> None:
    workflow._save_storyboard_episode(
        project_dir,
        fake_storyboard_episode(episode_key, shot_count=shot_count),
    )
