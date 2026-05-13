"""Retry decorator with exponential backoff."""

import functools
import random
import time

from loguru import logger


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
):
    """Decorator that retries a function on failure with exponential backoff.

    Args:
        max_attempts: Maximum attempts before re-raising the last exception.
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Ceiling for computed delay.
        backoff_factor: Multiplier applied to delay on each retry.
        jitter: Add uniform random jitter (±50%) to avoid thundering herd.
        exceptions: Exception types that trigger a retry.
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        raise
                    delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)
                    if jitter:
                        delay *= 0.5 + random.random() * 0.5
                    logger.warning(
                        f"Attempt {attempt}/{max_attempts} failed: {exc}. "
                        f"Retrying in {delay:.1f}s…"
                    )
                    time.sleep(delay)
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator
