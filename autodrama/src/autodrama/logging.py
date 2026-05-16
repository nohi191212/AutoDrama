from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

LOGGER_NAME = "autodrama"
PREGEN_DETAIL_LOGGER_NAME = "autodrama.pregen_detail"
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


def get_pregen_detail_logger() -> logging.Logger:
    return logging.getLogger(PREGEN_DETAIL_LOGGER_NAME)


def _clear_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def setup_logging(project_dir: Path | None = None, *, level: int = logging.INFO) -> logging.Logger:
    enable_windows_ansi()

    logger = get_logger()
    logger.setLevel(level)
    logger.propagate = False

    detail_logger = get_pregen_detail_logger()
    detail_logger.setLevel(logging.INFO)
    detail_logger.propagate = False

    _clear_handlers(logger)
    _clear_handlers(detail_logger)

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

        detail_handler = logging.FileHandler(log_dir / "pregen_detail.log", encoding="utf-8")
        detail_handler.setLevel(logging.INFO)
        detail_handler.setFormatter(logging.Formatter("%(message)s"))
        detail_logger.addHandler(detail_handler)

    return logger
