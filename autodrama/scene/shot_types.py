"""Shot type definitions and registry."""

from __future__ import annotations

CAMERA_ANGLES = ["eye-level", "high-angle", "low-angle", "dutch", "bird-eye", "worm-eye"]

FRAME_TYPES = ["extreme-close-up", "close-up", "medium", "long", "wide", "extreme-wide"]

LIGHTING_STYLES = [
    "natural",
    "cinematic",
    "high-key",
    "low-key",
    "backlit",
    "golden-hour",
    "neon",
    "moody",
    "flat",
]


class ShotTypeRegistry:
    """Lookup tables for shot composition parameters."""

    @staticmethod
    def camera_angles() -> list[str]:
        return CAMERA_ANGLES

    @staticmethod
    def frame_types() -> list[str]:
        return FRAME_TYPES

    @staticmethod
    def lighting_styles() -> list[str]:
        return LIGHTING_STYLES

    @staticmethod
    def recommended_frame_for_emotion(emotion: str) -> str:
        """Suggest a frame type for a given character emotion."""
        mapping: dict[str, str] = {
            "angry": "close-up",
            "sad": "close-up",
            "happy": "medium",
            "surprised": "close-up",
            "fearful": "close-up",
            "neutral": "medium",
            "romantic": "close-up",
            "suspicious": "medium",
        }
        return mapping.get(emotion.lower(), "medium")
