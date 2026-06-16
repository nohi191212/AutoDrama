from __future__ import annotations

import re


_KLING_PLACEHOLDER_PATTERN = re.compile(r"<<<\s*(?:image|element|video|audio)_\d+\s*>>>", re.IGNORECASE)
_ASPECT_RATIO_TOKEN_PATTERN = re.compile(r"\d{1,2}\s*[:：]\s*\d{1,2}|portrait|landscape", re.IGNORECASE)
_ASPECT_RATIO_WORDS = (
    "竖版",
    "横版",
    "竖屏",
    "横屏",
    "竖构图",
    "横构图",
    "竖向构图",
    "横向构图",
    "竖画幅",
    "横画幅",
    "竖幅",
    "横幅",
)
_LEADING_SEPARATORS_PATTERN = re.compile(r"^[\s,，、;；:：。.\-]+")
_TEXT_ARTIFACT_BAN_PATTERNS = (
    re.compile(
        r"(?:画面|全程|全片|视频|片段)?(?:中)?"
        r"(?:禁止|不要|不出现|不能出现|不得出现|避免出现)"
        r"(?:任何|可见|可读|多余|无关)?"
        r"(?:字幕|水印|logo|标志|文字|文本|商标|片段编号|编号)"
        r"(?:[、，,和及或/]*(?:字幕|水印|logo|标志|文字|文本|商标|片段编号|编号))*",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:无|没有)(?:任何|可见|可读|多余|无关)?"
        r"(?:字幕|水印|logo|标志|文字|文本|商标)"
        r"(?:[、，,和及或/]*(?:字幕|水印|logo|标志|文字|文本|商标))*",
        re.IGNORECASE,
    ),
)
_CAMERA_REWRITES = (
    ("正对镜头突然转头说话", "保持三分之二侧脸角度"),
    ("突然转头对镜头说话", "保持侧向角度"),
    ("转头对镜头说话", "侧向看向画面内对象"),
    ("正对镜头说话", "以三分之二侧脸角度表演"),
    ("直视镜头说话", "视线看向镜头旁侧"),
    ("正面直视镜头", "三分之二侧脸看向镜头旁侧"),
    ("正对镜头", "带角度朝向画面内对象"),
)


def sanitize_video_prompt_text(value: object) -> str:
    """Remove provider placeholders and aspect-ratio wording from natural-language shot prompts."""

    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    text = _KLING_PLACEHOLDER_PATTERN.sub("", text)
    text = _ASPECT_RATIO_TOKEN_PATTERN.sub("", text)
    for word in _ASPECT_RATIO_WORDS:
        text = text.replace(word, "")
    for source, replacement in _CAMERA_REWRITES:
        text = text.replace(source, replacement)
    for pattern in _TEXT_ARTIFACT_BAN_PATTERNS:
        text = pattern.sub("", text)
    text = " ".join(text.split()).strip()
    text = re.sub(r"[“\"']\s*[。！？!?，,；;：:、]*\s*[”\"']", "", text)
    text = re.sub(r"[，、,；;：:]\s*([。！？!?])", r"\1", text)
    text = re.sub(r"([。！？!?]){2,}", r"\1", text)
    text = re.sub(r"\s+([。！？!?，,；;：:、])", r"\1", text)
    text = _LEADING_SEPARATORS_PATTERN.sub("", text).strip()
    return text
