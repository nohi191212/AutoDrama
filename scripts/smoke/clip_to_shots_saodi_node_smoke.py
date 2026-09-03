from __future__ import annotations

import asyncio
import json
import shutil
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import (  # noqa: E402
    ClipSegment,
    ClipSegmentOutput,
    ClipToShotsEpisodeOutput,
    Layout,
    ProjectState,
    Prop,
    PropAsset,
    Role,
    RoleAppearance,
    ScriptBundle,
)
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.nodes.shot_asset_nodes import build_shot_asset_nodes  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


CONFIG_PATH = ROOT / "saodi.yaml"
SMOKE_ROOT = ROOT / ".tmp" / "clip_to_shots_saodi_node_smoke"
PROJECT_DIR = SMOKE_ROOT / "project"
SCENE_SOURCE = ROOT / "outputs" / "huyao" / "assets" / "images" / "layouts" / "layout_九尾狐庭院.png"
SCENE_RELATIVE_PATH = Path("assets/images/layouts/layout_九尾狐庭院.png")
EPISODE_KEY = "episode_001"
TARGET_SECONDS = 27


CLIP_TEXT = (
    "九韶（语气呆板）：「乐园场景已呈现，现在请求为宿主解释乐园规则。」 "
    "△江未晞还盯着指尖残留的花粉，像没完全从震撼里回神。她迟钝地点了点头，视线却仍扫着四周逼真的庭院。 "
    "江未晞（呆呆地点头）：「啊……你直接说就行。」 "
    "九韶（严肃冰山脸）：「乐园中，玩家将会通过不断触发秘境支线，完成剧情任务，进而解锁下一层的密室。"
    "比如在九尾狐之约这个密室中，游客需要根据线索躲避狐妖的魅惑与追捕，找到离开庭院的生门。比如……」 "
    "△九韶抬手，指向庭院一侧。 △江未晞顺着他的指尖看去，只见一盏石灯静静立在花木旁。"
    "石灯外表普通，表面覆着薄薄青苔，灯腔里没有火，只有一点极淡的幽光藏在石缝深处，若不细看几乎会被忽略。 "
    "九韶（指向庭院中一个看似普通的石灯）："
    "「游客触碰此机关后会被迷惑，如不及时挣脱，就会被隐藏在假山后的狐爪抓住。」"
)


class CapturingDirectorService:
    """Observe the real service result without changing its prompt or API call."""

    def __init__(self, delegate: Any) -> None:
        self.delegate = delegate
        self.raw_output: dict[str, Any] | None = None
        self.call_inputs: dict[str, Any] = {}

    async def clip_to_shots(self, *args: Any, **kwargs: Any) -> Any:
        scene_ref = kwargs.get("scene_ref")
        self.call_inputs = {
            "foreground_reference_budget": kwargs.get("foreground_reference_budget"),
            "scene_reference_id": getattr(scene_ref, "id", None),
            "scene_reference_path": getattr(scene_ref, "path", None),
            "entity_index": kwargs.get("entity_index"),
        }
        output = await self.delegate.clip_to_shots(*args, **kwargs)
        self.raw_output = output.model_dump(mode="json")
        return output

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


def role(role_id: str, name: str) -> Role:
    appearance = RoleAppearance(
        id=f"{role_id}_appearance_base",
        role_id=role_id,
        name="base",
        identity_invariants=[f"stable screen identity for {name}"],
        asset_id=f"{role_id}_appearance_base_roleboard",
    )
    return Role(
        id=role_id,
        name=name,
        intro=name,
        episode_keys=[EPISODE_KEY],
        appearances={"base": appearance},
    )


def build_state() -> ProjectState:
    stone_lantern_asset = PropAsset(
        id="prop_机关石灯__base",
        prop_id="prop_机关石灯",
        name="base",
        desc="覆有薄青苔、灯腔无火、石缝深处藏着极淡幽光的庭院机关石灯",
        asset_id="prop_机关石灯__base",
    )
    stone_lantern = Prop(
        id="prop_机关石灯",
        name="机关石灯",
        intro="庭院一侧看似普通、实际会迷惑游客的机关石灯",
        episode_keys=[EPISODE_KEY],
        assets={"base": stone_lantern_asset},
    )
    courtyard = Layout(
        id="layout_九尾狐庭院",
        name="九尾狐庭院",
        desc=(
            "入口视角下的古典围合庭院；弯曲青石路通向右后方灯笼照亮的亭子，"
            "右前景固定一盏覆苔石灯，假山分列道路两侧，左侧花枝探入画面"
        ),
        prompt="fixed spatial anchor for a classical fox-spirit courtyard",
        episode_keys=[EPISODE_KEY],
        space_features=[
            "foreground entrance gate",
            "curved flagstone path",
            "mossy stone lantern at right foreground",
            "rockeries flanking the path",
            "pavilion steps and red lanterns at upper-right rear",
        ],
        asset_id="layout_九尾狐庭院",
        asset_path=SCENE_RELATIVE_PATH.as_posix(),
    )
    return ProjectState(
        project_id="clip_to_shots_saodi_node_smoke",
        title="clip_to_shots saodi real-node acceptance",
        raw_script=CLIP_TEXT,
        script=ScriptBundle(
            raw_script=CLIP_TEXT,
            episode_outlines={EPISODE_KEY: CLIP_TEXT},
        ),
        roles={
            "role_江未晞": role("role_江未晞", "江未晞"),
            "role_九韶": role("role_九韶", "九韶"),
        },
        props={stone_lantern.id: stone_lantern},
        layouts={courtyard.id: courtyard},
        metadata={
            "episode_count": 1,
            "episode_duration_seconds": TARGET_SECONDS,
            "visual_style_prompt": "cinematic grounded fantasy drama with natural performance",
        },
    )


def prepare_project(repo: ProjectRepository, state: ProjectState) -> None:
    resolved_root = SMOKE_ROOT.resolve()
    expected_parent = (ROOT / ".tmp").resolve()
    if expected_parent not in resolved_root.parents:
        raise RuntimeError(f"refusing to prepare smoke directory outside .tmp: {resolved_root}")
    if SMOKE_ROOT.exists():
        shutil.rmtree(SMOKE_ROOT)
    scene_target = PROJECT_DIR / SCENE_RELATIVE_PATH
    scene_target.parent.mkdir(parents=True, exist_ok=True)
    if not SCENE_SOURCE.is_file():
        raise FileNotFoundError(f"frozen scene anchor is missing: {SCENE_SOURCE}")
    shutil.copy2(SCENE_SOURCE, scene_target)
    repo.write_json(repo.layout.state_path(PROJECT_DIR), state)
    repo.write_json(
        repo.layout.node_episode_output_path(PROJECT_DIR, "clip_segment", EPISODE_KEY),
        ClipSegmentOutput(
            {
                "clip_001": ClipSegment(
                    text=CLIP_TEXT,
                    scene_id="layout_九尾狐庭院",
                    role_names=["江未晞", "九韶"],
                    prop_names=["机关石灯"],
                    allocated_seconds=float(TARGET_SECONDS),
                    event_ids=["event_rule_exposition"],
                    required_beats=[
                        "九韶正式请求解释规则，江未晞仍被真实庭院吸引",
                        "江未晞迟钝应允，九韶完整说明支线、任务、密室、魅惑追捕与生门",
                        "九韶的手势把江未晞视线引向庭院一侧的机关石灯",
                        "普通覆苔石灯与几乎不可见的幽光被清晰揭示",
                        "九韶说明触碰后的迷惑与假山后狐爪后果",
                    ],
                    coverage_goal="listener-aware exposition with motivated eyeline and object reveal",
                    pace_class="measured_exposition",
                )
            }
        ),
    )


def audit_result(
    raw_output: dict[str, Any],
    plan: ClipToShotsEpisodeOutput,
    call_inputs: dict[str, Any],
    settings: Any,
) -> dict[str, Any]:
    expected_shot_fields = {
        "video_prompt",
        "ref_ids",
        "duration_seconds",
    }
    assert set(raw_output) == {"shots"}
    raw_shots = raw_output["shots"]
    assert raw_shots and all(set(shot) == expected_shot_fields for shot in raw_shots)
    assert all(1 <= int(shot["duration_seconds"]) <= 15 for shot in raw_shots)
    shots = [shot for clip in plan.clips for shot in clip.shots]
    assert shots
    assert all(1 <= shot.duration_seconds <= 15 for shot in shots)
    assert [shot.duration_seconds for shot in shots] == [int(shot["duration_seconds"]) for shot in raw_shots]
    assert all(1 + len(shot.ref_ids) <= 4 for shot in shots)
    forbidden_internal_cuts = (
        "cut to",
        "then cut",
        "cut-in",
        "reverse angle",
        "montage",
        "second viewpoint",
        "切到",
        "反打",
        "蒙太奇",
    )
    searchable = [shot.video_prompt.casefold() for shot in shots]
    assert not any(token in text for token in forbidden_internal_cuts for text in searchable)

    return {
        "status": "accepted",
        "config_path": str(CONFIG_PATH),
        "config_filename": CONFIG_PATH.name,
        "configured_model": settings.nodes["clip_to_shots"].model,
        "configured_params": dict(settings.nodes["clip_to_shots"].params),
        "scene_reference_count": 1,
        "scene_reference_id": call_inputs["scene_reference_id"],
        "model_contract": "lean shots-only JSON",
        "raw_top_level_fields": sorted(raw_output),
        "raw_shot_field_count": len(expected_shot_fields),
        "raw_shot_count": len(raw_shots),
        "compiled_shot_count": len(shots),
        "raw_durations": [shot["duration_seconds"] for shot in raw_shots],
        "compiled_durations": [shot.duration_seconds for shot in shots],
        "natural_total_duration_seconds": sum(shot.duration_seconds for shot in shots),
        "external_duration_budget_passed_to_model": False,
        "reference_counts_including_scene": [1 + len(shot.ref_ids) for shot in shots],
        "internal_cut_rejected_by_contract": True,
        "adjacent_shot_transition_default": "cut",
        "single_setup_per_shot": True,
    }


async def main_async() -> int:
    settings = load_settings(CONFIG_PATH)
    settings.output.root_dir = SMOKE_ROOT / "outputs"
    settings.generation.expected_output_seconds = -1
    repo = ProjectRepository(settings)
    state = build_state()
    prepare_project(repo, state)

    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(SMOKE_ROOT)
    workflow = PregenWorkflow(repo=repo, router=router)
    capture = CapturingDirectorService(workflow.director_service)
    workflow.director_service = capture
    node = next(item for item in build_shot_asset_nodes(workflow) if item.name == "clip_to_shots")
    await node.run(PROJECT_DIR, state)

    if capture.raw_output is None:
        raise RuntimeError("real clip_to_shots service returned no captured model output")
    output_path = repo.layout.node_episode_output_path(PROJECT_DIR, "clip_to_shots", EPISODE_KEY)
    plan = ClipToShotsEpisodeOutput.model_validate_json(output_path.read_text(encoding="utf-8"))
    summary = audit_result(capture.raw_output, plan, capture.call_inputs, settings)

    (SMOKE_ROOT / "raw_model_output.json").write_text(
        json.dumps(capture.raw_output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (SMOKE_ROOT / "call_inputs.json").write_text(
        json.dumps(capture.call_inputs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    (SMOKE_ROOT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
