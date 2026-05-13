"""Structured logging via loguru."""

import sys
from pathlib import Path

from loguru import logger


def setup_logger(
    name: str = "autodrama",
    level: str = "INFO",
    log_file: str | None = None,
    structured: bool = True,
) -> None:
    """Configure loguru logger for the application.

    Args:
        name: Logger name (prepended to messages).
        level: Minimum log level (DEBUG, INFO, WARNING, ERROR).
        log_file: Optional path to a JSON-lines log file.
        structured: Emit JSON-structured records to the log file.
    """
    logger.remove()

    # Console sink — human-readable
    logger.add(
        sys.stderr,
        level=level,
        format=f"<green>{{time:HH:mm:ss}}</green> | <level>{{level: <7}}</level> | <cyan>{name}</cyan> | <level>{{message}}</level>",
        colorize=True,
    )

    # File sink — structured JSON
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        logger.add(
            log_file,
            level=level,
            format="{time} {level} {name} {message} {extra}",
            serialize=structured,
            rotation="10 MB",
            retention="7 days",
        )


__all__ = ["setup_logger", "logger"]
