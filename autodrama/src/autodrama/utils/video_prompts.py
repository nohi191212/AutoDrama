from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Sequence


_KLING_PLACEHOLDER_PATTERN = re.compile(r"<<<\s*(?:image|element|video|audio)_\d+\s*>>>", re.IGNORECASE)
_SPOKEN_UNIT_PATTERN = re.compile(r"[\u3400-\u9fffA-Za-z0-9]")
_PROP_TRANSFER_PATTERN = re.compile(
    r"(?i)(?:\bhand(?:s|ed|ing)?\s+(?:(?:the|a|an|this|that)\b|(?:it|them)\b|(?:over|off|to)\b)|"
    r"\bgiv(?:e|es|en|ing)\b|\breceiv(?:e|es|ed|ing)\b|\btransfer(?:s|red|ring)?\b|"
    r"交给|递给|接过|接住|交接|传递|移交)"
)
_GROUNDED_LOCOMOTION_PATTERN = re.compile(
    r"(?i)(?:\bwalk(?:s|ed|ing)?\b|\bstep(?:s|ped|ping)?\b|\bclimb(?:s|ed|ing)?\b|"
    r"\bcross(?:es|ed|ing)?\b|\benter(?:s|ed|ing)?\b|\bapproach(?:es|ed|ing)?\b|"
    r"\bfootfall\b|\bmove(?:s|d|ing)? forward\b|迈步|跨过|跨入|跨越|走向|行走|登上|攀登|前行|进入)"
)

VIDEO_PROMPT_STYLE_PREFIX = "Use @shot_keyframe as the opening frame of a single continuous"


@dataclass(frozen=True, slots=True)
class VideoDialogueCue:
    speaker: str
    text: str
    delivery_mode: str = "on_screen"
    emotion: str = "normal"
    role_token: str | None = None


@dataclass(frozen=True, slots=True)
class VideoRoleBinding:
    role_token: str
    name: str


VideoPromptProfile = Literal[
    "silent_action",
    "voiceover",
    "offscreen_dialogue",
    "short_dialogue",
    "medium_dialogue",
    "long_dialogue",
    "multi_dialogue",
]


def sanitize_video_prompt_text(value: object) -> str:
    """Remove exact provider protocol placeholders without changing shot semantics."""

    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    text = _KLING_PLACEHOLDER_PATTERN.sub("", text)
    return " ".join(text.split()).strip()


def spoken_unit_count(text: object) -> int:
    """Count speech-bearing characters without treating punctuation as performance time."""

    return len(_SPOKEN_UNIT_PATTERN.findall(str(text or "")))


def classify_video_prompt_profile(
    dialogue_cues: Sequence[VideoDialogueCue],
    *,
    duration_seconds: float,
) -> VideoPromptProfile:
    cues = [cue for cue in dialogue_cues if cue.text.strip()]
    if not cues:
        return "silent_action"
    if all(cue.delivery_mode.strip().casefold() == "voiceover" for cue in cues):
        return "voiceover"
    if len(cues) != 1:
        return "multi_dialogue"
    cue = cues[0]
    delivery_mode = cue.delivery_mode.strip().casefold()
    if delivery_mode == "offscreen":
        return "offscreen_dialogue"
    units = spoken_unit_count(cue.text)
    if delivery_mode == "on_screen" and units <= 8 and duration_seconds <= 6.0:
        return "short_dialogue"
    if delivery_mode == "on_screen" and units <= 18 and duration_seconds <= 8.0:
        return "medium_dialogue"
    return "long_dialogue"


def is_prop_transfer_action(action: object) -> bool:
    return bool(_PROP_TRANSFER_PATTERN.search(str(action or "")))


def is_grounded_locomotion_action(action: object) -> bool:
    return bool(_GROUNDED_LOCOMOTION_PATTERN.search(str(action or "")))


def _seconds(value: float) -> str:
    return f"{max(0.0, float(value)):.1f}"


def _speaker(cue: VideoDialogueCue) -> str:
    if cue.role_token:
        return f"{cue.role_token} ({cue.speaker})"
    return cue.speaker


def _first_audible_unit(text: str) -> str:
    match = _SPOKEN_UNIT_PATTERN.search(text)
    return match.group(0) if match else text.strip()[:1]


def _assemble_bounded_prompt(
    *,
    core_lines: Sequence[str],
    optional_style_lines: Sequence[str],
    max_characters: int | None,
) -> str:
    full_prompt = "\n".join([*core_lines, *optional_style_lines])
    if max_characters is None:
        return full_prompt

    limit = int(max_characters)
    if limit <= 0:
        raise ValueError("video prompt max_characters must be positive")
    if len(full_prompt) <= limit:
        return full_prompt

    # Story content, exact dialogue and identity bindings are never shortened to make
    # room for generic style. Drop the least important style suffixes first.
    for keep_count in range(len(optional_style_lines) - 1, -1, -1):
        bounded_prompt = "\n".join([*core_lines, *optional_style_lines[:keep_count]])
        if len(bounded_prompt) <= limit:
            return bounded_prompt

    minimum_prompt = "\n".join(core_lines)
    raise ValueError(
        "essential video prompt content requires "
        f"{len(minimum_prompt)} characters and exceeds provider limit {limit}; "
        "shorten the shot prompt or exact dialogue, or split the shot"
    )


def compile_kling_video_prompt(
    base_prompt: object,
    *,
    duration_seconds: float,
    dialogue_cues: Sequence[VideoDialogueCue] = (),
    role_bindings: Sequence[VideoRoleBinding] = (),
    action: object = "",
    camera_movement: object = "",
    has_props: bool = False,
    max_characters: int | None = None,
) -> str:
    """Compile a compact Kling prompt whose style supports editable video motion."""

    raw_prompt = str(base_prompt or "").strip()
    if raw_prompt.startswith(VIDEO_PROMPT_STYLE_PREFIX):
        if max_characters is not None and len(raw_prompt) > int(max_characters):
            raise ValueError(
                f"compiled video prompt has {len(raw_prompt)} characters and exceeds provider limit {int(max_characters)}"
            )
        return raw_prompt
    source_prompt = sanitize_video_prompt_text(raw_prompt)
    if not source_prompt:
        raise ValueError("video prompt source cannot be empty")

    duration = max(1.0, float(duration_seconds))
    cues = [cue for cue in dialogue_cues if cue.text.strip()]
    profile = classify_video_prompt_profile(cues, duration_seconds=duration)
    performance_lines = [
        f"{VIDEO_PROMPT_STYLE_PREFIX} {_seconds(duration)}-second shot."
    ]

    if profile == "short_dialogue":
        cue = cues[0]
        units = max(1, spoken_unit_count(cue.text))
        preferred_end = max(0.8, units / 5.0 + 0.3)
        speech_end = min(preferred_end, max(0.6, duration - 0.8), duration)
        performance_lines.extend(
            [
                f"Dialogue, 0.0–{_seconds(speech_end)} seconds: {_speaker(cue)} says exactly once as one connected natural phrase, “{cue.text}”",
                f"The first audible unit is “{_first_audible_unit(cue.text)}”; speech begins with the shot, without a silent setup or an internal pause. Other visible people remain silent with closed mouths.",
                f"After {_seconds(speech_end)} seconds the speaker closes the mouth; the remaining time contains only performance already present in the shot description, ending in a brief stable settle.",
            ]
        )
    elif profile == "medium_dialogue":
        cue = cues[0]
        performance_lines.extend(
            [
                f"Dialogue: {_speaker(cue)} begins with the shot and says exactly once in one connected delivery, “{cue.text}”",
                f"The first audible unit is “{_first_audible_unit(cue.text)}”. The described performance and any supplied camera movement unfold during the words, never as a separate silent setup.",
                "Other visible people remain silent with closed mouths. After the final word, the speaker closes the mouth and the shot settles briefly.",
            ]
        )
    elif profile == "long_dialogue":
        cue = cues[0]
        finish_by = max(1.0, duration - 1.0)
        performance_lines.extend(
            [
                f"Dialogue: {_speaker(cue)} begins within the first 0.8 seconds and says exactly once in one continuous natural delivery, “{cue.text}”",
                f"Keep punctuation pauses brief and finish by about {_seconds(finish_by)} seconds. The described performance develops during the line, with no separate silent opening.",
                "Other visible people remain silent with closed mouths. After the final word, the speaker closes the mouth and the shot settles briefly.",
            ]
        )
    elif profile == "offscreen_dialogue":
        cue = cues[0]
        performance_lines.extend(
            [
                f"Offscreen dialogue: {_speaker(cue)} begins within the first 0.8 seconds and says exactly once, “{cue.text}”",
                f"The first audible unit is “{_first_audible_unit(cue.text)}”. The voice remains outside the frame; all visible mouths stay closed while the action already described in the shot proceeds at the same time.",
            ]
        )
    elif profile == "multi_dialogue":
        performance_lines.append("Dialogue begins within the first 0.8 seconds. Speak these lines exactly once in order:")
        for cue in cues:
            performance_lines.append(f"- {_speaker(cue)} [{cue.delivery_mode}, {cue.emotion}]: “{cue.text}”")
        modes = {cue.delivery_mode.strip().casefold() for cue in cues}
        if "on_screen" in modes:
            performance_lines.append(
                "For each on-screen line, only the named speaker moves the lips; other visible people remain silent with closed mouths."
            )
        if modes & {"offscreen", "voiceover"}:
            performance_lines.append(
                "Offscreen and voiceover lines never activate a visible mouth."
            )
        performance_lines.append(
            "The performance already described in the shot unfolds during or after the spoken lines, with no separate silent opening."
        )
    elif profile == "voiceover":
        performance_lines.append("Voiceover begins with the shot and is heard exactly once in this order:")
        for cue in cues:
            performance_lines.append(f"- {_speaker(cue)}: “{cue.text}”")
        performance_lines.append(
            "All visible mouths remain closed while the action already described in the shot proceeds at the same time."
        )
    else:
        performance_lines.extend(
            [
                "Silent performance: all mouths remain closed. Begin the action already described in the shot with the opening frame; give it clear physical progression and a brief living settle, without adding another action or padding the duration with idle time.",
                "Any supplied camera movement follows the existing action at low amplitude and ends with it.",
            ]
        )
        prop_transfer = has_props and is_prop_transfer_action(action)
        if prop_transfer:
            performance_lines.append(
                "Physical style for the described transfer: contact establishes control, supported weight transfer precedes release, and separation remains clear; motion comes from touch and gravity."
            )
        elif is_grounded_locomotion_action(action):
            performance_lines.append(
                "Physical style for the described locomotion: balance, support and weight transfer remain believable; secondary motion follows the body naturally, without sliding or weightlessness."
            )
        if has_props and not prop_transfer:
            performance_lines.append(
                "Visible props remain materially consistent and passive except where the shot already describes contact or motion."
            )

    identity_lines: list[str] = []
    identity_lines.extend(
        f"Reference: {binding.role_token} is {binding.name}; preserve the same identity and costume."
        for binding in role_bindings
    )

    movement = sanitize_video_prompt_text(camera_movement)
    audio_line = "Audio: human voices and SFX only; no BGM, music, song, score or melody."
    core_lines = [*performance_lines, f"Shot: {source_prompt}", *identity_lines, audio_line]
    optional_style_lines: list[str] = []
    if movement:
        optional_style_lines.append(
            f"Camera: {movement}. Keep it subordinate to the described performance and settle it with the action."
        )
    optional_style_lines.append(
        "Natural cinematic motion, anatomy and hand articulation; stable identity, prop shape, framing and screen direction. Single take, with no added subject, text, logo, watermark or subtitles."
    )
    return _assemble_bounded_prompt(
        core_lines=core_lines,
        optional_style_lines=optional_style_lines,
        max_characters=max_characters,
    )
