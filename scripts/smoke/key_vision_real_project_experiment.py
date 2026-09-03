from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    KeyVisionPromptOutput,
    ProjectState,
    StaticAssetGenerationItem,
)
from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.image_audit_rubrics import (  # noqa: E402
    KEY_VISION_AUDIT_DIMENSION_IDS,
    ProductionRubricBundle,
    load_production_rubric_bundle,
)
from autodrama.providers.base import AssetRef  # noqa: E402
from autodrama.providers.router import ProviderRouter  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.services.director_service import DirectorService  # noqa: E402
from autodrama.services.media_store import MediaStore  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.image_audit_nodes import (  # noqa: E402
    KeyVisionAuditDecision,
    KeyVisionImageAuditNode,
)
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


PROJECT_DIR = ROOT / "outputs" / "saodi_0803"
LAB = ROOT / ".tmp" / "key-vision-real-project-experiment-20260806"
EXPERIMENT_DOC = PROJECT_DIR / "docs" / "key_vision_prompt_edit_experiment.md"
REFERENCE_STYLE_IMAGE = Path(r"C:\Users\csh10\AppData\Local\Temp\codex-clipboard-8ca3fa6f-d181-455a-b47d-aa0c1bb5c1e7.png")
MAX_IMAGE_BUDGET = 200
EXPERIMENT_PROVIDER_ATTEMPTS = 4
EXPERIMENT_IMAGE_ATTEMPTS = 5

REFERENCE_STYLE_LANGUAGE = (
    "Semi-realistic cinematic Eastern xuanhuan 3D CG donghua production frame with natural adult "
    "proportions, believable cloth weight, softly stylized mature faces, restrained realistic materials, "
    "warm directional light with cool mountain ambience, gentle atmospheric depth, and clean modeled "
    "silhouettes. Clearly rendered 3D animation rather than live-action photography; no real actor, DSLR "
    "look, skin pores, film grain, hyper-real microtexture, dominant bokeh, plastic gloss, flat 2D "
    "illustration, obvious cartoon outlines, or flat cel shading."
)


VARIANTS: dict[str, dict[str, str]] = {
    "v01_inventory_locked": {
        "label": "短 prompt + 现实/闪回道具库存锁定",
        "focus": (
            "The present-day frame contains Ye Fan, Li Dehai, the gate, the stone stairs, "
            "and Ye Fan's wooden walking staff only. The red-gold furnace belongs only to the "
            "eighty-years-ago flashback and must not appear in the present frame."
        ),
        "shot": (
            "Use a moderate 65mm equivalent medium-long full-body three-quarter view from the "
            "inner courtyard. Freeze the moment immediately after Ye Fan's leading foot has "
            "landed, with the trailing foot still clearly behind the threshold; show both feet "
            "in a readable scale plane and never enlarge the nearest foot."
        ),
    },
    "v02_style_first": {
        "label": "媒介风格前置 + 简化动作",
        "focus": (
            "The first and strongest instruction must be a clearly computer-rendered stylized "
            "Eastern xuanhuan 3D CG animation production frame, unmistakably not live-action "
            "photography. Keep clean donghua silhouettes and normal adult anatomy."
        ),
        "shot": (
            "Use a stable full-body 55mm shot at chest height. Show Ye Fan entering through the "
            "gate with a small natural weight transfer, but do not require a lifted heel, exact "
            "centimeter measurements, extreme foreground foreshortening, or a close shoe."
        ),
    },
    "v03_architecture_scale": {
        "label": "建筑/人物尺度优先",
        "focus": (
            "Make the gate, threshold, stairs, courtyard floor, Ye Fan, and Li Dehai share one "
            "continuous perspective and believable scale. The protagonist must remain a normal "
            "adult figure of about seven-and-a-half heads tall even in the foreground."
        ),
        "shot": (
            "Use a slightly wider 70mm-equivalent full-body composition with visible margins above "
            "the head and below both feet. Keep Ye Fan in the center-right but do not let one limb "
            "or foot dominate the frame."
        ),
    },
    "v04_completed_crossing": {
        "label": "完成跨门动作，降低动作拓扑风险",
        "focus": (
            "The dramatic instant is the first calm beat after Ye Fan has crossed the outer gate: "
            "he is still moving forward, but both feet are fully on the inner courtyard floor. "
            "Do not depict a complicated mid-step or a foot hidden in the robe."
        ),
        "shot": (
            "Use a balanced 60mm full-body shot from inside the courtyard. Keep the threshold "
            "visible behind him, the wooden staff clearly contacting the ground, and Li Dehai "
            "separated in the exterior background."
        ),
    },
    "v05_staff_action": {
        "label": "恢复木杖接触链，移除错误炉子",
        "focus": (
            "The required present-day prop is the wooden walking staff: one hand visibly grips it, "
            "the lower tip visibly contacts the stone floor, and the contact chain is simple. "
            "Do not add the flashback furnace, weapons, extra props, or magical effects."
        ),
        "shot": (
            "Use a three-quarter 50mm full-body production-frame view. Keep both legs and the staff "
            "silhouette-separated against the courtyard, with a natural walking pose and moderate "
            "perspective rather than a dramatic low-angle near-foot view."
        ),
    },
    "v06_side_blocking": {
        "label": "侧向调度，验证是否为近景正面透视主因",
        "focus": (
            "Use a side-biased three-quarter view that makes the walking direction and the gate "
            "boundary easy to read. Preserve the exact present-day story facts and normal adult "
            "body proportions."
        ),
        "shot": (
            "Use a 70mm full-body side-biased frame at 1.5m camera height. Keep the subject at a "
            "moderate distance, both feet visible, the staff grounded, and no body part enlarged "
            "by proximity to the lens."
        ),
    },
    "v07_stylized_medium": {
        "label": "替换摄影化渲染词，保持空间与库存约束",
        "focus": (
            "Keep the exact present-day prop inventory, normal adult anatomy, continuous gate space, "
            "and readable staff contact. Do not add a furnace or any flashback content."
        ),
        "shot": (
            "Use a moderate 60mm full-body three-quarter frame at chest height with both feet visible "
            "on the same continuous ground plane. Favor a readable production-frame composition over "
            "a glamorous portrait or a close foreground limb."
        ),
        "style_override": (
            "Clearly computer-rendered Eastern xuanhuan 3D donghua animation production frame, not a "
            "photograph. Use mature stylized 3D character models with clean graphic facial planes, "
            "slightly simplified forms, controlled toon-like 3D shading, matte-to-satin cloth, and "
            "clear scene-native color separation. Keep normal adult anatomy and believable 3D depth, "
            "but avoid photographic skin pores, DSLR or live-action cinematography, film grain, lens "
            "bokeh, hyper-real texture, glossy skin, or shallow-focus portrait styling."
        ),
    },
    "v08_production_template": {
        "label": "正式 key_vision_prompt 模板 + 新 xuanhuan-v1",
        "focus": "Use the production prompt template exactly; this branch is a template regression check.",
        "shot": "Keep the production template's resolved shot rules and do not add branch-specific story facts.",
        "production_template": "true",
    },
    "v09_reference_style": {
        "label": "参考图方向：半写实电影化 3D 国漫",
        "focus": "Match the reference direction: natural adult proportions, believable cloth weight, quiet cinematic atmosphere, soft mountain depth, and a side-biased walking frame. Keep the present-day inventory locked: wooden staff present, flashback furnace absent.",
        "shot": "Use a moderate 60mm three-quarter full-body frame from the destination side, with Ye Fan moving across the gate, the leading foot not close to the lens, the staff grounded, and Li Dehai separated in the exterior background.",
        "style_override": "Semi-realistic stylized Eastern xuanhuan 3D CG donghua production frame with natural adult anatomy, believable cloth weight, softly stylized faces, clean modeled silhouettes, restrained texture, atmospheric Chinese mountain architecture, gentle background separation, and quiet warm/cool cinematic daylight. Clearly rendered 3D animation rather than live-action photography; no real actor, DSLR look, skin pores, film grain, hyper-real microtexture, dominant bokeh, plastic gloss, flat 2D illustration, obvious cartoon outlines, or flat cel shading.",
    },
    "v10_reference_image": {
        "label": "参考图 image reference + 半写实 3D 国漫",
        "focus": "Use the supplied reference image as a visual reference for the semi-realistic 3D donghua medium, quiet light, cloth weight, gate framing, side-biased walking pose, staff contact, and background mountain depth. Preserve the real story facts: present-day Ye Fan and Li Dehai only, wooden staff present, flashback furnace absent.",
        "shot": "Use a moderate side-biased three-quarter full-body frame modeled on the reference composition: Ye Fan moves across the threshold rather than facing squarely forward; the nearer foot remains moderate in scale, both legs stay readable, the staff touches the ground, and Li Dehai stays separated behind him.",
        "style_override": "Use the supplied image as a style and composition reference. Semi-realistic cinematic Eastern xuanhuan 3D CG donghua production frame: natural adult proportions, believable cloth weight, softly stylized mature faces, restrained realistic materials, warm directional light with cool mountain ambience, and gentle atmospheric background depth. It must remain a rendered 3D animation scene, not live-action photography, with no actor, DSLR look, skin pores, film grain, hyper-real microtexture, dominant bokeh, plastic gloss, flat 2D illustration, obvious cartoon outlines, or flat cel shading.",
        "use_style_reference": "true",
    },
    "v11_side_threshold": {
        "label": "侧面门槛横向调度",
        "focus": "Present-day frame only: Ye Fan, Li Dehai, the gate, stone threshold, courtyard, mountain stairs, and Ye Fan's wooden staff. The flashback furnace is absent. Prioritize a natural adult silhouette and clear walking direction over a frontal portrait.",
        "shot": "Camera stands inside the destination courtyard at chest height, side-biased across the gate opening. Ye Fan crosses laterally from the exterior threshold toward the courtyard, shown in a readable three-quarter profile with both feet on the same moderate perspective plane. Li Dehai remains smaller behind the gate in the exterior zone. Keep the nearest foot no larger than the head-and-torso scale relationship allows.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v12_wide_establishing": {
        "label": "宽幅建筑建立镜头",
        "focus": "Present-day entry at the Tianjian Sect gate. Show the architecture as a believable scale reference, with Ye Fan and Li Dehai as two clearly readable adult figures. Wooden staff present; no furnace, flashback, extra people, or magic.",
        "shot": "Use a distant moderate-lens full-body establishing composition from the inner courtyard, with the entire gate opening, continuous flagstones, threshold, mountain stairs, and roofline visible. Place Ye Fan off-center while he takes one calm step into the courtyard; keep both feet and staff contact readable without enlarging any foreground limb. Li Dehai stays separated on the exterior stair landing.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v13_inner_diagonal": {
        "label": "内院斜向穿门",
        "focus": "Use one continuous present-day space and the exact story inventory. The wooden staff is the only active prop. The red-gold furnace belongs to the old flashback and must not appear here.",
        "shot": "Camera stands near the inner courtyard wall, looking diagonally across the threshold toward the exterior mountain stairs. Frame Ye Fan at center-left in a three-quarter walking pose, moving from the gate toward the right side of the courtyard; use a visible diagonal boundary and a clear staff-to-ground contact. Keep Li Dehai deep in the exterior background, smaller and silhouette-separated.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v14_door_leaf_foreground": {
        "label": "门扇前景遮挡构图",
        "focus": "Make the open timber door leaf a framing element, not a second scene. Keep the present-day prop inventory exact: Ye Fan's wooden staff only, with no furnace or flashback montage.",
        "shot": "Place the camera just outside and to the side of the gate at adult chest height. One door leaf enters the near left edge as a controlled architectural occlusion; Ye Fan is fully visible in the middle distance stepping across the sill, with both feet readable and no extreme near-foot perspective. Li Dehai is visible through the opening behind him in the exterior zone.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v15_exterior_stair_profile": {
        "label": "外侧台阶侧逆光",
        "focus": "Present-day threshold decision: Ye Fan enters the sect while Li Dehai watches from behind. Preserve normal adult body scale, the wooden staff, one gate, one courtyard, and one connected mountain stair. Do not add the flashback furnace.",
        "shot": "Camera stands on the exterior mountain stair landing, slightly to Ye Fan's side and facing the gate interior. Use soft warm backlight from the courtyard and cool mountain ambience outside. Capture Ye Fan in a medium-distance full-body profile as his leading foot reaches the courtyard floor; maintain visible sill separation and avoid a low-angle shoe close-up. Li Dehai stays farther up the exterior stairs.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v16_rear_three_quarter": {
        "label": "内院后方三分之四",
        "focus": "Use a quiet in-episode frame immediately after Ye Fan begins entering: the same gate, courtyard, mountain stairs, Li Dehai, and wooden staff only. No furnace, no flashback, no extra spectacle.",
        "shot": "Camera stands a few paces inside the destination courtyard behind and to the side of Ye Fan, at chest height. Show his rear three-quarter silhouette moving forward through the gate, the staff planted beside him, and Li Dehai visible beyond the threshold in the exterior background. Keep enough face profile and both feet readable to preserve identity and action, with moderate distance and no enlarged foreground limb.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v17_high_oblique_architecture": {
        "label": "高位斜俯建筑尺度",
        "focus": "Present-day gate crossing only. Let the Tianjian Sect outer gate, continuous stone paving, threshold, and mountain stair geometry establish scale. Keep Ye Fan, Li Dehai, and the wooden staff as the only story evidence; the flashback furnace and all extra spectacle are absent.",
        "shot": "Use a high oblique camera from the inner gate eave, looking diagonally down across the courtyard floor toward the exterior stair landing. Show the whole gate opening and a large readable ground plane. Ye Fan is a normal-sized full-body figure just beyond the sill, moving inward with a modest weight transfer; Li Dehai is smaller and separated on the far landing. Keep both feet on the same visible paving system and do not use a near-foot close-up or a distorted top-down body.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v18_corridor_shadow_line": {
        "label": "廊下阴影分界",
        "focus": "Use a quiet present-day beat under the gate's covered inner eave. The frame contains one continuous entrance space, Ye Fan in his worn grey robe with his wooden staff, and Li Dehai outside. The red-gold furnace remains absent because it belongs only to the old flashback.",
        "shot": "Place the camera along the inner covered passage, side-on to the threshold. A clean band of eave shadow crosses the flagstones while daylight opens toward the exterior stairs. Ye Fan has just passed the sill and takes a restrained next step inside, shown at moderate distance in full body; his staff tip touches the shaded floor. Li Dehai stays in daylight beyond the gate, with a clear depth interval. Avoid a frontal portrait, centered symmetry, lifted-sole display, or any foreground limb enlargement.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v19_reverse_li_dehai_foreground": {
        "label": "李德海前景反向层级",
        "focus": "Show the same present-day decision from Li Dehai's exterior-side position: Li Dehai is a partial, grounded foreground witness, while Ye Fan is the full-body subject entering through the gate. Keep only the named figures, the gate, connected paving/stairs, and Ye Fan's wooden staff; no furnace or flashback imagery.",
        "shot": "Use a moderate 85mm-equivalent view from the exterior stair landing, with Li Dehai's shoulder and sleeve forming a narrow near-edge foreground anchor rather than covering the frame. Look through the gate toward Ye Fan at a believable middle distance as he crosses into the courtyard. Keep Ye Fan's complete body, both feet, robe silhouette, and staff-to-ground contact visible; preserve one clear threshold boundary and do not let the witness become a duplicate or oversized face.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v20_long_lens_side_compression": {
        "label": "远距离侧向压缩",
        "focus": "Present-day entry seen from far along the exterior stairs. Use the long view to make the gate, courtyard, and mountain architecture read as one believable scale system. Ye Fan's wooden staff is present and the flashback furnace is strictly absent.",
        "shot": "Use a distant 100mm-equivalent side view parallel to the gate facade, with no object close to the lens. The gate columns create a measured rhythm across the frame. Ye Fan crosses the opening in a clean side profile at medium-small full-body scale, with both feet readable and a natural walking phase; Li Dehai stands farther back on the exterior landing, separated by depth and architecture. Favor perspective compression and spatial clarity over dramatic foreshortening or a face close-up.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
    },
    "v21_compact_inner_courtyard": {
        "label": "短 prompt：内院斜向穿门",
        "focus": "Test a shorter instruction set. Keep only the present-day inventory, adult proportions, staff contact, continuous space, and a clear gate boundary; do not repeat decorative negatives.",
        "shot": "Use a moderate 65mm three-quarter view from the inner courtyard wall. Ye Fan crosses diagonally through the gate at medium distance, both feet and the staff contact visible. Li Dehai remains outside in a separated background zone.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v22_compact_architecture_establishing": {
        "label": "短 prompt：建筑建立镜头",
        "focus": "Test whether a compact prompt lets the image model establish architecture without overconstraining body motion. Let gate, paving, stairs, and mountain depth carry the scale evidence.",
        "shot": "Use a distant 70mm architectural establishing view from inside the courtyard, with the complete gate opening and connected paving visible. Ye Fan is a small but readable full-body figure entering; Li Dehai stands outside on the stairs.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v23_compact_door_leaf_frame": {
        "label": "短 prompt：门扇边缘框景",
        "focus": "Test one controlled foreground architecture element without changing the story inventory. The door leaf is a narrow frame edge, not a second scene or an occluding wall.",
        "shot": "Use a moderate 75mm side view from just outside the gate. A timber door leaf touches one frame edge; Ye Fan remains fully visible at middle distance crossing the sill with a grounded staff, while Li Dehai is separated beyond the opening.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v24_courtyard_pause_side_profile": {
        "label": "多场景：内院停步侧面全身",
        "focus": "Test a quieter beat after the crossing instead of another dramatic step. Present-day inventory is exact: Ye Fan, Li Dehai, the gate, connected paving, and one wooden staff; no furnace or flashback material.",
        "shot": "Use a distant 85mm side profile from inside the courtyard under the gate eave. Ye Fan has just completed the crossing and pauses with both feet planted on the inner flagstones, staff vertical and grounded. Leave generous architectural space around him; Li Dehai remains beyond the threshold on the exterior stairs.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v25_exterior_stair_wide_reverse": {
        "label": "多场景：外侧台阶宽幅反向建立",
        "focus": "Test an exterior-originating composition with the architecture carrying most of the frame. Keep the two adult figures small enough to prove building scale, while preserving the present-day wooden staff and excluding the flashback furnace.",
        "shot": "Use a wide 50mm view from the exterior mountain stair landing, looking toward the gate and inner courtyard. Ye Fan is a readable full-body figure just inside the gate, seen in rear three-quarter view moving inward; Li Dehai stands farther up the stairs as a smaller witness. Keep a broad continuous ground plane and no foreground limb near the lens.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v26_high_eave_diagonal_ground_plan": {
        "label": "多场景：门檐高位斜向地面关系",
        "focus": "Test a high architectural camera where people are scale markers rather than close portraits. Preserve believable adult proportions, the single staff, the gate boundary, and one continuous walkable surface; omit all flashback objects.",
        "shot": "Use a high oblique 60mm view from the inner gate eave, looking diagonally down across the courtyard and threshold. Ye Fan is a normal-sized full-body figure near the inner side with both feet visibly planted after entering; his staff touches the same paving plane. Li Dehai is smaller on the far exterior stair landing. Keep the body unwarped and avoid a top-down distortion.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v27_corridor_long_axis_silhouette": {
        "label": "多场景：廊道长轴剪影层次",
        "focus": "Test a long-axis covered-corridor composition with a restrained silhouette and strong depth intervals. The frame remains present-day only: Ye Fan, Li Dehai, gate architecture, connected flagstones, and the wooden staff.",
        "shot": "Use a moderate 70mm view looking along the covered inner corridor toward the open gate. Ye Fan stands in a three-quarter full-body silhouette at mid-distance just beyond the sill, turning slightly inward with the staff grounded. Li Dehai appears through the bright opening in the far exterior zone, clearly smaller and separated by the threshold.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v28_archway_empty_space_duo": {
        "label": "多场景：门洞留白双人关系",
        "focus": "Test a composition with substantial negative space and two separated figures rather than a foreground-dominant protagonist. Keep normal adult anatomy, readable feet and staff contact, one connected ground plane, and no furnace.",
        "shot": "Use a distant 65mm centered architectural frame from the courtyard. The gate arch and mountain view occupy most of the image; Ye Fan is placed to one side at medium-small full-body scale after entering, while Li Dehai is placed on the opposite exterior stair line. Do not enlarge either face, foot, or hand through proximity.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
    "v29_low_contrast_morning_threshold": {
        "label": "多场景：晨雾门槛低对比侧逆光",
        "focus": "Test a softer atmospheric lighting situation without changing geometry or story inventory. Maintain natural adult proportions and visible contact evidence even in haze; no furnace, magic, or extra figures.",
        "shot": "Use a moderate 75mm side-biased view from the inner courtyard at a neutral waist-to-chest camera height. Ye Fan is fully visible at middle distance with both feet on the courtyard floor, the staff planted beside him, and the gate sill behind. Li Dehai remains a smaller silhouette beyond the opening on the exterior stairs; keep the nearest limb at moderate scale.",
        "style_override": REFERENCE_STYLE_LANGUAGE,
        "prompt_profile": "compact",
    },
}


class VariantKeyVisionAuditNode(KeyVisionImageAuditNode):
    def __init__(
        self,
        *,
        workflow: Any,
        rubric_dir: Path | None = None,
        prompt_store: PromptStore | None = None,
        extra_dimension: bool = False,
    ) -> None:
        super().__init__(workflow=workflow)
        self._rubric_dir = rubric_dir
        self._extra_dimension = extra_dimension
        if prompt_store is not None:
            self.prompts = prompt_store

    def _rubric_bundle(self, state: ProjectState) -> ProductionRubricBundle:
        del state
        if self._rubric_dir is None:
            return super()._rubric_bundle(ProjectState.model_construct())
        bundle = load_production_rubric_bundle(self._rubric_dir, include_xuanhuan_style=False)
        allowed = set(KEY_VISION_AUDIT_DIMENSION_IDS)
        if self._extra_dimension:
            allowed.add("foreground_perspective")
        dimensions = [item for item in bundle.dimensions if item.id in allowed]
        return bundle.model_copy(update={"dimensions": dimensions})


class BlindKeyVisionAuditNode(VariantKeyVisionAuditNode):
    """Use the same model/rubric while withholding contracts that can bias scoring."""

    def _render_audit_request(self, state: ProjectState, item: StaticAssetGenerationItem) -> str:
        bundle = self._rubric_bundle(state)
        approval_threshold = self._approval_threshold(bundle)
        return self.prompts.render(
            "key_vision_image_audit",
            asset_name=item.name,
            expectation=self._asset_expectation(state, item),
            rubric=self._render_rubric(bundle),
            approval_threshold=f"{approval_threshold:g}",
        )

    @staticmethod
    def _render_rubric(bundle: ProductionRubricBundle) -> str:
        from autodrama.image_audit_rubrics import render_production_rubric

        return render_production_rubric(bundle)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def append_doc(text: str) -> None:
    EXPERIMENT_DOC.parent.mkdir(parents=True, exist_ok=True)
    with EXPERIMENT_DOC.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text.rstrip() + "\n\n")


def copy_grounded_rubric() -> tuple[Path, Path]:
    rubric_dir = LAB / "rubric_variants" / "grounded"
    if rubric_dir.exists():
        shutil.rmtree(rubric_dir)
    source = SRC / "autodrama" / "prompts" / "image_rubrics"
    shutil.copytree(source, rubric_dir)
    general_path = rubric_dir / "general_rubrics-v2.json"
    payload = read_json(general_path)
    dimensions = list(payload["dimensions"])
    if not any(item.get("id") == "foreground_perspective" for item in dimensions):
        dimensions.append(
            {
                "id": "foreground_perspective",
                "name": "前景透视与局部尺度稳定",
                "category": "contract",
                "weight": 6,
                "gate": True,
                "applies_to": ["all"],
                "audit_question": (
                    "前景人物、最近肢体、门槛和建筑是否处于同一套透视中？近端局部放大是否破坏人物整体比例、"
                    "建筑尺度或构图证据？若最近的脚/腿/脸相对同一人物其他部分出现明显异常放大，或长袍遮挡后"
                    "无法读出完整身体占位，应在本维度扣分；只检查可见的整体透视和构图，不检查脚部细节。"
                ),
            }
        )
    payload["dimensions"] = dimensions
    payload["revision"] = "2.5-experiment-grounded-foreground-perspective"
    write_json(general_path, payload)

    audit_prompt_dir = LAB / "prompt_variants" / "grounded"
    if audit_prompt_dir.exists():
        shutil.rmtree(audit_prompt_dir)
    (audit_prompt_dir / "key_vision_image_audit").mkdir(parents=True)
    original = (SRC / "autodrama" / "prompts" / "key_vision_image_audit" / "default.md").read_text(encoding="utf-8")
    extra = (
        "\n校准规则（本实验）：你只能根据图片中可见的二维证据评分，不能把镜头合同或当前提示词中的声明当作已经实现。"
        "先描述图片实际显示的尺度、占位、边界和遮挡，再给分；不得从提示词反推图片已经做到。若最近的脚、腿、脸或躯干"
        "相对同一人物其他部分明显异常放大，或近景局部大到改变人物整体解剖观感，`foreground_perspective` 必须不高于5分；"
        "若只是轻微透视夸张才可给6-8分。若长袍、道具或建筑遮挡使关键身体/边界关系无法确认，`evidence_survival` 不得给9-10分，"
        "且不能用 `applicable=false` 规避。即使合同声称使用了50mm、1:7.5或‘自然步态’，也必须按图片证据扣分。"
        "不要把‘电影感强’、‘构图完整’或提示词里的道具设定当作高分理由。\n"
    )
    (audit_prompt_dir / "key_vision_image_audit" / "default.md").write_text(
        original + extra,
        encoding="utf-8",
        newline="\n",
    )
    return rubric_dir, audit_prompt_dir


def copy_blind_prompt() -> Path:
    prompt_dir = LAB / "prompt_variants" / "blind"
    if prompt_dir.exists():
        shutil.rmtree(prompt_dir)
    target = prompt_dir / "key_vision_image_audit"
    target.mkdir(parents=True)
    (target / "default.md").write_text(
        """审查输入图片 1 是否可以作为生产链路中的构图与空间尺度主视觉锚点。

资产：{{asset_name}}
审计范围：{{expectation}}
通过线：{{approval_threshold}}

这是一次盲审。不要接收任何未提供的镜头合同、故事声明、提示词声明或模型参数；只根据你看到的图片像素和下面 rubric 评分。

{{rubric}}

盲审校准要求：
1. 逐维度输出一条 assessment，严格覆盖 rubric；无法从图片确认不等于适用性为 false，必须按可见证据保守评分。
2. 先写图片实际证据，再给 0–10 分。不要因为画面“电影感强”“主体突出”就给高分。
3. 如果最近的脚、腿、脸或躯干相对同一人物其他部分明显异常放大，或近景局部改变了人物整体比例，foreground_perspective 不得高于 5 分。
4. 如果长袍、道具或阴影使动作边界、身体占位或关键空间关系无法确认，evidence_survival 不得高于 5 分。
5. 如果画面出现一个没有可见持有/接触关系的突出道具，只能评价它是否造成构图或空间逻辑问题，不能把它自动当作剧情钩子。
6. score<10 时 defect 必须非空且具体；不要用“轻微瑕疵”替代可观察事实。只输出 schema JSON，不要解释文字。
""",
        encoding="utf-8",
        newline="\n",
    )
    return prompt_dir


def build_compiler_prompt(
    *,
    story_context: str,
    style: str,
    previous_feedback: str,
    variant: dict[str, str],
) -> str:
    if variant.get("prompt_profile") == "compact":
        return f"""Produce one concise production-ready image prompt for GPT-Image-2 from the supplied facts. Return exactly one JSON object with non-empty `shot_contract`, `scene_style_contract`, and `prompt` strings. Do not explain, use XML, mention roles, or output Markdown.

STORY:
{story_context}

MEDIUM:
{style}

PREVIOUS AUDIT FEEDBACK:
{previous_feedback}

BRANCH:
{variant['label']}
{variant['focus']}
{variant['shot']}

HARD FACTS:
- Present-day gate crossing only: Ye Fan enters the Tianjian Sect outer gate and Li Dehai remains outside behind him.
- Ye Fan wears a worn grey robe and carries one wooden walking staff; the flashback red-gold furnace is absent.
- Two named people only. Preserve normal adult proportions and one continuous walkable ground plane.

COMPOSITION:
- Use a moderate, believable perspective and a full-body view with both feet, the staff grip, and staff ground contact readable.
- Keep the gate boundary, character-to-building scale, depth order, and occlusion logic clear.
- Show a natural walking phase without prescribing exact heel angles, centimeter measurements, or a close foreground limb.

OUTPUT:
The final `prompt` must be 180–280 English words with these sections in order: [MEDIUM], [SCENE AND ACTION], [CAMERA AND SPATIAL RELATIONS], [LIGHT AND MATERIAL], [CLEANLINESS]. Use positive spatial instructions and only a short final exclusion sentence. Do not reintroduce the furnace, extra people, text, watermark, weapons, magic, or live-action photography."""
    return f"""Produce one production-ready image prompt for GPT-Image-2 from the supplied facts. Return exactly one JSON object with non-empty `shot_contract`, `scene_style_contract`, and `prompt` strings. Do not explain, use XML, mention roles, or output Markdown.

STORY FACTS:
{story_context}

GLOBAL MEDIUM:
{style}

PREVIOUS AUDIT FEEDBACK (repair only visible composition or spatial issues; do not invent story facts):
{previous_feedback}

EXPERIMENT BRANCH: {variant['label']}
{variant['focus']}
{variant['shot']}

NON-NEGOTIABLE FACT LEDGER:
- Present time: 80-year-old Ye Fan is entering the Tianjian Sect outer gate; Li Dehai remains behind him in the exterior zone.
- Present-day objects: Ye Fan's worn grey robe and wooden walking staff only.
- The red-gold furnace appears only in the eighty-years-ago flashback and must be absent from this present-day frame.
- No extra people, no flashback montage, no text, no watermark, no logo, no weapons, no magical effects.
- Preserve normal adult proportions, roughly seven to eight heads tall, natural torso, pelvis and limb lengths.

COMPOSITION PRIORITIES:
- Make one continuous ground plane and one readable gate boundary.
- Use a moderate perspective and a full-body shot with both feet visible at a comparable scale; never make the nearest foot, face, or torso disproportionately large.
- Keep the staff's hand grip and ground contact visible without hiding the legs.
- Make the image unmistakably a stylized Eastern xuanhuan 3D CG animation production frame, clearly computer-rendered and not live-action photography.

OUTPUT:
Return exactly one JSON object with non-empty strings `shot_contract`, `scene_style_contract`, and `prompt`.
The final `prompt` must be 220-360 English words, direct positive image instructions with these sections in order: [MEDIUM], [SCENE AND ACTION], [CAMERA AND BLOCKING], [LIGHT AND MATERIAL], [CLEANLINESS]. Keep only a short final exclusion sentence. Do not include reasoning, XML, Markdown fences, model parameters, or claims about invisible coordinates. The prompt must not reintroduce the flashback furnace into the present scene."""


def current_story_inputs(repo: ProjectRepository, state: ProjectState) -> tuple[str, str, str, str]:
    extract_path = PROJECT_DIR / "assets" / "json" / "scripts" / "novel_extract" / "episode_001.json"
    story_context = str(read_json(extract_path)["content"])
    script_type = str(state.metadata.get("script_type") or "").strip()
    if not script_type:
        raise ValueError("key vision experiment requires script_worldview_extract metadata")
    state.metadata["script_type"] = script_type
    style = str(state.metadata.get("visual_style_prompt") or "")
    director = DirectorService(PromptStore())
    feedback = director.key_vision_audit_feedback(state)
    image_canvas = "3840x2160"
    return story_context, style, feedback, image_canvas


def base_generation_item(repo: ProjectRepository, project_dir: Path) -> StaticAssetGenerationItem:
    output_path = repo.layout.node_output_path(project_dir, "key_vision_image_generation")
    payload = read_json(output_path)
    return StaticAssetGenerationItem.model_validate(payload["generated_assets"][0])


def score_summary(decision: KeyVisionAuditDecision) -> dict[str, Any]:
    return {
        "approved": decision.approved,
        "weighted_score": decision.weighted_score,
        "category_scores": decision.category_scores,
        "issues": decision.issues,
        "rationale": decision.rationale,
        "assessments": [row.model_dump(mode="json") for row in decision.assessments],
    }


def reflection(
    current: KeyVisionAuditDecision,
    grounded: KeyVisionAuditDecision | None,
    blind: KeyVisionAuditDecision | None = None,
) -> str:
    current_score = current.weighted_score
    grounded_score = grounded.weighted_score if grounded is not None else None
    blind_score = blind.weighted_score if blind is not None else None
    current_issues = ", ".join(current.issues) or "无"
    grounded_issues = ", ".join(grounded.issues) if grounded is not None else "未运行"
    blind_issues = ", ".join(blind.issues) if blind is not None else "未运行"
    if blind is not None and blind.approved is False:
        return (
            f"当前 rubric={current_score}，校准 rubric={grounded_score}，盲审 rubric={blind_score} 并拒绝；"
            f"盲审发现：{blind_issues}。这说明带合同上下文的 audit 分数偏乐观，漏检了图片可见的构图/透视问题。"
        )
    if grounded is not None and current.approved and not grounded.approved:
        return (
            f"当前生产 rubric 给出 {current_score} 分并通过，但校准 rubric 给出 {grounded_score} 分并拒绝；"
            f"这证明当前分数没有真实反映可见质量，且至少漏覆盖了：{grounded_issues}。"
        )
    if grounded is not None and grounded.weighted_score is not None and current_score is not None:
        return (
            f"当前 rubric={current_score}（issues: {current_issues}），校准 rubric={grounded_score}（issues: {grounded_issues}），"
            f"盲审={blind_score}（issues: {blind_issues}）。"
            "两者接近时，分数对该候选的区分力仍需结合图片目视复核；不能仅凭通过线认定图片可交付。"
        )
    return f"当前生产 rubric={current_score}，issues={current_issues}；尚未有第二套 rubric 对照。"


def is_transient_provider_failure(exc: BaseException) -> bool:
    parts: list[str] = []
    current: BaseException | None = exc
    while current is not None and len(parts) < 8:
        parts.append(type(current).__name__)
        parts.append(str(current))
        current = current.__cause__ or current.__context__
    text = " ".join(parts).lower()
    return any(
        marker in text
        for marker in (
            "connecterror",
            "timeoutexception",
            "timeout",
            "tls",
            "proxy",
            "temporary",
            "temporarily",
            "server error",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "http 408",
            "http 425",
            "http 429",
            "http 500",
            "http 502",
            "http 503",
            "http 504",
        )
    )


async def generate_json_with_experiment_retries(
    provider: Any,
    prompt: str,
    schema: type,
    *,
    temperature: float,
    refs: list[AssetRef] | None,
    metadata: dict[str, Any],
) -> Any:
    for attempt in range(1, EXPERIMENT_PROVIDER_ATTEMPTS + 1):
        try:
            return await provider.generate_json(
                prompt,
                schema,
                temperature=temperature,
                refs=refs,
                metadata=metadata,
            )
        except (ProviderBadResponseError, OSError) as exc:
            if attempt >= EXPERIMENT_PROVIDER_ATTEMPTS or not is_transient_provider_failure(exc):
                raise
            delay = min(30.0, 3.0 * (2 ** (attempt - 1)))
            print(
                f"[experiment] transient text failure attempt={attempt}/{EXPERIMENT_PROVIDER_ATTEMPTS}; "
                f"retrying in {delay:.1f}s: {type(exc).__name__}",
                flush=True,
            )
            await asyncio.sleep(delay)


async def generate_image_with_experiment_retries(
    provider: Any,
    prompt: str,
    *,
    refs: list[AssetRef] | None,
    size: str,
    metadata: dict[str, Any],
) -> Any:
    for attempt in range(1, EXPERIMENT_IMAGE_ATTEMPTS + 1):
        try:
            return await provider.generate_image(
                prompt,
                refs=refs,
                size=size,
                metadata=metadata,
            )
        except (ProviderBadResponseError, OSError) as exc:
            if attempt >= EXPERIMENT_IMAGE_ATTEMPTS or not is_transient_provider_failure(exc):
                raise
            delay = min(60.0, 5.0 * (2 ** (attempt - 1)))
            print(
                f"[experiment] transient image failure attempt={attempt}/{EXPERIMENT_IMAGE_ATTEMPTS}; "
                f"retrying in {delay:.1f}s: {type(exc).__name__}",
                flush=True,
            )
            await asyncio.sleep(delay)


async def audit_image(
    *,
    audit_node: VariantKeyVisionAuditNode,
    audit_provider: Any,
    state: ProjectState,
    item: StaticAssetGenerationItem,
    image_path: Path,
    image_url: str | None,
    lab_dir: Path,
    label: str,
) -> KeyVisionAuditDecision:
    request = audit_node._render_audit_request(state, item)
    ref = AssetRef(id=item.asset_id, type="image", path=str(image_path), url=image_url)
    metadata = {
        "node_name": audit_node.name,
        "project_id": state.project_id,
        "asset_id": item.asset_id,
        "prompt_asset_type": "key_vision_image_audit",
        "prompt_asset_name": label,
        "reasoning_effort": "high",
        "max_output_tokens": 16384,
    }
    normalized: KeyVisionAuditDecision | None = None
    validation_feedback = ""
    for validation_attempt in range(1, 4):
        decision = await generate_json_with_experiment_retries(
            audit_provider,
            request + validation_feedback,
            audit_node._decision_model(),
            temperature=0.1,
            refs=[ref],
            metadata=metadata,
        )
        if not isinstance(decision, KeyVisionAuditDecision):
            raise TypeError("audit provider returned an unexpected decision type")
        state.budget.used_text_calls += 1
        try:
            normalized = audit_node._normalize_decision(decision, state=state, item=item)
            break
        except (TypeError, ValueError) as exc:
            if validation_attempt >= 3:
                raise
            validation_feedback = (
                "\n\n上一份 JSON 未通过生产节点的 rubric 覆盖校验，不能省略任何维度。"
                f"校验错误：{exc}。请重新输出完整 JSON，必须逐一包含 rubric 中的每个 dimension_id，"
                "不适用维度也必须保留 assessment，并使用 applicable=false、score=null、defect=\"\"。"
            )
    if normalized is None:
        raise AssertionError("audit normalization produced no decision")
    write_json(lab_dir / "audits" / f"{label}.json", score_summary(normalized))
    return normalized


async def compile_prompt(
    *,
    provider: Any,
    state: ProjectState,
    story_context: str,
    style: str,
    feedback: str,
    variant_id: str,
) -> KeyVisionPromptOutput:
    variant = VARIANTS[variant_id]
    if variant.get("production_template") == "true":
        prompt_store = PromptStore()
        request = prompt_store.render(
            "key_vision_prompt",
            script_type=DirectorService.key_vision_script_type(state),
            global_visual_style=style,
            director_brief=DirectorService.key_vision_director_brief(state),
            render_contract=DirectorService.key_vision_render_contract(state, "3840x2160"),
            continuity_contract=DirectorService.key_vision_continuity_contract(state),
            audit_feedback=feedback,
        )
    else:
        request = build_compiler_prompt(
            story_context=story_context,
            style=str(variant.get("style_override") or style),
            previous_feedback=feedback,
            variant=variant,
        )
    output = await generate_json_with_experiment_retries(
        provider,
        request,
        KeyVisionPromptOutput,
        temperature=0.45,
        refs=None,
        metadata={
            "node_name": "key_vision_prompt",
            "project_id": state.project_id,
            "prompt_asset_type": "key_vision_prompt_experiment",
            "prompt_asset_name": variant_id,
        },
    )
    if not isinstance(output, KeyVisionPromptOutput):
        raise TypeError("prompt compiler returned an unexpected output type")
    for field in ("shot_contract", "scene_style_contract", "prompt"):
        if not str(getattr(output, field) or "").strip():
            raise ValueError(f"compiled prompt has empty {field}: {variant_id}")
    return output


async def generate_image(
    *,
    provider: Any,
    media_store: MediaStore,
    prompt: str,
    candidate_id: str,
    refs: list[AssetRef] | None = None,
    node_name: str = "key_vision_image_generation",
) -> tuple[Path, str | None, dict[str, Any]]:
    result = await generate_image_with_experiment_retries(
        provider,
        prompt,
        refs=refs,
        size="3840x2160",
        metadata={
            "node_name": node_name,
            "project_id": "saodi_0803",
            "asset_id": candidate_id,
            "asset_type": "key_vision",
            "prompt_asset_type": "key_vision_image_generation_experiment",
            "prompt_asset_name": candidate_id,
            "size": "3840x2160",
            "quality": "high",
        },
    )
    output_path = LAB / "images" / f"{candidate_id}.png"
    await media_store.write_first_generated_image(LAB, output_path, result)
    metadata = {
        "provider": result.provider,
        "model": result.model,
        "image_urls": result.image_urls,
        "request_id": result.request_id,
        "usage": result.usage,
        "raw_response": result.raw_response,
    }
    write_json(LAB / "generation" / f"{candidate_id}.json", metadata)
    return output_path, result.image_urls[0] if result.image_urls else None, metadata


async def run_candidate(
    *,
    args: argparse.Namespace,
    state: ProjectState,
    base_item: StaticAssetGenerationItem,
    prompt_provider: Any,
    image_provider: Any,
    audit_provider: Any,
    media_store: MediaStore,
    current_audit_node: VariantKeyVisionAuditNode,
    grounded_audit_node: VariantKeyVisionAuditNode,
    blind_audit_node: BlindKeyVisionAuditNode | None,
    story_context: str,
    style: str,
    feedback: str,
    variant_id: str,
    repeat: int,
    image_budget: dict[str, int],
    budget_lock: asyncio.Lock,
) -> dict[str, Any]:
    candidate_id = f"{variant_id}--r{repeat:02d}"
    cached_path = LAB / "candidates" / f"{candidate_id}.json"
    if args.resume and cached_path.is_file():
        return read_json(cached_path)
    if variant_id == "baseline_existing":
        prompt_output = KeyVisionPromptOutput.model_validate(state.metadata["key_vision_prompt"])
        image_path = PROJECT_DIR / str(base_item.asset_path)
        image_url = base_item.asset_url
        generation_metadata: dict[str, Any] = {"existing": True}
    else:
        prompt_output = await compile_prompt(
            provider=prompt_provider,
            state=state,
            story_context=story_context,
            style=style,
            feedback=feedback,
            variant_id=variant_id,
        )
        async with budget_lock:
            if image_budget["used"] >= MAX_IMAGE_BUDGET:
                raise RuntimeError(f"image experiment budget exhausted at {MAX_IMAGE_BUDGET}")
            image_budget["used"] += 1
        image_path, image_url, generation_metadata = await generate_image(
            provider=image_provider,
            media_store=media_store,
            prompt=prompt_output.prompt,
            candidate_id=candidate_id,
            refs=(
                [
                    AssetRef(
                        id="user_style_reference",
                        type="image",
                        path=str(REFERENCE_STYLE_IMAGE),
                        metadata={"reference_role": "style_and_composition_only"},
                    )
                ]
                if VARIANTS[variant_id].get("use_style_reference") == "true" and REFERENCE_STYLE_IMAGE.is_file()
                else None
            ),
        )

    state_variant = state.model_copy(deep=True)
    state_variant.metadata["key_vision_prompt"] = prompt_output.model_dump(mode="json")
    item = base_item.model_copy(
        update={
            "asset_id": candidate_id,
            "prompt": prompt_output.prompt,
            "asset_path": str(image_path),
            "asset_url": image_url,
        }
    )
    current = await audit_image(
        audit_node=current_audit_node,
        audit_provider=audit_provider,
        state=state_variant.model_copy(deep=True),
        item=item,
        image_path=image_path,
        image_url=image_url,
        lab_dir=LAB,
        label=f"{candidate_id}--current",
    )
    grounded = await audit_image(
        audit_node=grounded_audit_node,
        audit_provider=audit_provider,
        state=state_variant.model_copy(deep=True),
        item=item,
        image_path=image_path,
        image_url=image_url,
        lab_dir=LAB,
        label=f"{candidate_id}--grounded",
    )
    blind = None
    if blind_audit_node is not None:
        blind = await audit_image(
            audit_node=blind_audit_node,
            audit_provider=audit_provider,
            state=state_variant.model_copy(deep=True),
            item=item,
            image_path=image_path,
            image_url=image_url,
            lab_dir=LAB,
            label=f"{candidate_id}--blind",
        )
    result = {
        "candidate_id": candidate_id,
        "variant_id": variant_id,
        "repeat": repeat,
        "image_path": str(image_path),
        "image_url": image_url,
        "prompt": prompt_output.model_dump(mode="json"),
        "generation": generation_metadata,
        "current_audit": score_summary(current),
        "grounded_audit": score_summary(grounded),
        "blind_audit": score_summary(blind) if blind is not None else None,
        "reflection": reflection(current, grounded, blind),
    }
    write_json(LAB / "candidates" / f"{candidate_id}.json", result)
    append_doc(
        f"""## 候选 {candidate_id}

- 分支：{VARIANTS.get(variant_id, {}).get('label', variant_id)}
- 图片：`{image_path}`
- 当前生产 rubric：`{current.weighted_score}` 分，approved=`{current.approved}`
- 校准 rubric：`{grounded.weighted_score}` 分，approved=`{grounded.approved}`
- 盲审 rubric：`{blind.weighted_score if blind is not None else '未运行'}` 分，approved=`{blind.approved if blind is not None else '未运行'}`
- 当前 rubric issues：{'; '.join(current.issues) or '无'}
- 校准 rubric issues：{'; '.join(grounded.issues) or '无'}
- 盲审 rubric issues：{'; '.join(blind.issues) if blind is not None else '未运行'}
- 反思：{result['reflection']}

最终生图 prompt：

```text
{prompt_output.prompt}
```
"""
    )
    print(
        f"[experiment] {candidate_id} current={current.weighted_score}/{current.approved} "
        f"grounded={grounded.weighted_score}/{grounded.approved} "
        f"blind={blind.weighted_score if blind is not None else '-'} "
        f"images={image_budget['used']}/{MAX_IMAGE_BUDGET}",
        flush=True,
    )
    return result


async def run_edit(
    *,
    source: dict[str, Any],
    image_provider: Any,
    audit_provider: Any,
    media_store: MediaStore,
    state: ProjectState,
    base_item: StaticAssetGenerationItem,
    current_audit_node: VariantKeyVisionAuditNode,
    grounded_audit_node: VariantKeyVisionAuditNode,
    blind_audit_node: BlindKeyVisionAuditNode | None,
    image_budget: dict[str, int],
    budget_lock: asyncio.Lock,
) -> dict[str, Any]:
    source_id = str(source["candidate_id"])
    edit_id = f"{source_id}--edit"
    async with budget_lock:
        if image_budget["used"] >= MAX_IMAGE_BUDGET:
            raise RuntimeError(f"image experiment budget exhausted at {MAX_IMAGE_BUDGET}")
        image_budget["used"] += 1
    edit_prompt = (
        "Edit the provided reference image, preserving the same characters, gate, architecture, lighting, "
        "color palette, image dimensions, camera distance, and overall composition. Make only these structural "
        "corrections: this is the present-day scene, so remove any red-gold furnace or other flashback-only "
        "object; restore Ye Fan's worn wooden walking staff with one believable hand grip and a clear ground "
        "contact; keep his normal adult seven-to-eight-head body proportions; reduce any extreme near-foot or "
        "near-limb enlargement so both feet and the full body share one moderate perspective; keep both legs "
        "readable and connected to the pelvis under the robe. Do not add people, text, logos, weapons, magic, "
        "new scenery, or photorealistic/live-action styling. This is a stylized Eastern xuanhuan 3D CG animation "
        "production frame."
    )
    source_path = Path(source["image_path"])
    source_url = source.get("image_url")
    refs = [AssetRef(id=source_id, type="image", path=str(source_path), url=source_url)]
    edited_path, edited_url, generation = await generate_image(
        provider=image_provider,
        media_store=media_store,
        prompt=edit_prompt,
        candidate_id=edit_id,
        refs=refs,
        node_name="key_vision_edit",
    )
    prompt_output = KeyVisionPromptOutput.model_validate(source["prompt"])
    state_variant = state.model_copy(deep=True)
    state_variant.metadata["key_vision_prompt"] = prompt_output.model_dump(mode="json")
    item = base_item.model_copy(
        update={
            "asset_id": edit_id,
            "prompt": edit_prompt,
            "asset_path": str(edited_path),
            "asset_url": edited_url,
        }
    )
    current = await audit_image(
        audit_node=current_audit_node,
        audit_provider=audit_provider,
        state=state_variant.model_copy(deep=True),
        item=item,
        image_path=edited_path,
        image_url=edited_url,
        lab_dir=LAB,
        label=f"{edit_id}--current",
    )
    grounded = await audit_image(
        audit_node=grounded_audit_node,
        audit_provider=audit_provider,
        state=state_variant.model_copy(deep=True),
        item=item,
        image_path=edited_path,
        image_url=edited_url,
        lab_dir=LAB,
        label=f"{edit_id}--grounded",
    )
    blind = None
    if blind_audit_node is not None:
        blind = await audit_image(
            audit_node=blind_audit_node,
            audit_provider=audit_provider,
            state=state_variant.model_copy(deep=True),
            item=item,
            image_path=edited_path,
            image_url=edited_url,
            lab_dir=LAB,
            label=f"{edit_id}--blind",
        )
    result = {
        "candidate_id": edit_id,
        "source_candidate_id": source_id,
        "image_path": str(edited_path),
        "image_url": edited_url,
        "edit_prompt": edit_prompt,
        "generation": generation,
        "current_audit": score_summary(current),
        "grounded_audit": score_summary(grounded),
        "blind_audit": score_summary(blind) if blind is not None else None,
        "reflection": reflection(current, grounded, blind),
    }
    write_json(LAB / "edits" / f"{edit_id}.json", result)
    append_doc(
        f"""## 编辑候选 {edit_id}

- 原图：`{source_path}`
- 编辑图：`{edited_path}`
- 当前生产 rubric：`{current.weighted_score}` 分，approved=`{current.approved}`
- 校准 rubric：`{grounded.weighted_score}` 分，approved=`{grounded.approved}`
- 盲审 rubric：`{blind.weighted_score if blind is not None else '未运行'}` 分，approved=`{blind.approved if blind is not None else '未运行'}`
- 反思：{result['reflection']}

编辑 prompt：

```text
{edit_prompt}
```
"""
    )
    print(
        f"[experiment] {edit_id} current={current.weighted_score}/{current.approved} "
        f"grounded={grounded.weighted_score}/{grounded.approved} "
        f"blind={blind.weighted_score if blind is not None else '-'} "
        f"images={image_budget['used']}/{MAX_IMAGE_BUDGET}",
        flush=True,
    )
    return result


async def main_async(args: argparse.Namespace) -> int:
    if args.max_images <= 0 or args.max_images > MAX_IMAGE_BUDGET:
        raise ValueError(f"--max-images must be between 1 and {MAX_IMAGE_BUDGET}")
    settings = __import__("autodrama.config", fromlist=["load_settings"]).load_settings(str(ROOT / "saodi.yaml"))
    repo = ProjectRepository(settings)
    state = repo.load_state(PROJECT_DIR)
    base_item = base_generation_item(repo, PROJECT_DIR)
    router = ProviderRouter(settings)
    router.set_prompt_audit_project_dir(LAB)
    workflow = PregenWorkflow(repo=repo, router=router)
    current_node = VariantKeyVisionAuditNode(workflow=workflow)
    grounded_rubric_dir, grounded_prompt_dir = copy_grounded_rubric()
    grounded_node = VariantKeyVisionAuditNode(
        workflow=workflow,
        rubric_dir=grounded_rubric_dir,
        prompt_store=PromptStore(grounded_prompt_dir),
        extra_dimension=True,
    )
    blind_node: BlindKeyVisionAuditNode | None = None
    if not args.no_blind_audit:
        blind_node = BlindKeyVisionAuditNode(
            workflow=workflow,
            rubric_dir=grounded_rubric_dir,
            prompt_store=PromptStore(copy_blind_prompt()),
            extra_dimension=True,
        )
    prompt_provider = router.text("director", node_name="key_vision_prompt")
    image_provider = router.image("key_vision", node_name="key_vision_image_generation")
    audit_provider = router.text("key_vision", node_name="key_vision_image_audit")
    media_store = MediaStore(repo.layout, timeout_seconds=settings.runtime.request_timeout_seconds)
    story_context, style, feedback, _image_canvas = current_story_inputs(repo, state)
    LAB.mkdir(parents=True, exist_ok=True)
    write_json(
        LAB / "manifest.json",
        {
            "project_dir": str(PROJECT_DIR),
            "source_prompt": str(PROJECT_DIR / "logs" / "prompts" / "key_vision_image_generation" / "key_vision_original.prompt.txt"),
            "source_compiler_prompt": str(PROJECT_DIR / "logs" / "prompts" / "key_vision_prompt" / "key_vision_prompt.prompt.txt"),
            "max_image_budget": MAX_IMAGE_BUDGET,
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "rubric_variant": str(grounded_rubric_dir),
        },
    )
    if not EXPERIMENT_DOC.exists():
        append_doc(
            f"""# key vision prompt / image edit 灰测记录

实验日期：{datetime.now().isoformat(timespec='seconds')}

真实项目：`{PROJECT_DIR}`

图片预算：最多 **{MAX_IMAGE_BUDGET} 张**（脚本有硬上限；当前 run 可通过 `--max-images` 使用其中一部分）。

## 实验问题

1. 当前两层 prompt 是否过长、约束是否互相干扰，导致 GPT-Image-2 生成异常透视、下肢和媒介风格偏差？
2. 当前 `key_vision_image_audit` 的同模型同 rubric 分数，是否真实反映可见图片质量？
3. 失败图使用 reference image edit 修复，是否优于重新 text-to-image？

## 已知基线观察

当前图的关键异常不是典型“大头娃娃”，而是近景局部透视放大、跨门槛下肢拓扑难读、以及现实场景错误出现闪回赤金小炉。当前最终生图 prompt 明确写入了“furnace suspended from Ye Fan's right waist sash”，但真实故事只在八十年前闪回中把小炉交给柳菡烟；同时现实场景的木杖没有被最终 prompt 作为硬库存保留。

每个候选都运行两次图片审计：

- `current`：生产中的 `key_vision_image_audit` 模型、当前 key-vision rubric。
- `grounded`：同一模型，增加“只根据可见二维证据评分”的校准指令，并增加一个只检查整体近景透视/局部尺度的 `foreground_perspective` 实验维度。该变体用于发现漏检，不自动替代生产 rubric。
"""
        )
    selected_variants = [item.strip() for item in args.variants.split(",") if item.strip()]
    if "baseline_existing" not in selected_variants:
        selected_variants.insert(0, "baseline_existing")
    unknown = [item for item in selected_variants if item != "baseline_existing" and item not in VARIANTS]
    if unknown:
        raise ValueError(f"unknown variants: {', '.join(unknown)}")
    image_budget = {"used": 0}
    budget_lock = asyncio.Lock()
    results: list[dict[str, Any]] = []
    for variant_id in selected_variants:
        repeats = 1 if variant_id == "baseline_existing" else args.repeats
        for repeat in range(1, repeats + 1):
            if variant_id != "baseline_existing" and image_budget["used"] >= args.max_images:
                break
            results.append(
                await run_candidate(
                    args=args,
                    state=state,
                    base_item=base_item,
                    prompt_provider=prompt_provider,
                    image_provider=image_provider,
                    audit_provider=audit_provider,
                    media_store=media_store,
                    current_audit_node=current_node,
                    grounded_audit_node=grounded_node,
                    blind_audit_node=blind_node,
                    story_context=story_context,
                    style=style,
                    feedback=feedback,
                    variant_id=variant_id,
                    repeat=repeat,
                    image_budget=image_budget,
                    budget_lock=budget_lock,
                )
            )
    if args.edit_top > 0:
        editable = [item for item in results if item["variant_id"] != "baseline_existing"]
        editable.sort(key=lambda item: (bool(item["grounded_audit"]["approved"]), item["grounded_audit"]["weighted_score"] or 0))
        for source in editable[: args.edit_top]:
            if image_budget["used"] >= args.max_images:
                break
            await run_edit(
                source=source,
                image_provider=image_provider,
                audit_provider=audit_provider,
                media_store=media_store,
                state=state,
                base_item=base_item,
                current_audit_node=current_node,
                grounded_audit_node=grounded_node,
                blind_audit_node=blind_node,
                image_budget=image_budget,
                budget_lock=budget_lock,
            )
    manifest = read_json(LAB / "manifest.json")
    manifest.update(
        {
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "images_used": image_budget["used"],
            "candidate_count": len(results),
            "variants": selected_variants,
            "repeats": args.repeats,
            "edit_top": args.edit_top,
        }
    )
    write_json(LAB / "manifest.json", manifest)
    append_doc(
        f"""## 本轮汇总

- 候选数：{len(results)}
- image generation/edit 调用数：{image_budget['used']} / {args.max_images}（总硬上限 {MAX_IMAGE_BUDGET}）
- 详细 JSON：`{LAB}`
- 说明：当前 production rubric 与 grounded rubric 的差异，以及每张图的分数/漏检反思，均保存在候选 JSON 和上面的逐候选记录中。
"""
    )
    print(json.dumps({"lab": str(LAB), "experiment_doc": str(EXPERIMENT_DOC), "images_used": image_budget["used"], "candidates": len(results)}, ensure_ascii=False, indent=2))
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a real-project key-vision prompt/audit/edit experiment.")
    parser.add_argument(
        "--variants",
        default=",".join(["baseline_existing", *VARIANTS.keys()]),
        help="Comma-separated variant ids.",
    )
    parser.add_argument("--repeats", type=int, default=2, help="Generated images per non-baseline variant.")
    parser.add_argument("--edit-top", type=int, default=4, help="Edit this many lowest grounded-score candidates.")
    parser.add_argument("--max-images", type=int, default=200, help="Per-run image budget, never above 200.")
    parser.add_argument(
        "--no-blind-audit",
        action="store_true",
        help="Skip the same-model blind audit used only for calibration.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Reuse completed candidate JSON files instead of regenerating them.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async(parse_args())))
