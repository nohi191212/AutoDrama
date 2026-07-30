from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from autodrama.core.schemas import RoleAppearance, SemanticProvenance, ShotEntityState, VisualStyleSpec


def build_visual_style_spec(
    value: VisualStyleSpec | Mapping[str, Any],
    *,
    source: str | None = None,
) -> VisualStyleSpec:
    """Validate an explicit visual style contract without interpreting prose."""
    if isinstance(value, VisualStyleSpec):
        spec = value
    elif isinstance(value, Mapping):
        spec = VisualStyleSpec.model_validate(dict(value))
    else:
        raise TypeError(
            "visual style must be a VisualStyleSpec or mapping; "
            "migrate legacy visual_style_prompt before running the workflow"
        )
    if source is not None and source != spec.source:
        spec = spec.model_copy(update={"source": source})
        spec = VisualStyleSpec.model_validate(spec.model_dump(mode="json"))
    return spec


def apply_visual_style_patch(
    base: VisualStyleSpec,
    patch: Mapping[str, Any],
) -> VisualStyleSpec:
    """Apply an explicit field-level user override without interpreting any values."""
    allowed_fields = {
        "medium",
        "render_engine_language",
        "materials",
        "palette",
        "lighting",
        "camera",
        "negative_constraints",
    }
    unknown_fields = set(patch).difference(allowed_fields)
    if unknown_fields:
        raise ValueError(
            "unsupported visual style patch fields: " + ", ".join(sorted(unknown_fields))
        )
    payload = base.model_dump(mode="json", exclude={"version"})
    payload.update(dict(patch))
    payload["source"] = "user_override"
    return VisualStyleSpec.model_validate(payload)


def visual_style_conflicts(left: VisualStyleSpec, right: VisualStyleSpec) -> list[str]:
    """Return differing structured fields; prose is compared only as its own field."""
    comparable_fields = (
        "medium",
        "render_engine_language",
        "materials",
        "palette",
        "lighting",
        "camera",
        "negative_constraints",
    )
    return [
        field_name
        for field_name in comparable_fields
        if getattr(left, field_name) != getattr(right, field_name)
    ]


def render_visual_style_brief(spec: VisualStyleSpec) -> str:
    parts = [f"视觉媒介：{spec.medium}。"]
    parts.extend(spec.render_engine_language)
    for label, values in (
        ("材质", spec.materials),
        ("色板", spec.palette),
        ("灯光", spec.lighting),
        ("镜头", spec.camera),
    ):
        if values:
            parts.append(f"{label}：" + "、".join(values) + "。")
    if spec.negative_constraints:
        parts.append("不得出现：" + "、".join(spec.negative_constraints) + "。")
    return "\n".join(dict.fromkeys(part.strip() for part in parts if part.strip()))


def identity_brief(appearance: RoleAppearance) -> str:
    if appearance.asset_role == "base" and appearance.reference_asset_name:
        raise ValueError(f"base appearance {appearance.id} must not reference another appearance")
    if appearance.asset_role == "variant":
        if not appearance.reference_asset_name:
            raise ValueError(f"variant appearance {appearance.id} requires reference_asset_name")
        if not (appearance.valid_from_event or appearance.valid_to_event):
            raise ValueError(
                f"variant appearance {appearance.id} requires an explicit event validity range"
            )
    clean_values = normalize_identity_values([*appearance.identity_invariants, *appearance.wardrobe])
    if not clean_values:
        raise ValueError(
            f"appearance {appearance.id} has no structured identity_invariants or wardrobe; "
            "rerun role_extract and roleboard_prompt, or run the visual contract migration"
        )
    return "；".join(dict.fromkeys(clean_values))


def normalize_identity_values(values: list[object]) -> list[str]:
    """Normalize whitespace and exact duplicates only; never classify content."""
    clean: list[str] = []
    for value in values:
        text = " ".join(str(value or "").split()).strip()
        if text:
            clean.append(text)
    return list(dict.fromkeys(clean))


def render_character_visual_context(
    appearance: RoleAppearance,
    shot_state: ShotEntityState | None,
) -> dict[str, str]:
    """Render stable identity and temporary shot state into separate prompt sections."""
    stable_identity = identity_brief(appearance)
    if shot_state is None:
        return {"stable_identity": stable_identity, "current_shot_state": ""}
    if shot_state.entity_id != appearance.role_id:
        raise ValueError(
            f"shot entity {shot_state.entity_id} does not match appearance role {appearance.role_id}"
        )
    if shot_state.appearance_id and shot_state.appearance_id != appearance.id:
        raise ValueError(
            f"shot appearance {shot_state.appearance_id} does not match selected appearance {appearance.id}"
        )
    state_parts = normalize_identity_values(
        [
            shot_state.pose,
            shot_state.emotion,
            shot_state.injury,
            *shot_state.held_props,
            shot_state.energy_state,
        ]
    )
    return {
        "stable_identity": stable_identity,
        "current_shot_state": "；".join(state_parts),
    }


def migrate_legacy_visual_style(
    style_prompt: str,
    *,
    medium: str,
    materials: list[str] | None = None,
    palette: list[str] | None = None,
    lighting: list[str] | None = None,
    camera: list[str] | None = None,
    negative_constraints: list[str] | None = None,
) -> VisualStyleSpec:
    """Create v2 only from explicitly supplied migration fields; prose is not parsed."""
    legacy_text = " ".join(str(style_prompt or "").split()).strip()
    return VisualStyleSpec(
        medium=medium,
        render_engine_language=[legacy_text] if legacy_text else [],
        materials=materials or [],
        palette=palette or [],
        lighting=lighting or [],
        camera=camera or [],
        negative_constraints=negative_constraints or [],
        source="migration",
    )


def migrate_legacy_role_appearance(
    appearance: RoleAppearance,
    *,
    identity_invariants: list[str],
    wardrobe: list[str] | None = None,
    evidence: list[str] | None = None,
    confidence: float | None = None,
    warnings: list[str] | None = None,
) -> RoleAppearance:
    """Apply reviewed migration fields while preserving the original prose for audit."""
    if appearance.asset_role == "variant":
        if not appearance.reference_asset_name:
            raise ValueError(f"variant appearance {appearance.id} requires reference_asset_name")
        if not (appearance.valid_from_event or appearance.valid_to_event):
            raise ValueError(
                f"variant appearance {appearance.id} migration requires an explicit event validity range"
            )
    stable_identity = normalize_identity_values(identity_invariants)
    if not stable_identity:
        raise ValueError(f"appearance {appearance.id} migration requires explicit identity_invariants")
    return appearance.model_copy(
        update={
            "schema_version": 2,
            "identity_invariants": stable_identity,
            "wardrobe": normalize_identity_values(wardrobe or []),
            "legacy_source": appearance.legacy_source or appearance.desc or appearance.visual_features,
            "provenance": SemanticProvenance(
                source="migration",
                evidence=normalize_identity_values(evidence or []),
                confidence=confidence,
            ),
            "migration_warnings": normalize_identity_values(warnings or []),
        }
    )


def assert_duration_gate(total: float, target: float, *, tolerance: float = 0.05) -> None:
    low = target * (1 - tolerance)
    high = target * (1 + tolerance)
    if not low <= total <= high:
        raise ValueError(
            f"shot duration gate rejected total={total:.3f}s; expected {low:.3f}-{high:.3f}s for target={target:.3f}s"
        )


def normalize_shot_durations(durations: list[int], target: int, *, minimum: int = 3, maximum: int = 15) -> list[int]:
    if not durations:
        raise ValueError("cannot allocate duration to an empty shot plan")
    if not len(durations) * minimum <= target <= len(durations) * maximum:
        raise ValueError(
            f"shot count {len(durations)} cannot satisfy target {target}s within {minimum}-{maximum}s per shot"
        )
    weights = [max(minimum, min(maximum, int(value))) for value in durations]
    scaled = [target * value / sum(weights) for value in weights]
    result = [max(minimum, min(maximum, int(value))) for value in scaled]
    remainder = target - sum(result)
    order = sorted(range(len(result)), key=lambda i: scaled[i] - int(scaled[i]), reverse=remainder > 0)
    while remainder:
        changed = False
        for index in order:
            if remainder > 0 and result[index] < maximum:
                result[index] += 1
                remainder -= 1
                changed = True
            elif remainder < 0 and result[index] > minimum:
                result[index] -= 1
                remainder += 1
                changed = True
            if remainder == 0:
                break
        if not changed:
            raise ValueError("duration allocation became infeasible")
    assert_duration_gate(float(sum(result)), float(target))
    return result
