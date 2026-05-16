from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

LOGGER_NAME = "autodrama"
BLUE = "\033[34m"
RESET = "\033[0m"


def enable_windows_ansi() -> None:
    if os.name != "nt":
        return

    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        return


class AutoDramaFormatter(logging.Formatter):
    def __init__(self, *, color: bool) -> None:
        prefix = f"{BLUE}[autodrama]{RESET}" if color else "[autodrama]"
        super().__init__(
            fmt=f"{prefix} %(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def setup_logging(project_dir: Path | None = None, *, level: int = logging.INFO) -> logging.Logger:
    enable_windows_ansi()

    logger = get_logger()
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(AutoDramaFormatter(color=True))
    logger.addHandler(console_handler)

    if project_dir is not None:
        log_dir = project_dir / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "pipeline.log", encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(AutoDramaFormatter(color=False))
        logger.addHandler(file_handler)

    return logger
