from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import logging
import os
import sys
from pathlib import Path
from typing import Iterator

LOGGER_NAME = "autodrama"
PREGEN_DETAIL_LOGGER_NAME = "autodrama.pregen_detail"
BLUE = "\033[34m"
LIGHT_RED = "\033[91m"
RESET = "\033[0m"
_LOG_CONTEXT: ContextVar[dict[str, str]] = ContextVar("autodrama_log_context", default={})


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
        self.color = color
        prefix = f"{BLUE}[autodrama]{RESET}" if color else "[autodrama]"
        super().__init__(
            fmt=f"{prefix} %(asctime)s %(levelname)s %(message)s",
            datefmt="%H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        message = super().format(record)
        if self.color and getattr(record, "console_color", None) == "light_red":
            message = message.replace(f"{BLUE}[autodrama]{RESET}", "[autodrama]", 1)
            return f"{LIGHT_RED}{message}{RESET}"
        return message


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)


def get_pregen_detail_logger() -> logging.Logger:
    return logging.getLogger(PREGEN_DETAIL_LOGGER_NAME)


@contextmanager
def log_context(**values: object) -> Iterator[None]:
    current = dict(_LOG_CONTEXT.get({}))
    for key, value in values.items():
        if value is None:
            current.pop(key, None)
            continue
        current[key] = str(value)
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


class LogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = _LOG_CONTEXT.get({})
        for key in ("node_name", "episode_key", "shot_id"):
            if not getattr(record, key, None):
                setattr(record, key, context.get(key))
        return True


def _safe_log_filename(value: object, *, fallback: str) -> str:
    text = str(value or "").strip() or fallback
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in text)
    cleaned = cleaned.strip(" .")
    return cleaned or fallback


class ContextFileRouter(logging.Handler):
    def __init__(
        self,
        log_dir: Path,
        *,
        include_node_logs: bool = True,
        include_shot_logs: bool = True,
    ) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.include_node_logs = include_node_logs
        self.include_shot_logs = include_shot_logs

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
            paths: list[Path] = []
            node_name = getattr(record, "node_name", None)
            episode_key = getattr(record, "episode_key", None)
            shot_id = getattr(record, "shot_id", None)

            if self.include_node_logs and node_name:
                node_file = _safe_log_filename(node_name, fallback="unknown_node")
                paths.append(self.log_dir / "nodes" / f"{node_file}.log")

            if self.include_shot_logs and shot_id:
                episode_dir = _safe_log_filename(episode_key, fallback="unscoped")
                shot_file = _safe_log_filename(shot_id, fallback="unknown_shot")
                paths.append(self.log_dir / "shots" / episode_dir / f"{shot_file}.log")

            for path in dict.fromkeys(paths):
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as file:
                    file.write(message)
                    file.write("\n")
        except Exception:
            self.handleError(record)


def _clear_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def setup_logging(project_dir: Path | None = None, *, level: int = logging.INFO) -> logging.Logger:
    enable_windows_ansi()

    logger = get_logger()
    logger.setLevel(level)
    logger.propagate = False
    logger.filters.clear()
    logger.addFilter(LogContextFilter())

    detail_logger = get_pregen_detail_logger()
    detail_logger.setLevel(logging.INFO)
    detail_logger.propagate = False
    detail_logger.filters.clear()
    detail_logger.addFilter(LogContextFilter())

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

        context_handler = ContextFileRouter(log_dir)
        context_handler.setLevel(level)
        context_handler.setFormatter(AutoDramaFormatter(color=False))
        logger.addHandler(context_handler)

        detail_handler = ContextFileRouter(log_dir)
        detail_handler.setLevel(logging.INFO)
        detail_handler.setFormatter(logging.Formatter("%(message)s"))
        detail_logger.addHandler(detail_handler)

    return logger
