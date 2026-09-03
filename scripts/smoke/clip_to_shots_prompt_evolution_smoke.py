from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    DialogueLine,
    OverlayTextSpec,
    SemanticProvenance,
    ShotCameraSpecification,
    ShotCharacterPlacement,
    ShotEntityState,
)
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.json_utils import parse_json_object  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402


LAB = ROOT / ".tmp" / "clip-to-shots-template-evolution-20260809"
CASES_PATH = LAB / "cases.json"
RUBRIC_PATH = ROOT / ".assets" / "storyboarding" / "clip_to_shots-rubric-v1.json"
CONFIG_PATH = ROOT / "saodi.yaml"
GENERATOR_CALL_LIMIT = 32
JUDGE_CALL_LIMIT = 24
JUDGE_ATTEMPTS_PER_CASE = 3

CANDIDATE_SETS = {
    "cycle01": {
        "A": LAB / "candidates" / "cycle01" / "A-incumbent-rich.md",
        "B": LAB / "candidates" / "cycle01" / "B-professional-rich.md",
        "C": LAB / "candidates" / "cycle01" / "C-professional-lean.md",
    },
    "cycle02": {
        "C": LAB / "candidates" / "cycle01" / "C-professional-lean.md",
        "D": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
    },
    "cycle03": {
        "D": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
        "E": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
    },
    "cycle04": {
        "D": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
        "F": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
    },
    "cycle05": {
        "D": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
        "G": LAB / "candidates" / "cycle02" / "D-listener-aware-lean.md",
    },
}
ACTIVE_CYCLE = "cycle01"
CANDIDATES = CANDIDATE_SETS[ACTIVE_CYCLE]


def configure_cycle(cycle: str) -> None:
    global ACTIVE_CYCLE, CANDIDATES
    ACTIVE_CYCLE = cycle
    CANDIDATES = CANDIDATE_SETS[cycle]

DIALOGUE_RE = re.compile(
    r"(?P<speaker>[\u4e00-\u9fffA-Za-z0-9_]+)"
    r"(?:（(?P<performance>[^）]+)）)?\s*：\s*"
    r"[「“\"](?P<text>.*?)[」”\"]"
)
INTERNAL_CUT_RE = re.compile(
    r"(?i)(?:\bcut\s+to\b|\bthen\s+cut\b|\breverse\s+angle\b|\bmontage\b|"
    r"\bswitch(?:es)?\s+to\b|切到|切为|转到另一|反打|蒙太奇)"
)


class RichModelItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    shot_description: str
    narrative_angle: str
    opening_state: str
    character_placements: list[ShotCharacterPlacement] = Field(default_factory=list)
    camera: ShotCameraSpecification
    ref_ids: list[str] = Field(default_factory=list)
    video_prompt: str
    duration_seconds: int = Field(ge=1, le=15)
    entity_states: list[ShotEntityState] = Field(default_factory=list)
    dialogue_lines: list[DialogueLine] = Field(default_factory=list)
    overlay_text_spec: OverlayTextSpec | None = None
    allowed_props: list[str] = Field(default_factory=list)


class RichModelOutput(RootModel[dict[str, RichModelItem]]):
    pass


class LeanCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str
    scene_position: str
    screen_position: str
    depth_layer: Literal["foreground", "midground", "background"]
    body_facing: str
    gaze_target: str | None = None
    opening_pose: str
    action: str
    emotion: str
    held_prop_ids: list[str] = Field(default_factory=list)


class LeanCamera(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: str
    target: str
    shot_size: Literal[
        "extreme_wide",
        "wide",
        "full",
        "medium_wide",
        "medium",
        "medium_close",
        "close",
        "extreme_close",
        "insert",
    ]
    angle: Literal["eye_level", "high", "low", "overhead", "ground_level", "canted"]
    perspective: Literal["objective", "over_shoulder", "point_of_view"]
    movement: str


class LeanShot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    camera: LeanCamera
    characters: list[LeanCharacter] = Field(default_factory=list)
    visible_prop_ids: list[str] = Field(default_factory=list)
    action: str
    on_screen_text: str | None = None
    duration_seconds: int = Field(ge=1, le=15)


class LeanModelOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shots: list[LeanShot] = Field(min_length=1)


class JudgeDimensionAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension_id: str
    score: float = Field(ge=0, le=10)
    evidence: str
    defect: str


class JudgeCandidateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failed_gates: list[str] = Field(default_factory=list)
    dimensions: list[JudgeDimensionAssessment]
    summary: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = value if value.endswith("\n") else value + "\n"
    path.write_text(normalized, encoding="utf-8", newline="\n")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def render_template(template: str, variables: dict[str, str]) -> str:
    expected = set(re.findall(r"\{\{([a-zA-Z0-9_]+)\}\}", template))
    missing = expected.difference(variables)
    extra = set(variables).difference(expected)
    if missing or extra:
        raise ValueError(f"template variables mismatch: missing={sorted(missing)} extra={sorted(extra)}")
    output = template
    for key, value in variables.items():
        output = output.replace("{{" + key + "}}", value)
    if "{{" in output or "}}" in output:
        raise ValueError("unresolved template variable")
    return output.strip() + "\n"


def load_inputs() -> tuple[dict[str, Any], dict[str, Any]]:
    cases = read_json(CASES_PATH)
    rubric = read_json(RUBRIC_PATH)
    if cases.get("schema_version") != 1 or rubric.get("version") != 1:
        raise ValueError("unsupported lab input version")
    for scene in cases["scene_anchors"].values():
        path = ROOT / scene["path"]
        if not path.is_file() or sha256(path) != scene["sha256"]:
            raise ValueError(f"scene anchor changed or missing: {path}")
    return cases, rubric


def asset_rows(
    case: dict[str, Any],
    payload: dict[str, Any],
    *,
    semantic: bool,
    concise_roles: bool = False,
) -> str:
    rows: list[str] = []
    for entity_id in [*case.get("role_ids", []), *case.get("prop_ids", [])]:
        entity = payload["assets"][entity_id]
        if semantic:
            if concise_roles and entity["kind"] == "role":
                rows.append(f"{entity_id}: role {entity['name']}")
            else:
                rows.append(f"{entity_id}: {entity['kind']} {entity['name']}; {entity['description']}")
        elif entity["kind"] == "role":
            rows.append(
                f"{entity['reference_id']}: {entity['description']} "
                f"[role_id={entity_id}; appearance_id={entity['appearance_id']}]"
            )
        else:
            rows.append(
                f"{entity['reference_id']}: {entity['description']} [prop_id={entity_id}]"
            )
    return "\n".join(rows) or "(none)"


def dialogue_catalog(case: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
    names_to_ids = {
        payload["assets"][role_id]["name"]: role_id for role_id in case.get("role_ids", [])
    }
    rows: list[dict[str, Any]] = []
    for index, match in enumerate(DIALOGUE_RE.finditer(case["clip_text"]), start=1):
        performance = str(match.group("performance") or "").strip()
        rows.append(
            {
                "id": f"D{index:02d}",
                "speaker_name": match.group("speaker"),
                "speaker_role_id": names_to_ids.get(match.group("speaker")),
                "performance": performance,
                "text": match.group("text").strip(),
                "source_text": match.group(0),
                "voiceover": performance.strip().upper() in {"VO", "V.O.", "旁白"},
            }
        )
    return rows


def dialogue_rows(catalog: list[dict[str, Any]]) -> str:
    if not catalog:
        return "(none)"
    return "\n".join(
        f"{row['id']}: speaker={row['speaker_role_id'] or 'unknown'}; "
        f"performance={row['performance'] or 'unspecified'}; text={row['text']}"
        for row in catalog
    )


def policy_neutral_visual_wording(text: str) -> str:
    return text.replace(
        "指尖拂过花瓣，柔软的触感带着细微湿意；再捏住一片叶子，叶脉清晰，边缘划过皮肤时有轻轻的痒。"
        "她猛地缩回手，又忍不住再次触碰",
        "指尖让柔软花瓣微微弯曲，一滴露水沿花瓣滚动；她再捏起一片叶子，看清叶脉，"
        "叶缘轻扫指尖带来细微痒感。她条件反射地收手，迟疑后又伸手确认一次",
    )


def candidate_prompt(
    candidate_id: str,
    case: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[str, list[dict[str, Any]]]:
    template = CANDIDATES[candidate_id].read_text(encoding="utf-8")
    scene = payload["scene_anchors"][case["scene_id"]]
    foreground = max(0, int(case["reference_budget"]) - 1)
    catalog = dialogue_catalog(case, payload)
    common = {
        "available_seconds": str(case["available_seconds"]),
        "scene_description": scene["description"],
        "previous_context": case["previous_context"],
        "clip_text": policy_neutral_visual_wording(case["clip_text"])
        if candidate_id in {"F", "G"}
        else case["clip_text"],
        "next_context": case["next_context"],
        "foreground_reference_budget": str(foreground),
    }
    if candidate_id in {"A", "B"}:
        variables = {
            **common,
            "scene_id": case["scene_id"],
            "asset_index": asset_rows(case, payload, semantic=False),
            "reference_budget": str(case["reference_budget"]),
        }
    else:
        variables = {
            **common,
            "entity_index": asset_rows(
                case,
                payload,
                semantic=True,
                concise_roles=candidate_id in {"E", "G"},
            ),
        }
    return render_template(template, variables), catalog


def emotion_value(performance: str) -> str:
    value = performance.casefold()
    if any(token in value for token in ("怒", "angry", "愤")):
        return "angry"
    if any(token in value for token in ("悲", "sad", "哭")):
        return "sad"
    if any(token in value for token in ("喜", "笑", "happy", "兴奋")):
        return "happy"
    if any(token in value for token in ("紧张", "恐", "震惊", "惊", "tense")):
        return "tense"
    if any(token in value for token in ("耳语", "低声", "whisper")):
        return "whisper"
    if not value or value in {"平静", "正常", "normal"}:
        return "normal"
    return "other"


def focal_length(shot_size: str) -> float:
    return {
        "extreme_wide": 20.0,
        "wide": 28.0,
        "full": 35.0,
        "medium_wide": 40.0,
        "medium": 50.0,
        "medium_close": 65.0,
        "close": 85.0,
        "extreme_close": 135.0,
        "insert": 100.0,
    }[shot_size]


def angle_defaults(angle: str) -> tuple[float, float]:
    return {
        "eye_level": (1.6, 0.0),
        "high": (2.6, -15.0),
        "low": (0.75, 12.0),
        "overhead": (6.0, -65.0),
        "ground_level": (0.25, 8.0),
        "canted": (1.6, 0.0),
    }[angle]


def compile_lean(
    output: LeanModelOutput,
    case: dict[str, Any],
    payload: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    compiled: dict[str, Any] = {}
    for index, shot in enumerate(output.shots, start=1):
        role_ids = [character.role_id for character in shot.characters]
        prop_ids = list(dict.fromkeys([*shot.visible_prop_ids, *[p for c in shot.characters for p in c.held_prop_ids]]))
        ref_ids = []
        for entity_id in [*role_ids, *prop_ids]:
            entity = payload["assets"].get(entity_id)
            if entity is not None:
                ref_ids.append(entity["reference_id"])
        reference_slots = max(0, int(case["reference_budget"]) - 1)
        refs = list(dict.fromkeys(ref_ids))[:reference_slots]

        placements = [
            ShotCharacterPlacement(
                role_id=character.role_id,
                scene_position=character.scene_position,
                screen_position=character.screen_position,
                depth_layer=character.depth_layer,
                body_facing=character.body_facing,
                gaze_target=character.gaze_target,
                pose=character.opening_pose,
            ).model_dump(mode="json")
            for character in shot.characters
        ]
        entity_states = []
        for character in shot.characters:
            entity = payload["assets"].get(character.role_id) or {}
            entity_states.append(
                ShotEntityState(
                    entity_id=character.role_id,
                    appearance_id=entity.get("appearance_id"),
                    pose=character.opening_pose,
                    emotion=character.emotion,
                    held_props=character.held_prop_ids,
                ).model_dump(mode="json")
            )

        dialogues: list[dict[str, Any]] = []

        focal = focal_length(shot.camera.shot_size)
        fov = math.degrees(2.0 * math.atan(36.0 / (2.0 * focal)))
        height, pitch = angle_defaults(shot.camera.angle)
        angle_text = f"{shot.camera.angle} {shot.camera.perspective}"
        camera = ShotCameraSpecification(
            scene_position=shot.camera.position,
            target=shot.camera.target,
            shooting_angle=angle_text,
            shot_size=shot.camera.shot_size,
            camera_height_m=height,
            pitch_degrees=pitch,
            field_of_view_degrees=round(fov, 2),
            focal_length_mm=focal,
            movement=shot.camera.movement,
        ).model_dump(mode="json")

        opening = "; ".join(
            f"{character.role_id} at {character.scene_position}, {character.screen_position} "
            f"{character.depth_layer}, facing {character.body_facing}, gaze "
            f"{character.gaze_target or 'unfixed'}, pose {character.opening_pose}"
            for character in shot.characters
        ) or "The shot opens on the described empty scene area."
        performance = "; ".join(
            f"{character.role_id}: {character.action}; emotion {character.emotion}"
            for character in shot.characters
        )
        video_prompt = (
            f"Opening: {opening}. Action: {shot.action}. Performance: {performance or 'none'}. "
            f"Camera: {shot.camera.shot_size} {angle_text} from {shot.camera.position} toward "
            f"{shot.camera.target}; {shot.camera.movement}."
        )
        overlay = None
        if shot.on_screen_text:
            overlay = OverlayTextSpec(
                text=shot.on_screen_text,
                render_mode="postproduction",
                placement_hint=None,
                start_seconds=0.0,
                end_seconds=float(shot.duration_seconds),
                provenance=SemanticProvenance(
                    source="model",
                    evidence=[shot.on_screen_text],
                    confidence=0.8,
                ),
            ).model_dump(mode="json")
        compiled[f"shot_{index}"] = {
            "scene_id": case["scene_id"],
            "shot_description": shot.action,
            "narrative_angle": shot.purpose,
            "opening_state": opening,
            "character_placements": placements,
            "camera": camera,
            "ref_ids": refs,
            "video_prompt": video_prompt,
            "duration_seconds": shot.duration_seconds,
            "entity_states": entity_states,
            "dialogue_lines": dialogues,
            "overlay_text_spec": overlay,
            "allowed_props": prop_ids,
        }
    return compiled


def rich_dict(output: RichModelOutput) -> dict[str, Any]:
    return {key: value.model_dump(mode="json") for key, value in output.root.items()}


def ordered_shots(compiled: dict[str, Any]) -> list[dict[str, Any]]:
    parsed: list[tuple[int, dict[str, Any]]] = []
    for key, value in compiled.items():
        match = re.fullmatch(r"shot_(\d+)", key)
        if match:
            parsed.append((int(match.group(1)), value))
    parsed.sort(key=lambda item: item[0])
    return [value for _, value in parsed]


def deterministic_audit(
    compiled: dict[str, Any],
    raw: dict[str, Any],
    case: dict[str, Any],
    payload: dict[str, Any],
    catalog: list[dict[str, Any]],
) -> dict[str, Any]:
    failed: set[str] = set()
    keys = list(compiled)
    expected_keys = [f"shot_{index}" for index in range(1, len(keys) + 1)]
    if keys != expected_keys or not keys:
        failed.add("G06_executable_opening_state")
    shots = ordered_shots(compiled)
    durations = [int(shot.get("duration_seconds") or 0) for shot in shots]
    if any(duration < 1 or duration > 15 for duration in durations):
        failed.add("G02_duration_contract")
    budget = int(case["available_seconds"])
    if not shots or not (len(shots) <= budget <= len(shots) * 15):
        failed.add("G02_duration_contract")

    expected_dialogue = Counter(row["text"] for row in catalog)
    actual_dialogue = Counter(
        str(line.get("text") or "")
        for shot in shots
        for line in shot.get("dialogue_lines", [])
    )
    if expected_dialogue != actual_dialogue:
        failed.add("G01_source_fidelity")

    valid_refs = {
        payload["assets"][entity_id]["reference_id"]
        for entity_id in [*case.get("role_ids", []), *case.get("prop_ids", [])]
    }
    valid_roles = set(case.get("role_ids", []))
    valid_props = set(case.get("prop_ids", []))
    for shot in shots:
        if shot.get("scene_id") != case["scene_id"]:
            failed.add("G05_asset_and_scene_validity")
        refs = list(shot.get("ref_ids") or [])
        if any(ref not in valid_refs for ref in refs) or 1 + len(set(refs)) > int(case["reference_budget"]):
            failed.add("G05_asset_and_scene_validity")
        placements = list(shot.get("character_placements") or [])
        if any(item.get("role_id") not in valid_roles for item in placements):
            failed.add("G05_asset_and_scene_validity")
        if any(prop not in valid_props for prop in shot.get("allowed_props") or []):
            failed.add("G05_asset_and_scene_validity")
        camera = shot.get("camera") or {}
        if not placements and any(ref.startswith("role_") for ref in refs):
            failed.add("G06_executable_opening_state")
        required_camera = ("scene_position", "target", "shooting_angle", "shot_size", "movement")
        if any(not str(camera.get(field) or "").strip() for field in required_camera):
            failed.add("G06_executable_opening_state")
        searchable = "\n".join(
            str(shot.get(field) or "")
            for field in ("shot_description", "opening_state", "video_prompt")
        )
        if INTERNAL_CUT_RE.search(searchable):
            failed.add("G03_single_setup_per_shot")

    raw_shots = raw.get("shots") if isinstance(raw.get("shots"), list) else list(raw.values())
    field_count = (
        sum(len(item) for item in raw_shots if isinstance(item, dict)) / max(1, len(raw_shots))
    )
    return {
        "failed_gates": sorted(failed),
        "shot_count": len(shots),
        "duration_sum": sum(durations),
        "duration_budget": budget,
        "dialogue_expected": list(expected_dialogue.elements()),
        "dialogue_actual": list(actual_dialogue.elements()),
        "average_raw_shot_field_count": round(field_count, 2),
    }


def cycle_dir() -> Path:
    return LAB / "cycles" / f"{ACTIVE_CYCLE}-saodi"


def generation_dir() -> Path:
    return cycle_dir() / "generation"


def judge_dir() -> Path:
    return cycle_dir() / "judge"


def result_path(case_id: str, candidate_id: str) -> Path:
    return generation_dir() / case_id / f"{candidate_id}.json"


def retryable_transport_failure(path: Path) -> bool:
    if not path.is_file():
        return True
    result = read_json(path)
    if result.get("status") == "success":
        return False
    error = f"{result.get('error_type', '')}: {result.get('error', '')}".casefold()
    return any(token in error for token in ("connecterror", "sslerror", "readerror", "timeout"))


def prior_call_count(kind: str) -> int:
    return sum(
        1
        for row in read_jsonl(LAB / "ledger.jsonl")
        if row.get("event") == "call"
        and row.get("kind") == kind
        and row.get("cycle", "cycle01") == ACTIVE_CYCLE
    )


async def generate_one(
    provider: Any,
    candidate_id: str,
    case: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    output_path = result_path(case["id"], candidate_id)
    if output_path.is_file():
        append_jsonl(
            generation_dir() / "retry-history.jsonl",
            {"archived_at": utc_now(), "previous_result": read_json(output_path)},
        )
    prompt, catalog = candidate_prompt(candidate_id, case, payload)
    scene = payload["scene_anchors"][case["scene_id"]]
    scene_path = ROOT / scene["path"]
    prompt_path = generation_dir() / case["id"] / f"{candidate_id}.prompt.txt"
    write_text(prompt_path, prompt)
    schema = LeanModelOutput if candidate_id not in {"A", "B"} else RichModelOutput
    started = utc_now()
    append_jsonl(
        LAB / "ledger.jsonl",
        {
            "event": "call",
            "kind": "generator",
            "cycle": ACTIVE_CYCLE,
            "case_id": case["id"],
            "candidate_id": candidate_id,
            "at": started,
        },
    )
    try:
        output = await provider.generate_json(
            prompt,
            schema,
            temperature=0.35,
            metadata={
                "node_name": "clip_to_shots",
                "prompt_asset_name": f"evolution-{case['id']}-{candidate_id}",
                "scene_id": case["scene_id"],
            },
            refs=[AssetRef(id=case["scene_id"], type="image", path=str(scene_path))],
        )
        if candidate_id not in {"A", "B"}:
            raw = output.model_dump(mode="json")
            compiled = compile_lean(output, case, payload, catalog)
        else:
            raw = output.model_dump(mode="json")
            compiled = rich_dict(output)
        audit = deterministic_audit(compiled, raw, case, payload, catalog)
        result = {
            "status": "success",
            "case_id": case["id"],
            "candidate_id": candidate_id,
            "prompt_path": str(prompt_path.relative_to(ROOT)).replace("\\", "/"),
            "prompt_sha256": sha256(prompt_path),
            "scene_sha256": scene["sha256"],
            "raw_output": raw,
            "compiled_output": compiled,
            "deterministic_audit": audit,
            "started_at": started,
            "completed_at": utc_now(),
        }
    except Exception as exc:
        result = {
            "status": "failure",
            "case_id": case["id"],
            "candidate_id": candidate_id,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "started_at": started,
            "completed_at": utc_now(),
        }
    write_json(output_path, result)
    return result


async def run_generation(split: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("API calls require --confirm-api-calls")
    payload, _rubric = load_inputs()
    selected = [case for case in payload["cases"] if case["split"] == split]
    pending = [
        (candidate_id, case)
        for case in selected
        for candidate_id in CANDIDATES
        if retryable_transport_failure(result_path(case["id"], candidate_id))
    ]
    if prior_call_count("generator") + len(pending) > GENERATOR_CALL_LIMIT:
        raise ValueError("generator call guard would be exceeded")
    if not pending:
        return {"status": "already_complete", "split": split}
    settings = load_settings(str(CONFIG_PATH))
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(LAB)
    provider = router.text("shot", node_name="clip_to_shots")
    semaphore = asyncio.Semaphore(1)

    async def guarded(candidate_id: str, case: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await generate_one(provider, candidate_id, case, payload)

    results = await asyncio.gather(*(guarded(candidate_id, case) for candidate_id, case in pending))
    return {
        "split": split,
        "calls": len(results),
        "success": sum(result["status"] == "success" for result in results),
        "failure": sum(result["status"] != "success" for result in results),
    }


def rotated_labels(case_id: str) -> tuple[dict[str, str], list[str]]:
    order = list(CANDIDATES)
    shift = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:2], 16) % len(order)
    order = order[shift:] + order[:shift]
    labels = ["X", "Y", "Z"]
    return {label: candidate_id for label, candidate_id in zip(labels, order)}, labels


def judge_candidate_prompt(
    case: dict[str, Any],
    result: dict[str, Any],
    rubric: dict[str, Any],
    label: str,
) -> str:
    return f"""Score one anonymized shot plan for a continuous story clip. This is a strict film-directing and editing evaluation, not a writing-style preference test.

SOURCE CLIP
{case['clip_text']}

PREVIOUS CONTINUITY
{case['previous_context']}

NEXT CONTINUITY
{case['next_context']}

DURATION BUDGET
{case['available_seconds']} seconds; every shot must be 1-15 seconds and adjacent shots are cuts.

REQUIRED BEATS
{json.dumps(case['required_beats'], ensure_ascii=False, indent=2)}

RUBRIC
{json.dumps(rubric, ensure_ascii=False, indent=2)}

PLAN {label}
Raw model output (use only when judging interface economy):
{json.dumps(result['raw_output'], ensure_ascii=False, indent=2)}

Deterministic audit:
{json.dumps(result['deterministic_audit'], ensure_ascii=False, indent=2)}

Compiled production plan:
{json.dumps(result['compiled_output'], ensure_ascii=False, indent=2)}

Return exactly one assessment object. For every rubric dimension, provide the exact dimension ID, a 0-10 score, concise concrete shot/transition evidence, and a concise concrete defect unless the score is 10. List every failed hard-gate ID. Treat deterministic failures as hard-gate failures; you may add other gates supported by the plan. Judge against the rubric absolutely, without imagining competing plans. A professional plan earns cuts with emotion, information, action phase, geography, or rhythm; it does not receive credit merely for naming lenses or movements."""


def failed_generation_score(result: dict[str, Any], rubric: dict[str, Any]) -> dict[str, Any]:
    dimension_ids = [item["id"] for item in rubric["dimensions"]]
    message = f"{result.get('error_type')}: {result.get('error')}"
    failed_gates = [
        "G01_source_fidelity",
        "G02_duration_contract",
        "G03_single_setup_per_shot",
        "G04_spatial_continuity",
        "G05_asset_and_scene_validity",
        "G06_executable_opening_state",
    ]
    return {
        "raw_score": 0.0,
        "gate_cap": 4.0,
        "gated_score": 0.0,
        "failed_gates": failed_gates,
        "dimension_scores": {dimension_id: 0.0 for dimension_id in dimension_ids},
        "dimension_evidence": {dimension_id: "No valid model output." for dimension_id in dimension_ids},
        "dimension_defects": {dimension_id: message for dimension_id in dimension_ids},
        "summary": "The raw-model interface did not produce a valid plan.",
        "deterministic_audit": {
            "failed_gates": failed_gates,
            "shot_count": 0,
            "duration_sum": 0,
            "duration_budget": None,
            "dialogue_expected": [],
            "dialogue_actual": [],
            "average_raw_shot_field_count": 0.0,
        },
    }


def score_assessment(
    assessment: JudgeCandidateAssessment,
    rubric: dict[str, Any],
    deterministic_failed: list[str],
) -> dict[str, Any]:
    expected = {item["id"]: item for item in rubric["dimensions"]}
    scores = {item.dimension_id: item for item in assessment.dimensions}
    if set(scores) != set(expected):
        raise ValueError(
            "judge dimensions mismatch: "
            f"missing={sorted(set(expected).difference(scores))} extra={sorted(set(scores).difference(expected))}"
        )
    weighted_sum = sum(scores[item_id].score * float(spec["weight"]) for item_id, spec in expected.items())
    weight_sum = sum(float(spec["weight"]) for spec in expected.values())
    raw_score = weighted_sum / weight_sum
    gate_caps = {item["id"]: float(item["cap"]) for item in rubric["hard_gates"]}
    failed = sorted(set(assessment.failed_gates).union(deterministic_failed))
    unknown = set(failed).difference(gate_caps)
    if unknown:
        raise ValueError(f"unknown hard gates: {sorted(unknown)}")
    cap = min([gate_caps[item] for item in failed] or [10.0])
    return {
        "raw_score": round(raw_score, 4),
        "gate_cap": cap,
        "gated_score": round(min(raw_score, cap), 4),
        "failed_gates": failed,
        "dimension_scores": {item_id: scores[item_id].score for item_id in expected},
        "dimension_evidence": {item_id: scores[item_id].evidence for item_id in expected},
        "dimension_defects": {item_id: scores[item_id].defect for item_id in expected},
        "summary": assessment.summary,
    }


def judge_partial_path(case_id: str, candidate_id: str) -> Path:
    return judge_dir() / "partials" / case_id / f"{candidate_id}.json"


async def judge_successful_candidate(
    provider: Any,
    case: dict[str, Any],
    rubric: dict[str, Any],
    candidate_id: str,
    label: str,
    result: dict[str, Any],
) -> dict[str, Any]:
    partial_path = judge_partial_path(case["id"], candidate_id)
    if partial_path.is_file():
        return read_json(partial_path)

    prompt = judge_candidate_prompt(case, result, rubric, label)
    prompt_path = judge_dir() / f"{case['id']}.{label}.prompt.txt"
    write_text(prompt_path, prompt)
    last_error: Exception | None = None
    for attempt in range(1, JUDGE_ATTEMPTS_PER_CASE + 1):
        if prior_call_count("judge") >= JUDGE_CALL_LIMIT:
            raise ValueError("judge call guard would be exceeded")
        append_jsonl(
            LAB / "ledger.jsonl",
            {
                "event": "call",
                "kind": "judge",
                "cycle": ACTIVE_CYCLE,
                "case_id": case["id"],
                "candidate_id": candidate_id,
                "label": label,
                "attempt": attempt,
                "at": utc_now(),
            },
        )
        try:
            assessment = await provider.generate_json(
                prompt,
                JudgeCandidateAssessment,
                temperature=0.1,
                metadata={
                    "node_name": "clip_to_shots",
                    "prompt_asset_name": f"evolution-judge-{case['id']}-{label}",
                },
            )
            deterministic = result["deterministic_audit"]
            score = {
                "label": label,
                **score_assessment(assessment, rubric, deterministic["failed_gates"]),
                "deterministic_audit": deterministic,
            }
            output = {
                "case_id": case["id"],
                "candidate_id": candidate_id,
                "label": label,
                "prompt_sha256": sha256(prompt_path),
                "raw_response": assessment.model_dump(mode="json"),
                "score": score,
                "completed_at": utc_now(),
            }
            write_json(partial_path, output)
            return output
        except Exception as exc:
            last_error = exc
            append_jsonl(
                judge_dir() / "retry-history.jsonl",
                {
                    "case_id": case["id"],
                    "candidate_id": candidate_id,
                    "label": label,
                    "attempt": attempt,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "at": utc_now(),
                },
            )
    raise RuntimeError(
        f"judge infrastructure failed for {case['id']}/{candidate_id} after "
        f"{JUDGE_ATTEMPTS_PER_CASE} attempts"
    ) from last_error


async def judge_one(
    provider: Any,
    case: dict[str, Any],
    rubric: dict[str, Any],
) -> dict[str, Any]:
    results = {candidate_id: read_json(result_path(case["id"], candidate_id)) for candidate_id in CANDIDATES}
    mapping, _labels = rotated_labels(case["id"])
    scored: dict[str, Any] = {}
    partials: dict[str, dict[str, Any]] = {}
    for label, candidate_id in mapping.items():
        result = results[candidate_id]
        if result.get("status") != "success":
            scored[candidate_id] = {"label": label, **failed_generation_score(result, rubric)}
            continue
        partial = await judge_successful_candidate(
            provider,
            case,
            rubric,
            candidate_id,
            label,
            result,
        )
        partials[label] = partial
        scored[candidate_id] = partial["score"]
    output = {
        "case_id": case["id"],
        "label_mapping": mapping,
        "prompt_sha256": {label: partial["prompt_sha256"] for label, partial in partials.items()},
        "raw_response": {label: partial["raw_response"] for label, partial in partials.items()},
        "scores": scored,
        "completed_at": utc_now(),
    }
    write_json(judge_dir() / f"{case['id']}.json", output)
    return output


def build_summary(split: str) -> dict[str, Any]:
    payload, rubric = load_inputs()
    selected = [case for case in payload["cases"] if case["split"] == split]
    rows = [read_json(judge_dir() / f"{case['id']}.json") for case in selected]
    candidates: dict[str, Any] = {}
    for candidate_id in CANDIDATES:
        scores = [row["scores"][candidate_id]["gated_score"] for row in rows]
        raw_scores = [row["scores"][candidate_id]["raw_score"] for row in rows]
        gate_failures = sum(len(row["scores"][candidate_id]["failed_gates"]) for row in rows)
        field_counts = [
            row["scores"][candidate_id]["deterministic_audit"]["average_raw_shot_field_count"]
            for row in rows
        ]
        mean = sum(scores) / len(scores)
        variance = sum((score - mean) ** 2 for score in scores) / len(scores)
        candidates[candidate_id] = {
            "mean_gated_score": round(mean, 4),
            "mean_raw_score": round(sum(raw_scores) / len(raw_scores), 4),
            "population_variance": round(variance, 6),
            "hard_gate_failure_count": gate_failures,
            "mean_raw_shot_field_count": round(sum(field_counts) / len(field_counts), 2),
            "case_scores": {row["case_id"]: row["scores"][candidate_id]["gated_score"] for row in rows},
        }
    ranking = sorted(
        candidates,
        key=lambda candidate_id: (
            -candidates[candidate_id]["mean_gated_score"],
            candidates[candidate_id]["hard_gate_failure_count"],
            candidates[candidate_id]["mean_raw_shot_field_count"],
            candidates[candidate_id]["population_variance"],
        ),
    )
    summary = {
        "split": split,
        "rubric": rubric["name"],
        "case_count": len(rows),
        "candidates": candidates,
        "ranking": ranking,
        "winner": ranking[0],
        "created_at": utc_now(),
    }
    write_json(cycle_dir() / f"summary-{split}.json", summary)
    return summary


async def run_judge(split: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("API calls require --confirm-api-calls")
    payload, rubric = load_inputs()
    selected = [case for case in payload["cases"] if case["split"] == split]
    pending = [case for case in selected if not (judge_dir() / f"{case['id']}.json").is_file()]
    settings = load_settings(str(CONFIG_PATH))
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(LAB)
    provider = router.text("shot", node_name="clip_to_shots")
    results = []
    for case in pending:
        results.append(await judge_one(provider, case, rubric))
    summary = build_summary(split)
    return {"calls": len(results), "summary": summary}


async def diagnose_raw(case_id: str, candidate_id: str, confirmed: bool) -> dict[str, Any]:
    if not confirmed:
        raise ValueError("API calls require --confirm-api-calls")
    payload, _rubric = load_inputs()
    case = next((item for item in payload["cases"] if item["id"] == case_id), None)
    if case is None:
        raise ValueError(f"unknown case: {case_id}")
    if candidate_id not in CANDIDATES:
        raise ValueError(f"unknown candidate for {ACTIVE_CYCLE}: {candidate_id}")
    prompt, _catalog = candidate_prompt(candidate_id, case, payload)
    schema = LeanModelOutput if candidate_id not in {"A", "B"} else RichModelOutput
    scene = payload["scene_anchors"][case["scene_id"]]
    scene_path = ROOT / scene["path"]
    settings = load_settings(str(CONFIG_PATH))
    router = ProviderRouter(settings)
    endpoint = router.text("shot", node_name="clip_to_shots")
    provider = endpoint._provider
    metadata = {
        "node_name": "clip_to_shots",
        "prompt_asset_name": f"diagnostic-{case_id}-{candidate_id}",
        "scene_id": case["scene_id"],
    }
    request_payload = provider.build_payload(
        prompt,
        schema,
        temperature=0.35,
        metadata=metadata,
        refs=[AssetRef(id=case["scene_id"], type="image", path=str(scene_path))],
    )
    append_jsonl(
        LAB / "ledger.jsonl",
        {
            "event": "call",
            "kind": "diagnostic",
            "cycle": ACTIVE_CYCLE,
            "case_id": case_id,
            "candidate_id": candidate_id,
            "at": utc_now(),
        },
    )
    response = await provider._post_with_retries(
        payload=request_payload,
        params=provider._request_params(),
        headers=provider._headers(),
        metadata=metadata,
        operation="diagnose_raw",
    )
    response_payload = response.json()
    content = provider._extract_text(response_payload) if response.status_code < 400 else response.text
    output = {
        "cycle": ACTIVE_CYCLE,
        "case_id": case_id,
        "candidate_id": candidate_id,
        "http_status": response.status_code,
        "content": content,
        "response": response_payload,
        "completed_at": utc_now(),
    }
    output_path = cycle_dir() / "diagnostics" / f"{case_id}.{candidate_id}.json"
    write_json(output_path, output)
    return {"output": str(output_path), "http_status": response.status_code, "content_length": len(content)}


def materialize_diagnostic(case_id: str, candidate_id: str) -> dict[str, Any]:
    payload, _rubric = load_inputs()
    case = next((item for item in payload["cases"] if item["id"] == case_id), None)
    if case is None:
        raise ValueError(f"unknown case: {case_id}")
    diagnostic_path = cycle_dir() / "diagnostics" / f"{case_id}.{candidate_id}.json"
    diagnostic = read_json(diagnostic_path)
    if int(diagnostic.get("http_status") or 0) != 200:
        raise ValueError("diagnostic response was not successful")
    prompt, catalog = candidate_prompt(candidate_id, case, payload)
    prompt_path = generation_dir() / case_id / f"{candidate_id}.prompt.txt"
    write_text(prompt_path, prompt)
    if candidate_id in {"A", "B"}:
        output = RichModelOutput.model_validate(parse_json_object(diagnostic["content"]))
        raw = output.model_dump(mode="json")
        compiled = rich_dict(output)
    else:
        output = LeanModelOutput.model_validate(parse_json_object(diagnostic["content"]))
        raw = output.model_dump(mode="json")
        compiled = compile_lean(output, case, payload, catalog)
    audit = deterministic_audit(compiled, raw, case, payload, catalog)
    result = {
        "status": "success",
        "case_id": case_id,
        "candidate_id": candidate_id,
        "prompt_path": str(prompt_path.relative_to(ROOT)).replace("\\", "/"),
        "prompt_sha256": sha256(prompt_path),
        "scene_sha256": payload["scene_anchors"][case["scene_id"]]["sha256"],
        "raw_output": raw,
        "compiled_output": compiled,
        "deterministic_audit": audit,
        "source": "materialized_raw_diagnostic",
        "completed_at": utc_now(),
    }
    write_json(result_path(case_id, candidate_id), result)
    return {"output": str(result_path(case_id, candidate_id)), "audit": audit}


def prepare() -> dict[str, Any]:
    payload, rubric = load_inputs()
    manifest = {
        "schema_version": 1,
        "cycle": ACTIVE_CYCLE,
        "created_at": utc_now(),
        "config_path": str(CONFIG_PATH),
        "config_sha256": sha256(CONFIG_PATH),
        "cases_path": str(CASES_PATH),
        "cases_sha256": sha256(CASES_PATH),
        "rubric_path": str(RUBRIC_PATH),
        "rubric_sha256": sha256(RUBRIC_PATH),
        "candidates": {
            candidate_id: {
                "path": str(path),
                "sha256": sha256(path),
                "entity_index": "role_ids_and_names" if candidate_id in {"E", "G"} else "semantic_descriptions",
                "source_wording": "policy_neutral_visual" if candidate_id in {"F", "G"} else "verbatim",
            }
            for candidate_id, path in CANDIDATES.items()
        },
        "scene_anchors": payload["scene_anchors"],
        "generator_call_limit": GENERATOR_CALL_LIMIT,
        "judge_call_limit": JUDGE_CALL_LIMIT,
        "rubric_name": rubric["name"],
    }
    manifest_path = cycle_dir() / "manifest.json"
    write_json(manifest_path, manifest)
    for case in payload["cases"]:
        catalog = dialogue_catalog(case, payload)
        if Counter(row["id"] for row in catalog) != Counter({f"D{index:02d}": 1 for index in range(1, len(catalog) + 1)}):
            raise ValueError(f"dialogue IDs are not continuous for {case['id']}")
        for candidate_id in CANDIDATES:
            candidate_prompt(candidate_id, case, payload)
    return {
        "manifest": str(manifest_path),
        "case_count": len(payload["cases"]),
        "exploration_count": sum(case["split"] == "exploration" for case in payload["cases"]),
        "holdout_count": sum(case["split"] == "holdout" for case in payload["cases"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the clip_to_shots prompt evolution lab without pytest.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare_parser = subparsers.add_parser("prepare")
    prepare_parser.add_argument("--cycle", choices=tuple(CANDIDATE_SETS), default="cycle01")
    generate_parser = subparsers.add_parser("generate")
    generate_parser.add_argument("--cycle", choices=tuple(CANDIDATE_SETS), default="cycle01")
    generate_parser.add_argument("--split", choices=("exploration", "holdout"), default="exploration")
    generate_parser.add_argument("--confirm-api-calls", action="store_true")
    judge_parser = subparsers.add_parser("judge")
    judge_parser.add_argument("--cycle", choices=tuple(CANDIDATE_SETS), default="cycle01")
    judge_parser.add_argument("--split", choices=("exploration", "holdout"), default="exploration")
    judge_parser.add_argument("--confirm-api-calls", action="store_true")
    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--cycle", choices=tuple(CANDIDATE_SETS), default="cycle01")
    summary_parser.add_argument("--split", choices=("exploration", "holdout"), default="exploration")
    diagnose_parser = subparsers.add_parser("diagnose-raw")
    diagnose_parser.add_argument("--cycle", choices=tuple(CANDIDATE_SETS), default="cycle01")
    diagnose_parser.add_argument("--case", required=True)
    diagnose_parser.add_argument("--candidate", required=True)
    diagnose_parser.add_argument("--confirm-api-calls", action="store_true")
    materialize_parser = subparsers.add_parser("materialize-diagnostic")
    materialize_parser.add_argument("--cycle", choices=tuple(CANDIDATE_SETS), default="cycle01")
    materialize_parser.add_argument("--case", required=True)
    materialize_parser.add_argument("--candidate", required=True)
    args = parser.parse_args()
    configure_cycle(args.cycle)

    if args.command == "prepare":
        result = prepare()
    elif args.command == "generate":
        result = asyncio.run(run_generation(args.split, args.confirm_api_calls))
    elif args.command == "judge":
        result = asyncio.run(run_judge(args.split, args.confirm_api_calls))
    elif args.command == "diagnose-raw":
        result = asyncio.run(diagnose_raw(args.case, args.candidate, args.confirm_api_calls))
    elif args.command == "materialize-diagnostic":
        result = materialize_diagnostic(args.case, args.candidate)
    else:
        result = build_summary(args.split)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
