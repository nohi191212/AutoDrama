from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_CHAPTER_FILENAME_RE = re.compile(r"^chap(?P<number>[0-9]{4})_(?P<title>.+)\.txt$")
_CHAPTER_HEADING_RE = re.compile(
    r"(?m)^\s*第\s*(?:[0-9０-９]+|[零〇一二两三四五六七八九十百千万亿]+)\s*章(?:\s+.+)?\s*$"
)


@dataclass(frozen=True)
class ScriptChapter:
    number: int
    title: str
    path: Path
    content: str

    @property
    def filename(self) -> str:
        return self.path.name


def load_script_chapters(chapters_dir: Path) -> list[ScriptChapter]:
    chapters_dir = chapters_dir.expanduser().resolve()
    if not chapters_dir.is_dir():
        raise FileNotFoundError(f"Script chapters directory not found: {chapters_dir}")

    files = sorted(path for path in chapters_dir.iterdir() if path.is_file())
    if not files:
        raise ValueError(f"Script chapters directory is empty: {chapters_dir}")

    chapters: list[ScriptChapter] = []
    seen_numbers: set[int] = set()
    for path in files:
        match = _CHAPTER_FILENAME_RE.fullmatch(path.name)
        if not match:
            raise ValueError(
                "Every file in the script chapters directory must match "
                f"chap####_chapter-title.txt; invalid file: {path.name}"
            )
        number = int(match.group("number"))
        if not 1 <= number <= 9999:
            raise ValueError(f"Chapter number must be between 0001 and 9999: {path.name}")
        if number in seen_numbers:
            raise ValueError(f"Duplicate chapter number {number:04d}: {path.name}")
        seen_numbers.add(number)

        content = path.read_text(encoding="utf-8-sig")
        if not content.strip():
            raise ValueError(f"Chapter file is empty: {path}")
        if _CHAPTER_HEADING_RE.search(content):
            raise ValueError(
                f"Chapter file must not contain a chapter heading; remove it from: {path.name}"
            )
        chapters.append(
            ScriptChapter(
                number=number,
                title=match.group("title").strip(),
                path=path,
                content=content.strip(),
            )
        )

    return sorted(chapters, key=lambda chapter: chapter.number)


def combine_chapter_contents(chapters: list[ScriptChapter]) -> str:
    return "\n\n".join(chapter.content for chapter in chapters)


__all__ = [
    "ScriptChapter",
    "combine_chapter_contents",
    "load_script_chapters",
]
