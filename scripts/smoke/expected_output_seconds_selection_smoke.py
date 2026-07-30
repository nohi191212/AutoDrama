from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import Settings, load_settings
from autodrama.core.model_catalog import NodeModelSettings
from autodrama.core.schemas import (
    ClipShotPlan,
    ClipToShotsEpisodeOutput,
    ProjectState,
    ScriptBundle,
    ShotPlanItem,
)
from autodrama.repositories.project_repo import ProjectRepository
from autodrama.workflows.nodes.shot_asset_nodes import ShotAssetNodeBase
from autodrama.workflows.output_scope import (
    ExpectedOutputSelectionOutput,
    clear_pending_expected_output_shot_ids,
    configured_episode_output_selection,
    pending_expected_output_shot_ids,
    select_expected_output_prefix,
    synchronize_expected_output_scope,
)


def build_plan() -> ClipToShotsEpisodeOutput:
    clip_shot_durations = [[4, 4], [7], [10], [12], [9], [8], [5, 6], [5]]
    clips: list[ClipShotPlan] = []
    episode_shot_index = 0
    for clip_index, durations in enumerate(clip_shot_durations, start=1):
        clip_id = f"episode_001_clip_{clip_index:03d}"
        shots: list[ShotPlanItem] = []
        for shot_index, duration in enumerate(durations, start=1):
            episode_shot_index += 1
            shot_id = f"{clip_id}_shot_{shot_index:03d}"
            shots.append(
                ShotPlanItem(
                    shot_id=shot_id,
                    clip_id=clip_id,
                    clip_index=clip_index,
                    shot_index_in_clip=shot_index,
                    episode_shot_index=episode_shot_index,
                    shot_description=shot_id,
                    narrative_angle="deterministic smoke angle",
                    opening_state="deterministic smoke opening",
                    video_prompt="deterministic smoke motion",
                    duration_seconds=duration,
                )
            )
        clips.append(ClipShotPlan(clip_id=clip_id, clip_index=clip_index, shots=shots))
    return ClipToShotsEpisodeOutput(
        episode_key="episode_001",
        clips=clips,
        target_duration_seconds=70,
        total_duration_seconds=70,
        duration_gate_status="accepted",
    )


def build_node(repo: ProjectRepository, selectors: set[str]) -> ShotAssetNodeBase:
    workflow = type("Workflow", (), {"_active_shot_selectors": selectors})()
    return ShotAssetNodeBase(
        workflow=workflow,
        repo=repo,
        layout=repo.layout,
        router=None,
        script_service=None,
        asset_service=None,
        script_contents=None,
        prop_designs=None,
        media_store=None,
        logger=None,
    )


def main() -> None:
    plan = build_plan()
    pilot = select_expected_output_prefix(plan, 60)
    assert pilot.selected_clip_ids == [
        f"episode_001_clip_{index:03d}" for index in range(1, 8)
    ]
    assert pilot.planned_output_seconds == 65
    assert pilot.overshoot_seconds == 5
    assert pilot.first_excluded_clip_id == "episode_001_clip_008"
    assert pilot.target_reached
    assert pilot.selected_shot_ids[-2:] == [
        "episode_001_clip_007_shot_001",
        "episode_001_clip_007_shot_002",
    ], "the boundary clip must remain whole"

    full = select_expected_output_prefix(plan, -1)
    assert full.mode == "all"
    assert full.selected_clip_count == full.total_clip_count == 8
    assert full.selected_shot_count == full.total_shot_count == 10
    assert full.planned_output_seconds == 70

    longer_than_episode = select_expected_output_prefix(plan, 90)
    assert longer_than_episode.selected_clip_count == 8
    assert longer_than_episode.planned_output_seconds == 70
    assert not longer_than_episode.target_reached

    assert Settings().generation.expected_output_seconds == -1
    for invalid in (0, -2, True, 60.0, "60"):
        try:
            Settings.model_validate(
                {"generation": {"expected_output_seconds": invalid}}
            )
        except Exception:
            pass
        else:
            raise AssertionError(
                f"invalid expected_output_seconds must be rejected: {invalid!r}"
            )

    target_settings = load_settings(ROOT / "config.saodi_bashinian_terra_image2.yaml")
    assert target_settings.generation.expected_output_seconds == 60
    assert "expected_output" not in NodeModelSettings.model_fields
    example_settings = load_settings(ROOT / "config.yaml.example")
    assert example_settings.generation.expected_output_seconds == -1

    prompt_text = (
        ROOT
        / "autodrama"
        / "src"
        / "autodrama"
        / "prompts"
        / "clip_segment"
        / "default.md"
    ).read_text(encoding="utf-8")
    assert "expected_output" not in prompt_text
    assert "clip_count_instruction" not in prompt_text

    work_dir = ROOT / ".tmp" / "expected_output_seconds_selection"
    if work_dir.exists():
        shutil.rmtree(work_dir)
    project_dir = work_dir / "project"
    project_dir.mkdir(parents=True)
    repo = ProjectRepository(
        Settings.model_validate(
            {
                "output": {"root_dir": str(work_dir / "outputs")},
                "generation": {"expected_output_seconds": 60},
            }
        )
    )
    persisted = configured_episode_output_selection(repo, project_dir, plan)
    manifest_path = repo.layout.expected_output_selection_path(project_dir)
    manifest = ExpectedOutputSelectionOutput.model_validate_json(
        manifest_path.read_text(encoding="utf-8")
    )
    assert manifest.expected_output_seconds == 60
    assert manifest.episodes[0] == persisted

    automatic = build_node(repo, set())._selected_shots(project_dir, plan)
    assert [shot.shot_id for shot in automatic] == persisted.selected_shot_ids

    explicit = build_node(repo, {"10"})._selected_shots(project_dir, plan)
    assert [shot.shot_id for shot in explicit] == [
        "episode_001_clip_008_shot_001"
    ], "explicit --shots must override expected_output_seconds"

    plan_path = repo.layout.node_episode_output_path(
        project_dir,
        "clip_to_shots",
        plan.episode_key,
    )
    repo.write_json(plan_path, plan)
    old_repo = ProjectRepository(
        Settings.model_validate(
            {
                "output": {"root_dir": str(work_dir / "outputs")},
                "generation": {"expected_output_seconds": 30},
            }
        )
    )
    old_selection = configured_episode_output_selection(old_repo, project_dir, plan)
    state = ProjectState(
        project_id="scope-transition-smoke",
        title="scope transition smoke",
        raw_script="smoke",
        current_node="postgen_final_audit",
        completed_nodes=[
            "clip_segment",
            "clip_to_shots",
            "layout_to_background_prompt",
            "shot_background_image_generation",
            "shot_background_image_audit",
            "shot_keyframe_prompt",
            "shot_keyframe_image_generation",
            "shot_keyframe_image_audit",
            "shot_manifest_generation",
            "shot_video_generation",
            "shot_video_audit",
            "dynamic_asset_solidification",
            "postgen_source_collect",
            "postgen_final_audit",
        ],
        script=ScriptBundle(
            raw_script="smoke",
            episode_outlines={"episode_001": "smoke"},
        ),
        metadata={"expected_output_seconds": 30},
    )
    old_repo.save_state(project_dir, state)
    old_repo.write_json(
        project_dir / "generation_checklist.json",
        {
            "project_id": state.project_id,
            "episodes": [
                {
                    "episode_key": "episode_001",
                    "generate": False,
                    "generation_status": "completed",
                }
            ],
        },
    )
    full_repo = ProjectRepository(
        Settings.model_validate(
            {
                "output": {"root_dir": str(work_dir / "outputs")},
                "generation": {"expected_output_seconds": -1},
            }
        )
    )
    transition = synchronize_expected_output_scope(
        full_repo,
        project_dir,
        state,
        ["episode_001"],
    )
    expected_added = [
        shot_id
        for shot_id in full.selected_shot_ids
        if shot_id not in set(old_selection.selected_shot_ids)
    ]
    assert transition.config_changed and transition.selection_changed
    assert transition.added_shot_ids_by_episode["episode_001"] == expected_added
    assert "clip_segment" in state.completed_nodes
    assert "clip_to_shots" in state.completed_nodes
    assert "shot_keyframe_image_generation" not in state.completed_nodes
    assert "postgen_final_audit" not in state.completed_nodes
    assert pending_expected_output_shot_ids(state) == {
        "episode_001": expected_added
    }
    checklist = json.loads(
        (project_dir / "generation_checklist.json").read_text(encoding="utf-8")
    )
    assert checklist["episodes"][0]["generate"] is True
    assert checklist["episodes"][0]["generation_status"] == "pending"
    clear_pending_expected_output_shot_ids(state, ["episode_001"])
    assert not pending_expected_output_shot_ids(state)

    (work_dir / "expected_output_seconds_selection_smoke.ok").write_text(
        json.dumps(persisted.model_dump(mode="json"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print("expected_output_seconds_selection_smoke: ok")


if __name__ == "__main__":
    main()
