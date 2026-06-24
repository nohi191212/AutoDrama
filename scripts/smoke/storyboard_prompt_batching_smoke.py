from __future__ import annotations

import sys
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import Settings
from autodrama.core.schemas import (
    ClipSegment,
    Layout,
    ProjectState,
    Role,
    RoleAppearance,
    ScriptBundle,
    StoryboardPromptOutput,
)
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.script_content_repo import ScriptContentRepository
from autodrama.utils.prompts import PromptStore
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardPromptNode


def _clip_payload(episode_key: str, index: int) -> dict[str, object]:
    return {
        "clip_id": f"{episode_key}_clip_{index:03d}",
        "clip_title": f"Clip {index:03d}",
        "clip_duration_hint": "10s",
        "clip_text": f"第 {index} 段。",
        "duration_seconds": 10,
        "role_ids": ["role_江未晞"],
        "layout_ids": ["layout_古老殿宇"],
        "prop_ids": [],
        "camera_shots": [
            {"camera_shot_id": "CS1", "time_range": "0-5秒", "description": "连续动作开始。"},
            {"camera_shot_id": "CS2", "time_range": "5-10秒", "description": "连续动作完成。"},
        ],
        "panel_plan": {f"P{panel:02d}": "Camera Shot 1" for panel in range(1, 13)},
        "video_prompt": (
            "Camera Shot 1（0-5秒）：江未晞在古老殿宇中观察。"
            "Camera Shot 2（5-10秒）：她继续行动并形成情绪推进。"
            "十二宫格面板规划 P01 P02 P03 P04 P05 P06 P07 P08 P09 P10 P11 P12。"
        ),
        "negative_prompt": "禁止字幕、水印、logo。",
    }


def main() -> None:
    tmp_project = ROOT / ".tmp" / "storyboard_prompt_batching" / "project"
    role_image = tmp_project / "assets" / "images" / "roles" / "role_jiang.png"
    layout_image = tmp_project / "assets" / "images" / "layouts" / "layout_hall.png"
    role_image.parent.mkdir(parents=True, exist_ok=True)
    layout_image.parent.mkdir(parents=True, exist_ok=True)
    role_image.write_bytes(b"role image placeholder")
    layout_image.write_bytes(b"layout image placeholder")

    settings = Settings()
    layout = ProjectLayout(settings)
    node = object.__new__(StoryboardPromptNode)
    node.layout = layout
    node.script_contents = ScriptContentRepository.__new__(ScriptContentRepository)
    node.script_contents.layout = layout
    node.logger = None

    clips = {
        str(index): ClipSegment(
            text=f"第 {index} 段，江未晞在古老殿宇行动。",
            role_names=["江未晞"],
            layout_names=["古老殿宇"],
            prop_names=[],
        )
        for index in range(1, 18)
    }
    batches = node._clip_segment_batches(clips)
    if [len(batch) for batch in batches] != [8, 8, 1]:
        raise AssertionError("storyboard_prompt batch sizes should be 8, 8, 1")
    if list(batches[1]) != [str(index) for index in range(9, 17)]:
        raise AssertionError("second storyboard_prompt batch should preserve original clip keys 9-16")

    state = ProjectState(
        project_id="storyboard_prompt_batching",
        title="Smoke",
        raw_script="原始故事",
        script=ScriptBundle(
            raw_script="原始故事",
            novel_full={
                "episode_001": "上一集全文",
                "episode_002": "本集全文",
                "episode_003": "下一集全文",
            },
            novel_extract={"episode_002": "本集摘要"},
        ),
        roles={
            "role_江未晞": Role(
                id="role_江未晞",
                name="江未晞",
                intro="主角",
                episode_keys=["episode_002"],
                appearances={
                    "base": RoleAppearance(
                        id="appearance_江未晞_base",
                        role_id="role_江未晞",
                        asset_path="assets/images/roles/role_jiang.png",
                    )
                },
            )
        },
        layouts={
            "layout_古老殿宇": Layout(
                id="layout_古老殿宇",
                name="古老殿宇",
                desc="残破殿宇",
                prompt="残破殿宇",
                episode_keys=["episode_002"],
                asset_path="assets/images/layouts/layout_hall.png",
            )
        },
    )

    role_ids = node._role_ids_for_batch(state, episode_key="episode_002", batch_clips=batches[1])
    layout_ids = node._layout_ids_for_batch(state, episode_key="episode_002", batch_clips=batches[1])
    if role_ids != ["role_江未晞"]:
        raise AssertionError(f"unexpected role ids for batch: {role_ids}")
    if layout_ids != ["layout_古老殿宇"]:
        raise AssertionError(f"unexpected layout ids for batch: {layout_ids}")

    refs, ref_context = node._storyboard_prompt_reference_refs(
        tmp_project,
        state,
        role_ids=role_ids,
        layout_ids=layout_ids,
    )
    if len(refs) != 2:
        raise AssertionError("storyboard_prompt should attach roleboard and layout image refs")
    if [item["input_slot"] for item in ref_context] != ["image_1", "image_2"]:
        raise AssertionError("reference image context should expose stable image slots")

    full_context = node._episode_window_full_context(
        tmp_project,
        state,
        "episode_002",
        ["episode_001", "episode_002", "episode_003"],
    )
    if list(full_context) != ["episode_001", "episode_002", "episode_003"]:
        raise AssertionError("storyboard_prompt should include previous/current/next full episode context")

    batch_output = StoryboardPromptOutput.model_validate(
        {
            "storyboards": [
                {
                    "episode_key": "episode_002",
                    "clips": [_clip_payload("episode_002", index) for index in range(9, 17)],
                }
            ]
        }
    )
    validated = node.validate_storyboard_prompt_output(
        batch_output,
        expected_episode_keys=["episode_002"],
        expected_clip_segments={"episode_002": batches[1]},
    )
    if validated.storyboards[0].clips[0].clip_id != "episode_002_clip_009":
        raise AssertionError("partial storyboard_prompt batch should preserve original clip index")

    rendered = PromptStore().render(
        "storyboard_prompt",
        title=state.title,
        episode_keys="episode_002",
        clip_batch="episode_002 clip_segment keys 9-16; batch 2/3; max 8 clips per call",
        clip_count=node._format_json({"episode_002": 8}),
        shot_count=node._format_json({"episode_002": 8}),
        clip_count_by_episode=node._format_json({"episode_002": 8}),
        total_clip_count_by_episode=node._format_json({"episode_002": 17}),
        final_aspect_ratio="9:16",
        storyboard_panel_count=node.STORYBOARD_PANEL_COUNT,
        storyboard_grid=node.storyboard_grid(),
        storyboard_panel_aspect_ratio=node.storyboard_panel_aspect_ratio(),
        raw_script=state.raw_script,
        novel_extract=node._format_json({"episode_002": "本集摘要"}),
        novel_full=node._format_json(full_context),
        director_prep="story_core/worldview/visual_tone",
        roleboard_context=node._format_json(node._roleboard_context_for_ids(tmp_project, state, role_ids)),
        layout_context=node._format_json(node._layout_context_for_ids(state, layout_ids)),
        prop_context=node._format_json([]),
        reference_image_context=node._format_json(ref_context),
        clip_segments=node._format_json({"episode_002": batches[1]}),
    )
    if re.search(r"\{\{[A-Za-z_][A-Za-z0-9_]*\}\}", rendered):
        raise AssertionError("storyboard_prompt render left unresolved template variables")

    marker = ROOT / ".tmp" / "storyboard_prompt_batching_smoke.ok"
    marker.write_text("ok\n", encoding="utf-8")
    print("storyboard_prompt_batching_smoke: ok")


if __name__ == "__main__":
    main()
