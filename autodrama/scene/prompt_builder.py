"""Build polished image-generation prompts from shot compositions."""

from autodrama.models.scene_plan import ShotComposition


def build_image_prompt(
    *,
    scene_description: str,
    shot: ShotComposition,
    style: str = "realistic",
) -> str:
    """Assemble an English image-generation prompt.

    Args:
        scene_description: The scene's textual description.
        shot: Shot composition details.
        style: Overall visual style.

    Returns:
        A prompt string suitable for DALL-E, Stable Diffusion, etc.
    """
    parts: list[str] = []

    # Subject
    if shot.subject_focus:
        parts.append(shot.subject_focus)

    # Background context
    if shot.background_description:
        parts.append(f"in {shot.background_description}")
    elif scene_description:
        parts.append(f"in {scene_description}")

    # Camera
    camera_parts: list[str] = []
    if shot.frame_type:
        camera_parts.append(f"{shot.frame_type} shot")
    if shot.camera_angle and shot.camera_angle != "eye-level":
        camera_parts.append(f"{shot.camera_angle} angle")
    if camera_parts:
        parts.append(", ".join(camera_parts))

    # Lighting
    if shot.lighting and shot.lighting != "natural":
        parts.append(f"{shot.lighting} lighting")

    # Style tags
    style_tags: list[str] = []
    if style:
        style_tags.append(style)
    style_tags.extend(["cinematic", "high quality", "detailed", "4K"])
    parts.append(", ".join(style_tags))

    # Negative hints (appended as a note to the prompt)
    prompt = ". ".join(parts) + "."

    if shot.negative_prompt:
        prompt += f" Avoid: {shot.negative_prompt}."

    return prompt
