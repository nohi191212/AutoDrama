from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import StoryboardPromptOutput


def main() -> None:
    new_payload = {
        "storyboards": [
            {
                "episode_key": "episode_001",
                "clips": [
                    {
                        "clip_id": "episode_001_clip_001",
                        "clip_title": "Clip 001",
                        "clip_duration_hint": "10s",
                        "clip_text": "林舟进入办公室。",
                        "duration_seconds": 15,
                        "role_ids": ["role_a"],
                        "layout_ids": ["layout_a"],
                        "prop_ids": [],
                        "camera_shots": [
                            {
                                "camera_shot_id": "Camera Shot 1",
                                "time_range": "0-5秒",
                                "description": "角色进入办公室。",
                            }
                        ],
                        "panel_plan": {f"P{index:02d}": "Camera Shot 1" for index in range(1, 13)},
                        "video_prompt": (
                            "Camera Shot 1（0-5秒）：连续推进。"
                            "十二宫格面板规划 P01 P02 P03 P04 P05 P06 P07 P08 P09 P10 P11 P12 对应该镜头。"
                        ),
                        "negative_prompt": "无字幕。",
                    }
                ],
            }
        ]
    }
    output = StoryboardPromptOutput.model_validate(new_payload)
    episode = output.storyboards[0]
    if episode.clips[0].clip_id != "episode_001_clip_001":
        raise AssertionError("new clips payload did not load")
    dumped = output.model_dump(mode="json")
    if "clips" not in dumped["storyboards"][0] or "shots" in dumped["storyboards"][0]:
        raise AssertionError("new storyboard prompt output should serialize clips, not shots")

    legacy_payload = {
        "storyboards": [
            {
                "episode_key": "episode_001",
                "shots": [
                    {
                        "shot_id": "episode_001_shot_001",
                        "duration_seconds": 15,
                        "role_ids": ["role_a"],
                        "layout_ids": ["layout_a"],
                        "prop_ids": [],
                        "video_prompt": "legacy",
                    }
                ],
            }
        ]
    }
    legacy = StoryboardPromptOutput.model_validate(legacy_payload)
    if legacy.storyboards[0].clips[0].clip_id != "episode_001_shot_001":
        raise AssertionError("legacy shots payload did not map to clips")
    if legacy.storyboards[0].shots[0].shot_id != "episode_001_shot_001":
        raise AssertionError("legacy shots property compatibility failed")

    tmp_dir = ROOT / ".tmp"
    tmp_dir.mkdir(exist_ok=True)
    (tmp_dir / "storyboard_clip_schema_smoke.ok").write_text("ok\n", encoding="utf-8")
    print("storyboard_clip_schema_smoke: ok")


if __name__ == "__main__":
    main()
