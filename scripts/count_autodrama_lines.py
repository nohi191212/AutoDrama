from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TARGET = ROOT_DIR / "autodrama"
DEFAULT_EXTENSIONS = {".py"}
SKIP_DIR_NAMES = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "site-packages",
}


@dataclass
class FileStats:
    path: Path
    total_lines: int
    blank_lines: int
    comment_lines: int

    @property
    def code_lines(self) -> int:
        return self.total_lines - self.blank_lines - self.comment_lines


def parse_extensions(values: list[str] | None) -> set[str]:
    if not values:
        return set(DEFAULT_EXTENSIONS)
    extensions: set[str] = set()
    for value in values:
        for raw_part in value.split(","):
            part = raw_part.strip().lower()
            if not part:
                continue
            extensions.add(part if part.startswith(".") else f".{part}")
    return extensions


def iter_files(target: Path, extensions: set[str]) -> list[Path]:
    files: list[Path] = []
    for path in target.rglob("*"):
        if any(part in SKIP_DIR_NAMES for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.suffix.lower() not in extensions:
            continue
        files.append(path)
    return sorted(files)


def count_file(path: Path) -> FileStats:
    total_lines = 0
    blank_lines = 0
    comment_lines = 0
    with path.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            total_lines += 1
            stripped = line.strip()
            if not stripped:
                blank_lines += 1
            elif stripped.startswith("#"):
                comment_lines += 1
    return FileStats(
        path=path,
        total_lines=total_lines,
        blank_lines=blank_lines,
        comment_lines=comment_lines,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Count source lines under the autodrama directory.",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=str(DEFAULT_TARGET),
        help="Directory to scan. Defaults to ./autodrama.",
    )
    parser.add_argument(
        "--ext",
        action="append",
        help="Comma-separated extensions to count. Defaults to .py. Example: --ext py,toml",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Show the largest N files by physical line count. Defaults to 10.",
    )
    args = parser.parse_args()

    target = Path(args.target).expanduser()
    if not target.is_absolute():
        target = (ROOT_DIR / target).resolve()
    if not target.exists() or not target.is_dir():
        raise SystemExit(f"Target directory does not exist: {target}")

    extensions = parse_extensions(args.ext)
    stats = [count_file(path) for path in iter_files(target, extensions)]
    total_lines = sum(item.total_lines for item in stats)
    blank_lines = sum(item.blank_lines for item in stats)
    comment_lines = sum(item.comment_lines for item in stats)
    code_lines = sum(item.code_lines for item in stats)

    print(f"target={target}")
    print(f"extensions={','.join(sorted(extensions))}")
    print(f"files={len(stats)}")
    print(f"total_lines={total_lines}")
    print(f"blank_lines={blank_lines}")
    print(f"comment_lines={comment_lines}")
    print(f"code_lines={code_lines}")

    if args.top > 0 and stats:
        print()
        print(f"top_{args.top}_files_by_total_lines:")
        for item in sorted(stats, key=lambda value: value.total_lines, reverse=True)[: args.top]:
            relative_path = item.path.relative_to(ROOT_DIR)
            print(
                f"{item.total_lines:6d} total  {item.code_lines:6d} code  "
                f"{item.blank_lines:5d} blank  {item.comment_lines:5d} comment  {relative_path}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
