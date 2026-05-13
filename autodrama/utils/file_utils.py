"""File-system utilities for temporary files and output directories."""

import shutil
import tempfile
from pathlib import Path


def ensure_dir(path: str | Path) -> Path:
    """Create a directory if it doesn't exist, returning its Path."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_path(suffix: str = "", directory: str | Path | None = None) -> Path:
    """Create a named temporary file path (caller must clean up)."""
    if directory:
        directory = str(directory)
    fd, name = tempfile.mkstemp(suffix=suffix, dir=directory)
    Path(fd).close()
    return Path(name)


def clean_temp(temp_dir: str | Path) -> None:
    """Remove the entire temp directory and its contents."""
    temp_path = Path(temp_dir)
    if temp_path.exists():
        shutil.rmtree(temp_path, ignore_errors=True)
