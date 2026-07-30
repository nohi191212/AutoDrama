"""Focused checks for the structured shot, dialogue, and overlay contract."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (
    DialogueLine,
    OverlayTextSpec,
    Role,
    RoleAudio,
    SemanticProvenance,
    ShotEntityState,
    ShotManifestItem,
    ShotPlanItem,
)
from autodrama.utils.video_prompts import sanitize_video_prompt_text
from autodrama.workflows.nodes.shot_asset_nodes import ShotKeyframePromptNode
from autodrama.workflows.editing import EditingWorkflow
from autodrama.workflows.pregen import PregenWorkflow


PROVENANCE = SemanticProvenance(
    source="model",
    evidence=["fixture"],
    confidence=1.0,
    model="fake",
)


def _line(*, emotion: str, delivery_mode: str = "on_screen") -> DialogueLine:
    return DialogueLine(
        line_index=1,
        speaker_role_id="role_Rose",
        speaker_name="Rose",
        text="冷静看完证据，沉默三秒。",
        emotion=emotion,
        intensity=0.5,
        delivery_mode=delivery_mode,
        source_text="Rose: 冷静看完证据，沉默三秒。",
        provenance=PROVENANCE,
    )


def _shot(*, line: DialogueLine, overlay: OverlayTextSpec | None = None) -> ShotPlanItem:
    return ShotPlanItem(
        shot_id="episode_001_clip_001_shot_001",
        clip_id="episode_001_clip_001",
        clip_index=1,
        shot_index_in_clip=1,
        episode_shot_index=1,
        shot_description="Rose说出带引号的对白“到此为止”。",
        narrative_angle="平视观察人物反应。",
        opening_state="Rose位于画面中景。",
        ref_ids=["layout_room", "rose_roleboard"],
        video_prompt="横幅广告留在背景，画面参数由 provider 单独接收。",
        duration_seconds=6,
        entity_states=[ShotEntityState(entity_id="role_Rose", pose="站立")],
        dialogue_lines=[line],
        overlay_text_spec=overlay,
        allowed_props=[],
    )


def main() -> None:
    normal = _shot(line=_line(emotion="normal"))
    if normal.dialogue != ["冷静看完证据，沉默三秒。"]:
        raise AssertionError("legacy dialogue display must be derived from dialogue_lines")
    if normal.dialogue_lines[0].delivery_mode != "on_screen":
        raise AssertionError("a role name containing 'os' must not become voiceover")
    if normal.dialogue_lines[0].emotion != "normal":
        raise AssertionError("dialogue words must not override explicit normal emotion")
    if normal.overlay_text_spec is not None:
        raise AssertionError("quoted dialogue must not create an overlay")
    if ShotKeyframePromptNode._requires_clean_plate(normal):
        raise AssertionError("a shot without overlay must not request a clean plate")

    role = Role(
        id="role_Rose",
        name="Rose",
        intro="fixture",
        audio={
            "normal": RoleAudio(id="rose_normal", role_id="role_Rose", emotion="normal"),
            "angry": RoleAudio(id="rose_angry", role_id="role_Rose", emotion="angry"),
        },
    )
    normal_audio = PregenWorkflow._shot_dialogue_role_audio(None, role, "normal")
    angry_audio = PregenWorkflow._shot_dialogue_role_audio(None, role, "angry")
    if normal_audio is None or angry_audio is None or normal_audio.id == angry_audio.id:
        raise AssertionError("explicit emotions must select distinct performance assets")

    postproduction = _shot(
        line=_line(emotion="angry"),
        overlay=OverlayTextSpec(
            text="第三章",
            render_mode="postproduction",
            placement_hint="画面下方",
            start_seconds=1.0,
            end_seconds=4.0,
            provenance=PROVENANCE,
        ),
    )
    if not ShotKeyframePromptNode._requires_clean_plate(postproduction):
        raise AssertionError("postproduction overlay must request a clean plate")
    manifest_shot = ShotManifestItem(
        shot_id=postproduction.shot_id,
        index=1,
        title=postproduction.shot_description,
        duration_seconds=postproduction.duration_seconds,
        video_prompt=postproduction.video_prompt,
        dialogue_lines=postproduction.dialogue_lines,
        text_overlay_spec=postproduction.overlay_text_spec,
    )
    overlay_cues = EditingWorkflow._build_overlay_cues(
        manifest_shot,
        shot_start=10.0,
        shot_duration=6.0,
        existing_count=0,
    )
    if len(overlay_cues) != 1 or overlay_cues[0].cue_type != "overlay":
        raise AssertionError("postproduction overlay must enter the editing overlay layer")

    in_scene = _shot(
        line=_line(emotion="normal"),
        overlay=OverlayTextSpec(
            text="出口",
            render_mode="in_scene",
            provenance=PROVENANCE,
        ),
    )
    if ShotKeyframePromptNode._requires_clean_plate(in_scene):
        raise AssertionError("in-scene text must not enter postproduction overlay flow")
    manifest_shot.text_overlay_spec = in_scene.overlay_text_spec
    if EditingWorkflow._build_overlay_cues(manifest_shot, 0.0, 6.0, 0) != []:
        raise AssertionError("in-scene text must not create an editing overlay layer")

    prompt = "保留横幅广告和 9:16 构图 <<<image_1>>>"
    sanitized = sanitize_video_prompt_text(prompt)
    if sanitized != "保留横幅广告和 9:16 构图":
        raise AssertionError("only exact provider placeholders may be removed")
    if (
        normal.entity_states[0].pose != "站立"
        or normal.shot_description != "Rose说出带引号的对白“到此为止”。"
    ):
        raise AssertionError("current-shot state must remain separate from shot identity text")

    print("structured_shot_contract_smoke: ok")


if __name__ == "__main__":
    main()
