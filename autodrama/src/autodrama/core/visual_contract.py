from __future__ import annotations

from autodrama.core.schemas import RoleAppearance, ShotEntityState


def identity_brief(appearance: RoleAppearance) -> str:
    if appearance.asset_role == "base" and appearance.reference_asset_name:
        raise ValueError(f"base appearance {appearance.id} must not reference another appearance")
    if appearance.asset_role == "variant":
        if not appearance.reference_asset_name:
            raise ValueError(f"variant appearance {appearance.id} requires reference_asset_name")
        if not (appearance.time_period or appearance.valid_from_event or appearance.valid_to_event):
            raise ValueError(
                f"variant appearance {appearance.id} requires a time_period or an explicit event validity range"
            )
    clean_values = normalize_identity_values([*appearance.identity_invariants, *appearance.wardrobe])
    if not clean_values:
        raise ValueError(
            f"appearance {appearance.id} has no structured identity_invariants or wardrobe; "
            "rerun role_extract and roleboard_prompt"
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
