"""Post-generation validation for Script objects."""

from autodrama.models.script import Script


def validate_script_structure(script: Script) -> None:
    """Validate that a Script has basic structural integrity.

    Raises:
        ValueError: If the script is structurally invalid.
    """
    if not script.title.strip():
        raise ValueError("Script must have a non-empty title")

    if not script.scenes:
        raise ValueError("Script must contain at least one scene")

    character_names = {c.name for c in script.characters}

    for i, scene in enumerate(script.scenes, 1):
        if scene.scene_number != i:
            raise ValueError(
                f"Scene {i} has scene_number={scene.scene_number}; expected sequential numbering"
            )

        if not scene.description.strip() and not scene.dialogue:
            raise ValueError(f"Scene {i} has neither description nor dialogue")

        for line in scene.dialogue:
            if line.character not in character_names:
                raise ValueError(
                    f"Dialogue references unknown character '{line.character}' "
                    f"in scene {i}"
                )
            if not line.text.strip():
                raise ValueError(f"Empty dialogue line for '{line.character}' in scene {i}")
