from __future__ import annotations

import argparse
import base64
import hashlib
import html
import io
import json
import mimetypes
import re
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderAuthError, ProviderBadResponseError  # noqa: E402
from autodrama.providers.aibox.image.gpt_image import AiboxImageProvider  # noqa: E402
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.google.text.gemini import GeminiTextProvider  # noqa: E402
from autodrama.repositories.project_layout import ProjectLayout  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402
from autodrama.workflows.pregen import PREGEN_NODE_GROUPS  # noqa: E402


LAB_DEFAULT = ROOT / ".tmp" / "layout-gen-template-evolution-20260809-real-layout"
CONFIG_DEFAULT = ROOT / "saodi.yaml"
SPATIAL_TEMPLATE_DEFAULT = ROOT / ".assets" / "image_templates" / "scene_spatial_anchor_template.png"
LAYOUT_JSON_DEFAULT = (
    ROOT
    / "outputs"
    / "saodi_0803"
    / "assets"
    / "json"
    / "nodes"
    / "layout_prop_boundary_review.json"
)
KEY_VISION_ATTACHMENT_DEFAULT = Path(
    r"C:\Users\csh10\AppData\Local\Temp\codex-clipboard-b1dac8c5-f727-48f0-ae61-6daf12367e1c.png"
)
IMAGE_MODEL = "gpt-image-2-guan"
TEXT_MODEL = "gemini-3.6-flash"
IMAGE_SIZE = "1024x1536"
IMAGE_QUALITY = "high"
MAX_IMAGE_CALLS = 20
MAX_TEXT_CALLS = 80
ROUND_COUNT = 5
CANDIDATE_COUNT = 4


class LayoutPromptDraft(BaseModel):
    final_prompt: str = Field(min_length=80)


class LayoutAuditAssessment(BaseModel):
    style_alignment: float = Field(ge=0, le=10)
    style_evidence: str = Field(min_length=1)
    spatial_consistency: float = Field(ge=0, le=10)
    spatial_evidence: str = Field(min_length=1)
    fatal_spatial_inconsistency: bool
    defect_tags: list[str] = Field(default_factory=list)
    recommendation: str = Field(min_length=1)


ROUND_SPECS: tuple[dict[str, str], ...] = (
    {
        "round": "01",
        "layout_name": "天剑宗外门牌楼",
        "prop_id": "wooden_staff",
        "prop": "one simple wooden staff resting against the edge of the stone platform, with no person holding it",
        "purpose": "test a small vertical prop without changing the extracted gate and stair architecture",
        "view_pair": "View A is a high oblique camera at the lower outer approach, looking uphill toward the front of the paifang, its platform, and the stairs passing through it. View B is a high oblique camera at the inner mountain side beyond the paifang, looking back downhill toward the rear of the same paifang and the platform. View B must expose a different side of the gate and reverse the staircase direction in screen space; it must not show the same front facade or a horizontal flip.",
    },
    {
        "round": "02",
        "layout_name": "天剑宗山脚",
        "prop_id": "red_gold_brazier",
        "prop": "one small red-gold portable brazier placed beside the open dirt path, with no person holding it",
        "purpose": "test a compact prop in a natural clearing without replacing the extracted grass, trees, or path",
        "view_pair": "View A is a high oblique camera at one outer edge of the clearing, looking toward the side path that rises toward the mountain. View B is a high oblique camera at the opposite far edge near the mountain side, looking back across the same clearing with the path descending toward the opposite screen side. Move the same asymmetric trees, rocks, path bend, and brazier to their correct screen positions; do not horizontally flip View A.",
    },
    {
        "round": "03",
        "layout_name": "天剑宗外门牌楼",
        "prop_id": "rope_barrier",
        "prop": "a short plain rope barrier on two low wooden posts at one side of the stone platform",
        "purpose": "test a horizontal boundary prop while preserving the actual gate opening and long stairs",
        "view_pair": "View A is a high oblique camera at the lower outer approach, looking uphill toward the front of the paifang, its platform, and the stairs passing through it. View B is a high oblique camera at the inner mountain side beyond the paifang, looking back downhill toward the rear of the same paifang and the platform. View B must expose a different side of the gate and reverse the staircase direction in screen space; it must not show the same front facade or a horizontal flip.",
    },
    {
        "round": "04",
        "layout_name": "天剑宗山脚",
        "prop_id": "wooden_water_bucket",
        "prop": "one rough wooden water bucket beside the existing dirt path, clearly separate from the tree roots",
        "purpose": "test a low prop near circulation while preserving the extracted open ground and vegetation",
        "view_pair": "View A is a high oblique camera at one outer edge of the clearing, looking toward the side path that rises toward the mountain. View B is a high oblique camera at the opposite far edge near the mountain side, looking back across the same clearing with the path descending toward the opposite screen side. Move the same asymmetric trees, rocks, path bend, and bucket to their correct screen positions; do not horizontally flip View A.",
    },
    {
        "round": "05",
        "layout_name": "天剑宗外门牌楼",
        "prop_id": "stone_lantern",
        "prop": "one low weathered stone lantern placed at the side of the stone platform, with no readable markings",
        "purpose": "confirm a material-rich prop without changing the extracted mountain-pass structure",
        "view_pair": "View A is a high oblique camera at the lower outer approach, looking uphill toward the front of the paifang, its platform, and the stairs passing through it. View B is a high oblique camera at the inner mountain side beyond the paifang, looking back downhill toward the rear of the same paifang and the platform. View B must expose a different side of the gate and reverse the staircase direction in screen space; it must not show the same front facade or a horizontal flip.",
    },
)


CANDIDATE_FAMILIES: tuple[dict[str, str], ...] = (
    {
        "family": "incumbent",
        "label": "Incumbent spatial-anchor contract",
        "reference_policy": "whitebox_only",
        "instruction": (
            "Use a restrained spatial-anchor contract, but make the camera pair physically opposite rather than "
            "mirrored: name the camera sides, the visible front/back surfaces, and the route reversal. Keep the "
            "prompt compact and concrete."
        ),
    },
    {
        "family": "topology_proof",
        "label": "Topology proof",
        "reference_policy": "whitebox_only",
        "instruction": (
            "Spend the prompt budget on correspondence proof. Explicitly require a true reverse-side camera "
            "orbit, different visible surfaces, reversed route direction, and asymmetric landmark relocation. "
            "A horizontal mirror or two views of the same facade is a failure."
        ),
    },
    {
        "family": "style_separation",
        "label": "Separated layout/style references",
        "reference_policy": "dual_reference",
        "instruction": (
            "Keep the white reference responsible only for two-panel format and the physically opposite camera "
            "pair. Keep the style reference responsible only for rendering medium, palette, light, atmosphere, "
            "material response, and finish. State the reverse-side requirement without copying people or composition."
        ),
    },
    {
        "family": "balanced_minimal",
        "label": "Balanced minimal priority prompt",
        "reference_policy": "dual_reference",
        "instruction": (
            "Use a short priority order: one physical place first, a genuine opposite-corner orbit with a visible "
            "reverse side second, prop placement third, style translation fourth. Remove abstract praise, workflow "
            "language, and redundant negatives."
        ),
    },
)


EXPERIMENT_PROTOCOL = """# layout_gen template evolution protocol

- Node group under test: `layout_prompt` → `layout_image_generation` → `layout_image_audit`.
- The image budget is a hard ceiling of 20 real image-generation calls.
- Five rounds are run. Each round uses a different fixed prop and four prompt-template candidates.
- Every image is one empty two-view scene spatial-anchor sheet. The top and bottom views must be the same
  physical location from genuinely opposite camera corners, not a horizontal mirror or two shots from the
  same facade. The reverse view must expose different surfaces and reverse the route direction in screen space.
- `layout_prompt` is compiled by Gemini. Its output is sent verbatim as the image prompt.
- `layout_image_generation` uses `gpt-image-2-guan`, one image per call, high quality, 1024x1536.
- The white spatial reference is always available to Gemini. The image generator receives either the white
  reference alone or the white reference plus the user-confirmed main-visual style reference, according to the
  candidate's reference policy.
- Gemini audits only two dimensions: style coordination with the main visual anchor, and spatial consistency
  between the two generated views. The audit does not expand into a generic image-quality rubric.
- Later rounds inherit the previous round's winning direction and receive a short failure summary. The prop
  changes every round so that the prompt template is tested against different spatially anchored objects.
- Image failures are recorded and consume their reserved call. No automatic image retry is allowed inside this
  20-call budget.
"""


SCENE_SHEET_RULES = """The white spatial reference defines only the delivery grammar: one clean scene spatial-anchor sheet,
two vertically stacked complementary high-angle views from opposite corners, and clear spatial correspondence.
Do not copy its courtyard, walls, gate, pavilion, pond, columns, stairs, architecture, or object arrangement.
The extracted layout description is the source of truth for the actual scene.
The two panels must be a real 3D orbit around the same place: do not make the lower panel a horizontal mirror,
do not reuse the same camera side, and do not show the same front-facing facade twice. The reverse panel must
reveal a different side or rear surface and move asymmetric landmarks to their physically correct screen sides."""


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
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(value, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def ensure_file(path: Path, label: str) -> Path:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"{label} is missing: {path}")
    return path


def copy_snapshot(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if sha256(destination) != sha256(source):
            raise FileExistsError(f"Frozen input already exists with different bytes: {destination}")
        return
    shutil.copy2(source, destination)


def snapshot_record(logical_role: str, source: Path, snapshot: Path) -> dict[str, Any]:
    return {
        "logical_role": logical_role,
        "source_path": str(source),
        "snapshot_path": str(snapshot),
        "sha256": sha256(snapshot),
        "mime_type": mimetypes.guess_type(source.name)[0] or "application/octet-stream",
    }


def group_contract() -> dict[str, Any]:
    expected = ("layout_prompt", "layout_image_generation", "layout_image_audit")
    actual = tuple(PREGEN_NODE_GROUPS.get("layout_gen", ()))
    if actual != expected:
        raise AssertionError(f"layout_gen contract changed: expected {expected}, got {actual}")
    return {"name": "layout_gen", "nodes": list(actual), "validated_at": utc_now()}


def prepare_lab(args: argparse.Namespace) -> dict[str, Any]:
    lab = Path(args.lab_dir).expanduser().resolve()
    if lab.parent != (ROOT / ".tmp").resolve():
        raise ValueError(f"lab-dir must be a direct child of {ROOT / '.tmp'}: {lab}")
    config = ensure_file(Path(args.config), "config")
    spatial = ensure_file(Path(args.spatial_template), "spatial white-model reference")
    key_vision = ensure_file(Path(args.key_vision), "user-confirmed main-visual reference")
    layout_json = ensure_file(Path(args.layout_json), "production extracted layout JSON")
    image_budget = int(args.image_budget)
    prior_image_calls = int(args.prior_image_calls)
    if image_budget < 1 or image_budget > MAX_IMAGE_CALLS:
        raise ValueError(f"image-budget must be between 1 and {MAX_IMAGE_CALLS}")
    if prior_image_calls < 0 or prior_image_calls + image_budget > MAX_IMAGE_CALLS:
        raise ValueError(f"prior-image-calls + image-budget must not exceed {MAX_IMAGE_CALLS}")
    contract = group_contract()

    lab.mkdir(parents=True, exist_ok=True)
    spatial_snapshot = lab / "inputs" / "scene_spatial_anchor_template.png"
    key_snapshot = lab / "inputs" / "key_vision_user_confirmed.png"
    layout_snapshot = lab / "inputs" / "layout_prop_boundary_review.json"
    copy_snapshot(spatial, spatial_snapshot)
    copy_snapshot(key_vision, key_snapshot)
    copy_snapshot(layout_json, layout_snapshot)

    current_prompt_path = ROOT / "autodrama" / "src" / "autodrama" / "prompts" / "layout_prompt" / "default.md"
    current_prompt_snapshot = lab / "inputs" / "production_layout_prompt_default.md"
    if current_prompt_path.is_file():
        copy_snapshot(current_prompt_path, current_prompt_snapshot)

    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "active_config": snapshot_record("active_config", config, config),
        "files": [
            snapshot_record("spatial_template", spatial, spatial_snapshot),
            snapshot_record("key_vision", key_vision, key_snapshot),
            snapshot_record("production_layout_json", layout_json, layout_snapshot),
        ],
    }
    if current_prompt_snapshot.is_file():
        manifest["files"].append(snapshot_record("production_layout_prompt", current_prompt_path, current_prompt_snapshot))
    write_json(lab / "inputs" / "manifest.json", manifest)

    experiment = {
        "schema_version": 1,
        "status": "prepared",
        "created_at": utc_now(),
        "node_group": contract,
        "config_path": str(config),
        "image_model": IMAGE_MODEL,
        "text_model": TEXT_MODEL,
        "image_settings": {"size": IMAGE_SIZE, "quality": IMAGE_QUALITY, "n": 1},
        "authorization": {
            "global_image_calls": MAX_IMAGE_CALLS,
            "prior_invalid_image_calls": prior_image_calls,
            "current_lab_image_calls": image_budget,
        },
        "budgets": {
            "image_calls": image_budget,
            "text_calls": MAX_TEXT_CALLS,
            "rounds": ROUND_COUNT,
            "candidates_per_round": CANDIDATE_COUNT,
        },
        "inputs": {
            "spatial_template": str(spatial_snapshot.relative_to(lab)).replace("\\", "/"),
            "key_vision": str(key_snapshot.relative_to(lab)).replace("\\", "/"),
            "layout_json": str(layout_snapshot.relative_to(lab)).replace("\\", "/"),
            "key_vision_selection": "user_confirmed_attachment",
        },
    }
    old_experiment = lab / "experiment.json"
    if old_experiment.exists():
        previous = read_json(old_experiment)
        previous_inputs = previous.get("inputs") or {}
        if previous_inputs != experiment["inputs"]:
            raise ValueError("Existing lab was prepared with different frozen input selection")
        experiment["created_at"] = previous.get("created_at") or experiment["created_at"]
        experiment["status"] = previous.get("status") or "prepared"
        experiment["authorization"] = previous.get("authorization") or experiment["authorization"]
        experiment["budgets"] = previous.get("budgets") or experiment["budgets"]
    write_json(old_experiment, experiment)
    (lab / "prompts").mkdir(parents=True, exist_ok=True)
    (lab / "images").mkdir(parents=True, exist_ok=True)
    (lab / "audits").mkdir(parents=True, exist_ok=True)
    (lab / "responses").mkdir(parents=True, exist_ok=True)
    (lab / "experiment_protocol.md").write_text(EXPERIMENT_PROTOCOL, encoding="utf-8", newline="\n")
    real_layouts = read_json(layout_snapshot).get("layouts") or []
    write_json(
        lab / "round_plan.json",
        {
            "scene_sheet_rules": SCENE_SHEET_RULES,
            "production_layout_source": str(layout_snapshot.relative_to(lab)).replace("\\", "/"),
            "real_layouts": real_layouts,
            "rounds": list(ROUND_SPECS),
            "candidates": list(CANDIDATE_FAMILIES),
            "planned_samples": len(candidate_schedule(image_budget)),
        },
    )

    print(json.dumps({"lab": str(lab), "status": "prepared", "image_budget": image_budget, "inputs": manifest["files"]}, ensure_ascii=False, indent=2))
    return {"lab": str(lab), "status": "prepared"}


def load_lab_inputs(lab: Path) -> tuple[dict[str, Any], Path, Path, Path, Path]:
    lab = lab.resolve()
    experiment = read_json(lab / "experiment.json")
    manifest = read_json(lab / "inputs" / "manifest.json")
    files = {str(item["logical_role"]): item for item in manifest.get("files", [])}
    spatial = (lab / str(experiment["inputs"]["spatial_template"])).resolve()
    key_vision = (lab / str(experiment["inputs"]["key_vision"])).resolve()
    layout_json = (lab / str(experiment["inputs"]["layout_json"])).resolve()
    config = Path(str(experiment["config_path"])).resolve()
    for path, label in (
        (spatial, "frozen spatial template"),
        (key_vision, "frozen key-vision reference"),
        (layout_json, "frozen production layout JSON"),
        (config, "active config"),
    ):
        ensure_file(path, label)
    for role, path in (("spatial_template", spatial), ("key_vision", key_vision), ("production_layout_json", layout_json)):
        expected = str(files[role]["sha256"])
        if sha256(path) != expected:
            raise ValueError(f"Frozen {role} hash mismatch: {path}")
    return experiment, config, spatial, key_vision, layout_json


def make_gemini_provider(settings: Any) -> GeminiTextProvider:
    provider_settings = settings.providers["aibox"].model_copy(deep=True)
    provider_settings.models["text"] = TEXT_MODEL
    provider_settings.api_keys = settings.api_keys
    return GeminiTextProvider(
        provider_settings,
        settings.runtime,
        model_key="text",
        provider_name="aibox",
    )


def make_image_provider(settings: Any) -> AiboxImageProvider:
    provider_settings = settings.providers["aibox"].model_copy(deep=True)
    provider_settings.models["image"] = IMAGE_MODEL
    provider_settings.options.update(
        {
            "size": IMAGE_SIZE,
            "quality": IMAGE_QUALITY,
            "n": 1,
            "aibox_max_attempts": 1,
            "max_attempts": 1,
            "aibox_request_timeout_seconds": 300,
            "max_wait_seconds": 900,
            "poll_interval_seconds": 4,
        }
    )
    provider_settings.api_keys = settings.api_keys
    return AiboxImageProvider(
        provider_settings,
        settings.runtime,
        reference_uploader_settings=settings.providers.get("toapi"),
    )


def make_ref(path: Path, role: str, sample_id: str) -> AssetRef:
    return AssetRef(
        id=f"{sample_id}-{role}",
        type="image",
        path=str(path),
        metadata={"logical_role": role, "mime_type": "image/png", "sha256": sha256(path)},
    )


def reference_policy_text(policy: str) -> str:
    if policy == "whitebox_only":
        return (
            "For image generation, only the white spatial reference is supplied. Treat it as a presentation and "
            "view-correspondence reference, not as a source of buildings or props. Ignore every courtyard "
            "structure visible in it. Translate the main visual anchor into concrete words for medium, palette, "
            "light, atmosphere, and material response; do not refer to an unseen style image."
        )
    if policy == "dual_reference":
        return (
            "For image generation, the white spatial reference is supplied first and the main visual style "
            "reference second. Use the first only for presentation format and view correspondence; ignore every "
            "courtyard structure visible in it. Use the second only for rendering language, not its people or "
            "composition."
        )
    raise KeyError(policy)


def prior_feedback(rows: list[dict[str, Any]], round_number: int) -> str:
    summaries = [
        row
        for row in rows
        if row.get("kind") == "round_summary" and int(row.get("round", 0)) < round_number
    ]
    if not summaries:
        return "There is no previous-round feedback. Establish a clean baseline."
    latest = sorted(summaries, key=lambda row: int(row.get("round", 0)))[-1]
    observations = latest.get("observations") or []
    observations_text = " ".join(str(item) for item in observations[:3]).strip()
    if len(observations_text) > 900:
        observations_text = observations_text[:897] + "..."
    return (
        f"The previous round's strongest attempt was {latest.get('winner_family', 'unknown')} with "
        f"style score {float(latest.get('winner_style', 0)):.1f}/10 and spatial score "
        f"{float(latest.get('winner_spatial', 0)):.1f}/10. Preserve what worked and repair this observation: "
        f"{observations_text or 'no reliable defect evidence was recorded.'}"
    )


def candidate_schedule(image_budget: int) -> list[tuple[dict[str, str], dict[str, str]]]:
    """Use complete four-way exploration rounds, then a final validation sample if needed."""
    schedule: list[tuple[dict[str, str], dict[str, str]]] = []
    for index, round_spec in enumerate(ROUND_SPECS):
        candidates = list(CANDIDATE_FAMILIES)
        if image_budget < ROUND_COUNT * CANDIDATE_COUNT and index == ROUND_COUNT - 1:
            candidates = [next(item for item in CANDIDATE_FAMILIES if item["family"] == "balanced_minimal")]
        for candidate in candidates:
            if len(schedule) >= image_budget:
                return schedule
            schedule.append((round_spec, candidate))
    return schedule


def load_real_layouts(path: Path) -> dict[str, dict[str, Any]]:
    payload = read_json(path)
    layouts = payload.get("layouts")
    if not isinstance(layouts, list) or not layouts:
        raise ValueError(f"Production layout JSON contains no layouts: {path}")
    result: dict[str, dict[str, Any]] = {}
    for item in layouts:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        features = [str(value).strip() for value in item.get("space_features") or [] if str(value).strip()]
        result[name] = {
            "name": name,
            "brief": str(item.get("brief") or "").strip(),
            "space_features": features,
        }
    if not result:
        raise ValueError(f"Production layout JSON has no usable named layouts: {path}")
    missing = sorted({str(spec["layout_name"]) for spec in ROUND_SPECS} - set(result))
    if missing:
        raise ValueError(f"Round plan references layouts missing from production JSON: {', '.join(missing)}")
    return result


def layout_content(layout: dict[str, Any]) -> str:
    feature_lines = "\n".join(f"- {value}" for value in layout.get("space_features") or [])
    name = str(layout.get("name") or "").strip()
    brief = str(layout.get("brief") or "").strip()
    return (
        "Actual extracted scene content:\n"
        f"Scene: {name}\n"
        f"{brief}\n"
        + (f"Stable visible features:\n{feature_lines}\n" if feature_lines else "")
    )


def build_prompt_writer_input(
    round_spec: dict[str, str],
    candidate: dict[str, str],
    feedback: str,
    layout: dict[str, Any],
) -> str:
    return f"""Write one concise English image-generation prompt for an empty reusable scene spatial-anchor sheet.

{layout_content(layout)}

{SCENE_SHEET_RULES}

This round's fixed prop is {round_spec['prop']}.
The prop is the only changing set-dressing element this round. It must have one exact world position and appear
in that same position in both views. Its size, material, light response, and relationship to nearby architecture
must remain stable.

Camera-pair contract for this scene:
{round_spec['view_pair']}

Known failure to correct in this iteration: previous sheets often reused the same front facade in both panels or
made the lower panel a left-right mirror. Treat either behavior as a hard failure. The second panel must be a
genuine reverse-side orbit of the same physical place, not a redesigned scene and not a mirrored image.

{reference_policy_text(candidate['reference_policy'])}

Prompt strategy for this attempt: {candidate['instruction']}
The attempt inherits the previous evidence: {feedback}

The final prompt must require two vertically stacked, complementary high-angle views from the explicitly defined
opposite camera positions of the same extracted place. View B must show a different surface or rear side of the
same central structure, reverse the route direction in screen space, and preserve asymmetric landmarks without
mirroring. Both views must preserve the extracted topology, entrances, visible architecture, terrain, orientation,
scale, paths, the declared prop, and lighting logic. It must request a finished stylized 3D scene with no people,
hands, silhouettes, cameras, FOV cones, shot annotations, subtitles, watermarks, logos, or readable signage.
Keep the wording concrete and image-focused. Do not mention a model, API, project, episode, file path, prompt
template, JSON schema, resolution, or these instructions.

Return exactly one JSON object with exactly one key: final_prompt. The value must be the complete English prompt
that will be sent verbatim to the image generator."""


def build_audit_input(
    round_spec: dict[str, str],
    candidate: dict[str, str],
    layout: dict[str, Any],
) -> str:
    return f"""Evaluate the target image against the two supplied references and the actual extracted scene content.
The target should be an empty two-view scene spatial-anchor sheet for {round_spec['prop']}.

{layout_content(layout)}

Camera-pair contract:
{round_spec['view_pair']}

Reference order:
1. target image to audit
2. white spatial reference: use it only to check the two-view sheet format and spatial-anchor presentation; do
   not use its courtyard architecture as the target scene
3. main visual style reference: use it only to check rendering language

Judge exactly two dimensions, each from 0 to 10.

1) style_alignment: Does the target's overall rendering medium, palette relationships, material response,
lighting character, atmosphere, and finish coordinate with the main visual style reference? Ignore whether the
target depicts the same people, composition, or location. A white-clay or generic game-asset appearance should
score low even if the geometry is correct.

2) spatial_consistency: Do the upper and lower views depict one physically consistent version of the actual
extracted place from opposite corners? Check the extracted architecture, terrain, entrances, paths, the fixed
prop, object count, relative positions, and scale. Penalize mirrored, redesigned, missing, duplicated, or
impossible structures. A view may reveal a different side, but it may not invent a different topology. Do not
penalize the target for differing from the white reference's courtyard because that white reference is only a
sheet-format guide. Do not require the white reference's compass, camera arrows, diagram insets, borders, or
other UI marks; their presence or absence is outside both rubric dimensions.

Hard complementary-view test: the second panel must be a genuine camera move to the opposite side. It must
reveal at least one different surface or rear side of the central structure, reverse the main path or stair
direction in screen space, and relocate asymmetric landmarks consistently. If both panels show the same
front-facing facade, the same camera side, or a horizontal mirror of one another, set fatal_spatial_inconsistency
to true and score spatial_consistency no higher than 3/10. A different crop or lighting change alone is not a
complementary view.

This candidate used the {candidate['reference_policy']} image-reference policy. Do not score that policy itself;
score only the visible result. Return exactly one JSON object with these keys: style_alignment, style_evidence,
spatial_consistency, spatial_evidence, fatal_spatial_inconsistency, defect_tags, recommendation. Keep both
evidence fields concrete and short. Do not add a generic image-quality rubric."""


def latest_stage(rows: list[dict[str, Any]], kind: str, sample_id: str) -> dict[str, Any] | None:
    matches = [row for row in rows if row.get("kind") == kind and row.get("sample_id") == sample_id]
    return matches[-1] if matches else None


def stage_attempt_count(rows: list[dict[str, Any]], kind: str, sample_id: str) -> int:
    return sum(1 for row in rows if row.get("kind") == kind and row.get("sample_id") == sample_id)


def latest_stage_rows(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != kind or not row.get("sample_id"):
            continue
        latest[str(row["sample_id"])] = row
    return list(latest.values())


def latest_round_summaries(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[int, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != "round_summary":
            continue
        latest[int(row.get("round", 0))] = row
    return list(latest.values())


def image_reservations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("event") == "reserve" and row.get("kind") == "image"]


def text_reservations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if row.get("event") == "reserve" and row.get("kind") == "text"]


def reserve_call(lab: Path, rows: list[dict[str, Any]], *, kind: str, call_id: str, sample_id: str, round_number: int) -> None:
    if any(row.get("call_id") == call_id for row in rows):
        return
    experiment = read_json(lab / "experiment.json")
    budgets = experiment.get("budgets") or {}
    if kind == "image":
        used = len(image_reservations(rows))
        limit = int(budgets.get("image_calls") or MAX_IMAGE_CALLS)
        if used >= limit:
            raise RuntimeError(f"Image budget exhausted: {used}/{limit}")
    elif kind == "text":
        used = len(text_reservations(rows))
        limit = int(budgets.get("text_calls") or MAX_TEXT_CALLS)
        if used >= limit:
            raise RuntimeError(f"Text budget exhausted: {used}/{limit}")
    else:
        raise ValueError(kind)
    append_jsonl(
        lab / "ledger.jsonl",
        {
            "event": "reserve",
            "kind": kind,
            "call_id": call_id,
            "sample_id": sample_id,
            "round": round_number,
            "at": utc_now(),
        },
    )
    rows.append({"event": "reserve", "kind": kind, "call_id": call_id, "sample_id": sample_id, "round": round_number})


def record_completion(lab: Path, rows: list[dict[str, Any]], *, call_id: str, status: str, extra: dict[str, Any] | None = None) -> None:
    row = {"event": "complete", "call_id": call_id, "status": status, "at": utc_now(), **(extra or {})}
    append_jsonl(lab / "ledger.jsonl", row)
    rows.append(row)


def failure_status(exc: Exception) -> str:
    if isinstance(exc, ProviderAuthError):
        return "provider_rejection"
    if isinstance(exc, (ProviderBadResponseError, OSError, TimeoutError)):
        message = str(exc).lower()
        if any(token in message for token in ("safety", "policy", "validation", "invalid prompt")):
            return "provider_rejection"
        return "transport_failure"
    return "provider_rejection"


async def generate_prompt(
    lab: Path,
    rows: list[dict[str, Any]],
    provider: GeminiTextProvider,
    *,
    round_spec: dict[str, str],
    candidate: dict[str, str],
    sample_id: str,
    round_number: int,
    spatial: Path,
    key_vision: Path,
    feedback: str,
    layout: dict[str, Any],
) -> dict[str, Any]:
    existing = latest_stage(rows, "prompt", sample_id)
    if existing and existing.get("status") == "success":
        return existing
    prompt_input = build_prompt_writer_input(round_spec, candidate, feedback, layout)
    prompt_path = lab / "prompts" / f"r{round_spec['round']}-{candidate['family']}.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(prompt_input, encoding="utf-8", newline="\n")
    attempt = stage_attempt_count(rows, "prompt", sample_id)
    call_id = f"txt-prompt-{sample_id}" if attempt == 0 else f"txt-prompt-{sample_id}-retry-{attempt}"
    reserve_call(lab, rows, kind="text", call_id=call_id, sample_id=sample_id, round_number=round_number)
    try:
        output = await provider.generate_json(
            prompt_input,
            LayoutPromptDraft,
            temperature=0.4,
            metadata={
                "node_name": "layout_prompt",
                "prompt_asset_name": sample_id,
                "max_output_tokens": 4096,
            },
            refs=[
                make_ref(spatial, "spatial_template", sample_id),
                make_ref(key_vision, "main_visual_style", sample_id),
            ],
        )
        row = {
            "kind": "prompt",
            "sample_id": sample_id,
            "round": round_number,
            "layout_name": round_spec["layout_name"],
            "prop_id": round_spec["prop_id"],
            "candidate_family": candidate["family"],
            "reference_policy": candidate["reference_policy"],
            "status": "success",
            "writer_prompt_path": str(prompt_path.relative_to(lab)).replace("\\", "/"),
            "writer_prompt": prompt_input,
            "final_prompt": output.final_prompt.strip(),
            "model": provider.model,
            "at": utc_now(),
        }
        append_jsonl(lab / "results.jsonl", row)
        record_completion(lab, rows, call_id=call_id, status="success")
        rows.append(row)
        return row
    except Exception as exc:
        row = {
            "kind": "prompt",
            "sample_id": sample_id,
            "round": round_number,
            "layout_name": round_spec["layout_name"],
            "prop_id": round_spec["prop_id"],
            "candidate_family": candidate["family"],
            "reference_policy": candidate["reference_policy"],
            "status": failure_status(exc),
            "writer_prompt_path": str(prompt_path.relative_to(lab)).replace("\\", "/"),
            "writer_prompt": prompt_input,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "at": utc_now(),
        }
        append_jsonl(lab / "results.jsonl", row)
        record_completion(lab, rows, call_id=call_id, status=row["status"])
        rows.append(row)
        return row


async def generate_image(
    lab: Path,
    rows: list[dict[str, Any]],
    provider: AiboxImageProvider,
    media_store: MediaStore,
    *,
    round_spec: dict[str, str],
    candidate: dict[str, str],
    sample_id: str,
    round_number: int,
    spatial: Path,
    key_vision: Path,
    prompt_row: dict[str, Any],
) -> dict[str, Any]:
    existing = latest_stage(rows, "image", sample_id)
    if existing and existing.get("status") != "skipped_prompt_failure":
        return existing
    if prompt_row.get("status") != "success":
        row = {
            "kind": "image",
            "sample_id": sample_id,
            "round": round_number,
            "layout_name": round_spec["layout_name"],
            "prop_id": round_spec["prop_id"],
            "candidate_family": candidate["family"],
            "reference_policy": candidate["reference_policy"],
            "status": "skipped_prompt_failure",
            "error": "No Gemini image prompt was available",
            "at": utc_now(),
        }
        append_jsonl(lab / "results.jsonl", row)
        rows.append(row)
        return row

    call_id = f"img-{sample_id}"
    reserve_call(lab, rows, kind="image", call_id=call_id, sample_id=sample_id, round_number=round_number)
    if candidate["reference_policy"] == "whitebox_only":
        refs = [make_ref(spatial, "spatial_template", sample_id)]
        reference_roles = ["spatial_template"]
    else:
        refs = [
            make_ref(spatial, "spatial_template", sample_id),
            make_ref(key_vision, "main_visual_style", sample_id),
        ]
        reference_roles = ["spatial_template", "main_visual_style"]
    output_path = lab / "images" / f"round-{round_number:02d}" / f"{candidate['family']}.png"
    try:
        result = await provider.generate_image(
            str(prompt_row["final_prompt"]),
            refs=refs,
            size=IMAGE_SIZE,
            metadata={
                "node_name": "layout_image_generation",
                "asset_id": sample_id,
                "model": IMAGE_MODEL,
                "size": IMAGE_SIZE,
                "quality": IMAGE_QUALITY,
                "max_reference_images": len(refs),
            },
        )
        await media_store.write_first_generated_image(lab, output_path, result)
        row = {
            "kind": "image",
            "sample_id": sample_id,
            "round": round_number,
            "layout_name": round_spec["layout_name"],
            "prop_id": round_spec["prop_id"],
            "prop": round_spec["prop"],
            "candidate_family": candidate["family"],
            "candidate_label": candidate["label"],
            "reference_policy": candidate["reference_policy"],
            "reference_roles": reference_roles,
            "status": "success",
            "prompt": prompt_row["final_prompt"],
            "output_path": str(output_path.relative_to(lab)).replace("\\", "/"),
            "output_sha256": sha256(output_path),
            "provider": result.provider,
            "model": result.model,
            "size": IMAGE_SIZE,
            "quality": IMAGE_QUALITY,
            "request_id": result.request_id,
            "task_id": result.task_id,
            "usage": result.usage,
            "at": utc_now(),
        }
        append_jsonl(lab / "results.jsonl", row)
        record_completion(lab, rows, call_id=call_id, status="success", extra={"request_id": result.request_id})
        rows.append(row)
        return row
    except Exception as exc:
        row = {
            "kind": "image",
            "sample_id": sample_id,
            "round": round_number,
            "layout_name": round_spec["layout_name"],
            "prop_id": round_spec["prop_id"],
            "prop": round_spec["prop"],
            "candidate_family": candidate["family"],
            "candidate_label": candidate["label"],
            "reference_policy": candidate["reference_policy"],
            "reference_roles": reference_roles,
            "status": failure_status(exc),
            "prompt": prompt_row["final_prompt"],
            "error_type": type(exc).__name__,
            "error": str(exc),
            "at": utc_now(),
        }
        append_jsonl(lab / "results.jsonl", row)
        record_completion(lab, rows, call_id=call_id, status=row["status"])
        rows.append(row)
        return row


async def audit_image(
    lab: Path,
    rows: list[dict[str, Any]],
    provider: GeminiTextProvider,
    *,
    round_spec: dict[str, str],
    candidate: dict[str, str],
    sample_id: str,
    round_number: int,
    image_row: dict[str, Any],
    spatial: Path,
    key_vision: Path,
    layout: dict[str, Any],
) -> dict[str, Any] | None:
    existing = latest_stage(rows, "audit", sample_id)
    if existing and existing.get("status") == "success":
        return existing
    if image_row.get("status") != "success":
        return None
    target = lab / str(image_row["output_path"])
    prompt = build_audit_input(round_spec, candidate, layout)
    audit_path = lab / "audits" / f"{sample_id}.md"
    audit_path.write_text(prompt, encoding="utf-8", newline="\n")
    attempt = stage_attempt_count(rows, "audit", sample_id)
    call_id = f"txt-audit-{sample_id}" if attempt == 0 else f"txt-audit-{sample_id}-retry-{attempt}"
    reserve_call(lab, rows, kind="text", call_id=call_id, sample_id=sample_id, round_number=round_number)
    try:
        assessment = await provider.generate_json(
            prompt,
            LayoutAuditAssessment,
            temperature=0.1,
            metadata={
                "node_name": "layout_image_audit",
                "prompt_asset_name": sample_id,
                "max_output_tokens": 4096,
            },
            refs=[
                make_ref(target, "target_layout", sample_id),
                make_ref(spatial, "spatial_template", sample_id),
                make_ref(key_vision, "main_visual_style", sample_id),
            ],
        )
        row = {
            "kind": "audit",
            "sample_id": sample_id,
            "round": round_number,
            "layout_name": round_spec["layout_name"],
            "prop_id": round_spec["prop_id"],
            "candidate_family": candidate["family"],
            "status": "success",
            "audit_prompt_path": str(audit_path.relative_to(lab)).replace("\\", "/"),
            **assessment.model_dump(),
            "at": utc_now(),
        }
        append_jsonl(lab / "results.jsonl", row)
        record_completion(lab, rows, call_id=call_id, status="success")
        rows.append(row)
        return row
    except Exception as exc:
        row = {
            "kind": "audit",
            "sample_id": sample_id,
            "round": round_number,
            "layout_name": round_spec["layout_name"],
            "prop_id": round_spec["prop_id"],
            "candidate_family": candidate["family"],
            "status": failure_status(exc),
            "audit_prompt_path": str(audit_path.relative_to(lab)).replace("\\", "/"),
            "error_type": type(exc).__name__,
            "error": str(exc),
            "at": utc_now(),
        }
        append_jsonl(lab / "results.jsonl", row)
        record_completion(lab, rows, call_id=call_id, status=row["status"])
        rows.append(row)
        return row


def round_winner(rows: list[dict[str, Any]], round_number: int) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for row in latest_stage_rows(rows, "audit"):
        if row.get("kind") != "audit" or int(row.get("round", 0)) != round_number or row.get("status") != "success":
            continue
        if row.get("fatal_spatial_inconsistency"):
            continue
        score = (float(row.get("style_alignment", 0)) + float(row.get("spatial_consistency", 0))) / 2
        candidates.append({**row, "mean_score": score})
    if not candidates:
        return None
    return max(candidates, key=lambda row: (float(row["mean_score"]), float(row.get("spatial_consistency", 0))))


def make_round_observations(rows: list[dict[str, Any]], round_number: int) -> list[str]:
    audits = [
        row
        for row in latest_stage_rows(rows, "audit")
        if row.get("kind") == "audit" and int(row.get("round", 0)) == round_number and row.get("status") == "success"
    ]
    audits.sort(key=lambda row: (float(row.get("style_alignment", 0)) + float(row.get("spatial_consistency", 0))) / 2)
    observations: list[str] = []
    for row in audits[:2]:
        observations.append(
            f"{row.get('candidate_family')}: style {float(row.get('style_alignment', 0)):.1f}, "
            f"space {float(row.get('spatial_consistency', 0)):.1f}; "
            f"style evidence: {row.get('style_evidence', '')}; spatial evidence: {row.get('spatial_evidence', '')}"
        )
    return [item[:1200] for item in observations]


async def execute_lab(args: argparse.Namespace) -> dict[str, Any]:
    if not args.confirm_paid_calls:
        raise ValueError("Image calls require --confirm-paid-calls")
    lab = Path(args.lab_dir).expanduser().resolve()
    experiment, config, spatial, key_vision, layout_json = load_lab_inputs(lab)
    if experiment.get("node_group", {}).get("nodes") != ["layout_prompt", "layout_image_generation", "layout_image_audit"]:
        raise ValueError("Frozen lab does not describe the current layout_gen node group")
    result_rows = read_jsonl(lab / "results.jsonl")
    rows = read_jsonl(lab / "ledger.jsonl")
    # Include completed stage rows so an interrupted run resumes without
    # re-dispatching a prompt, image, or audit already recorded in results.
    rows.extend(result_rows)
    settings = load_settings(config)
    gemini = make_gemini_provider(settings)
    image_provider = make_image_provider(settings)
    media_store = MediaStore(ProjectLayout(settings), timeout_seconds=max(300, settings.runtime.request_timeout_seconds))
    layouts = load_real_layouts(layout_json)
    schedule = candidate_schedule(int((experiment.get("budgets") or {}).get("image_calls") or MAX_IMAGE_CALLS))

    for round_spec in ROUND_SPECS:
        round_number = int(round_spec["round"])
        round_candidates = [candidate for planned_round, candidate in schedule if planned_round["round"] == round_spec["round"]]
        if not round_candidates:
            continue
        layout = layouts[round_spec["layout_name"]]
        feedback = prior_feedback(result_rows, round_number)
        for candidate in round_candidates:
            sample_id = f"r{round_spec['round']}-{candidate['family']}"
            prompt_row = await generate_prompt(
                lab,
                rows,
                gemini,
                round_spec=round_spec,
                candidate=candidate,
                sample_id=sample_id,
                round_number=round_number,
                spatial=spatial,
                key_vision=key_vision,
                feedback=feedback,
                layout=layout,
            )
            result_rows.append(prompt_row) if prompt_row not in result_rows else None
            image_row = await generate_image(
                lab,
                rows,
                image_provider,
                media_store,
                round_spec=round_spec,
                candidate=candidate,
                sample_id=sample_id,
                round_number=round_number,
                spatial=spatial,
                key_vision=key_vision,
                prompt_row=prompt_row,
            )
            result_rows.append(image_row) if image_row not in result_rows else None
            audit_row = await audit_image(
                lab,
                rows,
                gemini,
                round_spec=round_spec,
                candidate=candidate,
                sample_id=sample_id,
                round_number=round_number,
                image_row=image_row,
                spatial=spatial,
                key_vision=key_vision,
                layout=layout,
            )
            if audit_row is not None:
                result_rows.append(audit_row) if audit_row not in result_rows else None
            print(
                f"[layout-gen] round={round_number}/5 candidate={candidate['family']} "
                f"image={image_row.get('status')} audit={(audit_row or {}).get('status', 'n/a')}",
                flush=True,
            )

        winner = round_winner(result_rows, round_number)
        previous_summary = next(
            (row for row in reversed(result_rows) if row.get("kind") == "round_summary" and int(row.get("round", 0)) == round_number),
            None,
        )
        current_audit_count = len(
            [
                row
                for row in latest_stage_rows(result_rows, "audit")
                if int(row.get("round", 0)) == round_number and row.get("status") == "success"
            ]
        )
        summary_changed = (
            previous_summary is None
            or previous_summary.get("winner_sample_id") != (winner or {}).get("sample_id")
            or float(previous_summary.get("winner_mean", -1)) != float((winner or {}).get("mean_score", -2))
            or int(previous_summary.get("audit_count", -1)) != current_audit_count
        )
        if winner and summary_changed:
            summary = {
                "kind": "round_summary",
                "round": round_number,
                "layout_name": round_spec["layout_name"],
                "prop_id": round_spec["prop_id"],
                "prop": round_spec["prop"],
                "winner_sample_id": winner["sample_id"],
                "winner_family": winner["candidate_family"],
                "winner_style": round(float(winner["style_alignment"]), 4),
                "winner_spatial": round(float(winner["spatial_consistency"]), 4),
                "winner_mean": round(float(winner["mean_score"]), 4),
                "audit_count": current_audit_count,
                "observations": make_round_observations(result_rows, round_number),
                "at": utc_now(),
            }
            append_jsonl(lab / "results.jsonl", summary)
            result_rows.append(summary)

    experiment["status"] = "executed"
    experiment["completed_at"] = utc_now()
    experiment["image_calls_reserved"] = len(image_reservations(rows))
    current_images = latest_stage_rows(result_rows, "image")
    current_audits = latest_stage_rows(result_rows, "audit")
    experiment["image_calls_completed"] = len(image_reservations(rows))
    experiment["successful_images"] = len([row for row in current_images if row.get("status") == "success"])
    write_json(lab / "experiment.json", experiment)
    output = {
        "lab": str(lab),
        "status": "executed",
        "image_calls_reserved": len(image_reservations(rows)),
        "image_budget": int((experiment.get("budgets") or {}).get("image_calls") or MAX_IMAGE_CALLS),
        "successful_images": experiment["successful_images"],
        "audits": len([row for row in current_audits if row.get("status") == "success"]),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


def embedded_webp(path: Path) -> str:
    with Image.open(path) as source:
        image = source.convert("RGB")
        max_side = 1400
        if max(image.size) > max_side:
            scale = max_side / max(image.size)
            image = image.resize((max(1, int(image.width * scale)), max(1, int(image.height * scale))), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        image.save(buffer, format="WEBP", quality=84, method=6)
    return "data:image/webp;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def score_badges(audit: dict[str, Any] | None) -> str:
    if not audit or audit.get("status") != "success":
        return '<span class="badge muted">未完成审查</span>'
    style = float(audit.get("style_alignment", 0))
    spatial = float(audit.get("spatial_consistency", 0))
    fatal = bool(audit.get("fatal_spatial_inconsistency"))
    kind = "bad" if fatal else "good"
    return (
        f'<span class="badge score">画风 {style:.1f}/10</span>'
        f'<span class="badge score">空间 {spatial:.1f}/10</span>'
        f'<span class="badge {kind}">{"空间硬伤" if fatal else "空间通过"}</span>'
    )


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    lab = Path(args.lab_dir).expanduser().resolve()
    experiment, _config, spatial, key_vision, layout_json = load_lab_inputs(lab)
    ledger = read_jsonl(lab / "ledger.jsonl")
    rows = read_jsonl(lab / "results.jsonl")
    summaries = latest_round_summaries(rows)
    plan = read_json(lab / "round_plan.json")
    schedule = candidate_schedule(int((experiment.get("budgets") or {}).get("image_calls") or MAX_IMAGE_CALLS))
    image_rows = latest_stage_rows(rows, "image")
    successful_images = [row for row in image_rows if row.get("status") == "success"]
    image_attempts = len(image_reservations(ledger))
    prompt_rows = latest_stage_rows(rows, "prompt")
    audit_rows = latest_stage_rows(rows, "audit")
    prompt_failures = [row for row in prompt_rows if row.get("status") != "success"]
    image_failures = [row for row in image_rows if row.get("status") not in {"success", "skipped_prompt_failure"}]
    latest = {(row.get("kind"), row.get("sample_id")): row for row in rows if row.get("kind") in {"prompt", "image", "audit"}}

    def image_src(row: dict[str, Any]) -> str:
        path = lab / str(row["output_path"])
        return embedded_webp(path) if path.is_file() else ""

    cards: list[str] = []
    for round_spec in ROUND_SPECS:
        round_number = int(round_spec["round"])
        round_candidates = [candidate for planned_round, candidate in schedule if planned_round["round"] == round_spec["round"]]
        for candidate in round_candidates:
            sample_id = f"r{round_spec['round']}-{candidate['family']}"
            prompt_row = latest.get(("prompt", sample_id))
            image_row = latest.get(("image", sample_id))
            audit_row = latest.get(("audit", sample_id))
            status = image_row.get("status") if image_row else "not_started"
            image_html = (
                f'<img loading="lazy" src="{image_src(image_row)}" alt="{esc(sample_id)}">'
                if image_row and image_row.get("status") == "success"
                else '<div class="placeholder">本样本未得到可嵌入图片<br><small>图片调用结果已记录在实验日志中</small></div>'
            )
            error_html = ""
            if image_row and image_row.get("status") != "success":
                error_html = f'<p class="error">{esc(image_row.get("error") or image_row.get("status"))}</p>'
            prompt_text = (prompt_row or {}).get("final_prompt") or ""
            writer_text = (prompt_row or {}).get("writer_prompt") or ""
            audit_text = ""
            if audit_row and audit_row.get("status") == "success":
                audit_text = (
                    f"<div class=\"evidence\"><b>画风证据：</b>{esc(audit_row.get('style_evidence'))}</div>"
                    f"<div class=\"evidence\"><b>空间证据：</b>{esc(audit_row.get('spatial_evidence'))}</div>"
                )
            elif audit_row:
                audit_text = f'<p class="error">审查失败：{esc(audit_row.get("error") or audit_row.get("status"))}</p>'
            cards.append(
                f"""<article class="card" data-round="{round_number}">
                  <div class="image">{image_html}</div>
                  <div class="body"><div class="eyebrow">Round {round_number} · {esc(round_spec['prop_id'])}</div>
                  <h3>{esc(candidate['label'])}</h3><p class="meta">{esc(round_spec['layout_name'])} · {esc(candidate['reference_policy'])} · {esc(status)} · {esc(round_spec['prop'])}</p>
                  <div class="badges">{score_badges(audit_row)}</div>{error_html}{audit_text}
                  <details><summary>查看发送给 Image-2 的完整提示词</summary><pre>{esc(prompt_text)}</pre></details>
                  <details><summary>查看 Gemini 的裸模提示模板</summary><pre>{esc(writer_text)}</pre></details></div>
                </article>"""
            )

    summary_rows = []
    for summary in sorted(summaries, key=lambda row: int(row.get("round", 0))):
        summary_rows.append(
            f"<tr><td>{summary.get('round')}</td><td>{esc(summary.get('prop_id'))}</td><td>{esc(summary.get('winner_family'))}</td>"
            f"<td>{float(summary.get('winner_style', 0)):.1f}</td><td>{float(summary.get('winner_spatial', 0)):.1f}</td>"
            f"<td>{float(summary.get('winner_mean', 0)):.2f}</td><td>{esc(' '.join(summary.get('observations') or []))}</td></tr>"
        )

    family_scores: dict[str, list[float]] = {}
    for audit in audit_rows:
        if audit.get("status") != "success" or audit.get("fatal_spatial_inconsistency"):
            continue
        family_scores.setdefault(str(audit.get("candidate_family")), []).append(
            (float(audit.get("style_alignment", 0)) + float(audit.get("spatial_consistency", 0))) / 2
        )
    family_table = "".join(
        f"<tr><td>{esc(family)}</td><td>{len(scores)}</td><td>{sum(scores) / len(scores):.2f}</td><td>{min(scores):.2f}</td></tr>"
        for family, scores in sorted(family_scores.items(), key=lambda item: sum(item[1]) / len(item[1]), reverse=True)
    )
    final_summary = sorted(summaries, key=lambda row: int(row.get("round", 0)))[-1] if summaries else None
    report_path = lab / "report-standalone.html"
    image_budget = int((experiment.get("budgets") or {}).get("image_calls") or MAX_IMAGE_CALLS)
    authorization = experiment.get("authorization") or {}
    diagnostic_path = lab / "runtime_diagnostic.json"
    diagnostic = read_json(diagnostic_path) if diagnostic_path.is_file() else {}
    diagnostic_notice = ""
    if diagnostic:
        diagnostic_notice = (
            f" Gemini 阻断诊断：{esc(diagnostic.get('error_type'))}，{esc(diagnostic.get('error'))}；"
            f"互补视角修订尚未新增图片调用。"
        )
    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>layout_gen · 互补视角修订迭代报告</title>
<style>
:root{{--bg:#0b0e13;--panel:#141a23;--panel2:#1a2230;--line:#2b3647;--text:#edf2f8;--muted:#9aa8bb;--cyan:#7cdde4;--gold:#f2c66d;--green:#7bd9a0;--red:#ef9292;--shadow:0 16px 42px rgba(0,0,0,.28)}}
*{{box-sizing:border-box}}html{{scroll-behavior:smooth}}body{{margin:0;background:radial-gradient(circle at 15% -10%,#243754 0,transparent 33%),var(--bg);color:var(--text);font:15px/1.65 system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}nav{{position:sticky;top:0;z-index:5;display:flex;gap:16px;align-items:center;padding:12px max(20px,calc((100vw - 1440px)/2));background:rgba(11,14,19,.9);backdrop-filter:blur(12px);border-bottom:1px solid var(--line)}}nav strong{{margin-right:auto}}nav a{{color:var(--muted);text-decoration:none;font-size:13px}}main{{max-width:1440px;margin:auto;padding:30px 20px 80px}}header{{padding:38px 0 28px}}.kicker,.eyebrow{{color:var(--cyan);font-size:12px;letter-spacing:.12em;text-transform:uppercase}}h1{{font-size:clamp(34px,5vw,66px);line-height:1.08;max-width:1000px;margin:12px 0 18px}}h2{{font-size:29px;margin:58px 0 18px}}h3{{font-size:20px;margin:4px 0 8px}}.lead{{max-width:920px;font-size:18px;color:#c9d3df}}.metrics{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0}}.metric{{background:linear-gradient(145deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:var(--shadow)}}.metric b{{display:block;color:var(--gold);font-size:29px}}.metric span{{color:var(--muted)}}.callout{{background:#211d12;border:1px solid #655126;border-radius:16px;padding:18px 22px}}.table-wrap{{overflow:auto;border:1px solid var(--line);border-radius:14px}}table{{width:100%;border-collapse:collapse;min-width:720px}}th,td{{text-align:left;padding:11px 13px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--cyan);font-weight:600;background:#111720}}tr:last-child td{{border-bottom:0}}.filters{{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 24px}}button{{background:#151c26;color:var(--muted);border:1px solid var(--line);border-radius:999px;padding:8px 14px;cursor:pointer}}button.active,button:hover{{background:var(--cyan);color:#071014;border-color:var(--cyan)}}.gallery{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}.card{{overflow:hidden;background:var(--panel);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}}.card.hidden{{display:none}}.image{{background:#0a0d12;min-height:280px;display:flex;align-items:center;justify-content:center}}.image img{{display:block;width:100%;height:auto;max-height:720px;object-fit:contain}}.placeholder{{height:280px;display:grid;place-items:center;text-align:center;color:var(--muted);padding:20px}}.body{{padding:17px 18px 20px}}.meta{{color:var(--muted);font-size:13px;margin:0 0 12px}}.badges{{display:flex;flex-wrap:wrap;gap:7px;margin:9px 0 12px}}.badge{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:3px 9px;font-size:12px;color:var(--muted)}}.badge.score{{color:var(--gold);border-color:#66532b}}.badge.good{{color:var(--green);border-color:#2d6848}}.badge.bad{{color:var(--red);border-color:#713f44}}.badge.muted{{color:var(--muted)}}.evidence{{border-left:2px solid #3c7181;padding:5px 0 5px 10px;margin:7px 0;color:#c7d2df}}.error{{color:var(--red);white-space:pre-wrap;word-break:break-word}}details{{margin-top:12px;border-top:1px solid var(--line);padding-top:9px}}summary{{cursor:pointer;color:var(--cyan)}}pre{{white-space:pre-wrap;word-break:break-word;background:#0e131b;border:1px solid #232e3d;border-radius:10px;padding:13px;max-height:420px;overflow:auto;font:13px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace;color:#d7e0eb}}.inputs{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px}}.input-card{{background:var(--panel);border:1px solid var(--line);border-radius:16px;overflow:hidden}}.input-card img{{width:100%;display:block}}.input-card p{{padding:0 15px;color:var(--muted);font-size:13px}}footer{{margin-top:60px;color:var(--muted);border-top:1px solid var(--line);padding-top:18px}}@media(max-width:900px){{.metrics,.inputs{{grid-template-columns:repeat(2,1fr)}}.gallery{{grid-template-columns:1fr}}}}@media(max-width:560px){{.metrics,.inputs{{grid-template-columns:1fr}}nav{{gap:9px;overflow:auto}}nav a{{white-space:nowrap}}}}
</style></head><body>
<nav><strong>layout_gen evolution lab</strong><a href="#summary">结论</a><a href="#rounds">轮次</a><a href="#gallery">图片</a><a href="#inputs">输入</a><a href="#protocol">协议</a></nav>
<main><header id="summary"><div class="kicker">Standalone · layout_prompt → layout_image_generation → layout_image_audit</div>
<h1>五轮场景空间锚点提示词迭代 · 互补视角修订</h1>
<p class="lead">Gemini 编译场景生图提示词，GPT-Image-2-guan 生成同一场景的上下两视图，Gemini 只审查主视觉画风协调性与两视图空间一致性。本次修订额外要求真实反向机位、露出不同表面、路线方向反转，并禁止水平镜像。每轮使用不同道具，图片授权上限为 20 张。</p>
<div class="metrics"><div class="metric"><b>{image_attempts} / {image_budget}</b><span>本实验图片调用 / 本实验额度</span></div><div class="metric"><b>{len(successful_images)}</b><span>成功图片</span></div><div class="metric"><b>{len(audit_rows)}</b><span>最新审查记录</span></div><div class="metric"><b>{len(prompt_rows) - len(prompt_failures)}</b><span>成功 Gemini 提示词</span></div></div>
<div class="callout"><b>输入与运行审计：</b>修正版以生产真实 layout JSON 为场景内容来源；白模只负责双视图空间锚点图的表现形式，不复制其中的院落拓扑。用户确认的附件作为主视觉 style reference。已消耗的前实验无效图片：{authorization.get('prior_invalid_image_calls', 0)}；本实验实际图片调用：{image_attempts}/{image_budget}；总授权上限：{authorization.get('global_image_calls', MAX_IMAGE_CALLS)}。本轮还有 {len(prompt_failures)} 个样本未获得 Gemini 提示词、{len(image_failures)} 个图片调用失败，未完成样本不会被当作合格结果。{('当前最后完整轮次胜者：' + esc(final_summary.get('winner_family')) + '，均值 ' + f"{float(final_summary.get('winner_mean', 0)):.2f}/10。") if final_summary else '实验尚未形成完整轮次结论。'}{diagnostic_notice}</div></header>
<section id="rounds"><h2>轮次结论</h2><div class="table-wrap"><table><thead><tr><th>轮次</th><th>本轮道具</th><th>胜者</th><th>画风</th><th>空间</th><th>均值</th><th>审查观察</th></tr></thead><tbody>{''.join(summary_rows) or '<tr><td colspan="7">暂无完整轮次结论</td></tr>'}</tbody></table></div>
<h2>候选族总体表现</h2><div class="table-wrap"><table><thead><tr><th>提示策略</th><th>有效审查数</th><th>均值</th><th>最低均值</th></tr></thead><tbody>{family_table or '<tr><td colspan="4">暂无有效审查</td></tr>'}</tbody></table></div></section>
<section id="gallery"><h2>{len(schedule)} 个修正版样本：图片、提示词与审查证据</h2><p>图片卡片内的 Image-2 提示词就是 Gemini 输出后实际发送的裸模生图提示词；Gemini 裸模模板只保留内容任务，不包含项目字段、文件路径或 skill 式流程说明。</p><div class="filters"><button class="active" data-filter="all">全部</button>{''.join(f'<button data-filter="{i}">Round {i}</button>' for i in range(1, ROUND_COUNT + 1))}</div><div class="gallery">{''.join(cards)}</div></section>
<section id="inputs"><h2>冻结输入</h2><div class="inputs"><div class="input-card"><img src="{embedded_webp(spatial)}" alt="scene spatial anchor white model"><p>白模：{esc(spatial)}<br>只用于双视图空间锚点图的版式、视角和一致性表达，不作为目标院落拓扑。</p></div><div class="input-card"><img src="{embedded_webp(key_vision)}" alt="user confirmed main visual style"><p>主视觉风格：用户确认的附件快照<br>用于画风、材质、光照、色彩和完成度协调性。</p></div></div><details><summary>查看生产实际 layout JSON</summary><pre>{esc((lab / str(experiment['inputs']['layout_json'])).read_text(encoding='utf-8'))}</pre></details></section>
<section id="protocol"><h2>实验协议与输入合同</h2><details open><summary>查看实验协议</summary><pre>{esc((lab / 'experiment_protocol.md').read_text(encoding='utf-8'))}</pre></details><details><summary>查看场景与候选计划</summary><pre>{esc(json.dumps(plan, ensure_ascii=False, indent=2))}</pre></details><details><summary>查看节点组合同</summary><pre>{esc(json.dumps(experiment.get('node_group'), ensure_ascii=False, indent=2))}</pre></details></section>
<footer>单文件报告，无外部图片、字体、脚本或网络依赖。生成时间：{esc(utc_now())}。图片错误和传输失败均保留在 .tmp 实验目录的 results.jsonl 中。</footer>
</main><script>document.querySelectorAll('[data-filter]').forEach(btn=>btn.addEventListener('click',()=>{{document.querySelectorAll('[data-filter]').forEach(x=>x.classList.remove('active'));btn.classList.add('active');const f=btn.dataset.filter;document.querySelectorAll('.card[data-round]').forEach(x=>x.classList.toggle('hidden',f!=='all'&&x.dataset.round!==f));}}));</script></body></html>"""
    if re.search(r'(?:src|href)="(?!data:|#)', document):
        raise RuntimeError("Standalone report contains an external or local src/href")
    report_path.write_text(document, encoding="utf-8", newline="\n")
    output = {
        "report": str(report_path),
        "bytes": report_path.stat().st_size,
        "image_calls": image_attempts,
        "successful_images": len(successful_images),
        "audits": len(audit_rows),
        "embedded_images": len(successful_images) + 2,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


def inspect_lab(args: argparse.Namespace) -> dict[str, Any]:
    contract = group_contract()
    config = ensure_file(Path(args.config), "config")
    spatial = ensure_file(Path(args.spatial_template), "spatial white-model reference")
    key_vision = ensure_file(Path(args.key_vision), "user-confirmed main-visual reference")
    layout_json = ensure_file(Path(args.layout_json), "production extracted layout JSON")
    lab = Path(args.lab_dir).expanduser().resolve()
    output: dict[str, Any] = {
        "node_group": contract,
        "config": str(config),
        "spatial_template": str(spatial),
        "key_vision": str(key_vision),
        "layout_json": str(layout_json),
        "lab_exists": lab.is_dir(),
        "paid_calls_dispatched_by_inspect": 0,
    }
    if lab.is_dir() and (lab / "experiment.json").is_file():
        experiment = read_json(lab / "experiment.json")
        ledger = read_jsonl(lab / "ledger.jsonl")
        results = read_jsonl(lab / "results.jsonl")
        output.update(
            {
                "lab_status": experiment.get("status"),
                "reserved_image_calls": len(image_reservations(ledger)),
                "image_results": len([row for row in results if row.get("kind") == "image"]),
                "successful_images": len([row for row in results if row.get("kind") == "image" and row.get("status") == "success"]),
                "report_exists": (lab / "report-standalone.html").is_file(),
            }
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ledgered smoke harness for AutoDrama layout_gen node-group evolution")
    parser.add_argument("--lab-dir", default=str(LAB_DEFAULT))
    parser.add_argument("--config", default=str(CONFIG_DEFAULT))
    parser.add_argument("--spatial-template", default=str(SPATIAL_TEMPLATE_DEFAULT))
    parser.add_argument("--key-vision", default=str(KEY_VISION_ATTACHMENT_DEFAULT))
    parser.add_argument("--layout-json", default=str(LAYOUT_JSON_DEFAULT))
    parser.add_argument("--image-budget", type=int, default=MAX_IMAGE_CALLS)
    parser.add_argument("--prior-image-calls", type=int, default=0)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("inspect", help="Validate the node-group contract and frozen-input readiness")
    subparsers.add_parser("prepare", help="Freeze the two references and the production layout JSON")
    execute = subparsers.add_parser("execute", help="Run multiple rounds within the recorded image budget")
    execute.add_argument("--confirm-paid-calls", action="store_true")
    subparsers.add_parser("report", help="Build the self-contained HTML report")
    run = subparsers.add_parser("run", help="Prepare, execute, and build the report")
    run.add_argument("--confirm-paid-calls", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "inspect":
        inspect_lab(args)
        return 0
    if args.command == "prepare":
        prepare_lab(args)
        return 0
    if args.command == "execute":
        import asyncio

        asyncio.run(execute_lab(args))
        return 0
    if args.command == "report":
        build_report(args)
        return 0
    if args.command == "run":
        import asyncio

        prepare_lab(args)
        asyncio.run(execute_lab(args))
        build_report(args)
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
