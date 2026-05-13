"""Configuration loader with env-var interpolation."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from dotenv import load_dotenv

from autodrama.config.schema import AppConfig


_ENV_VAR_PATTERN = re.compile(r"\$\{(\w+)\}")


class ConfigLoader:
    """Load, validate, and provide access to configuration."""

    @staticmethod
    def load(path: str | Path = "./config/config.yaml") -> AppConfig:
        """Load YAML config, resolve ${ENV_VAR} placeholders, return AppConfig."""
        load_dotenv()

        raw_text = Path(path).read_text(encoding="utf-8")
        interpolated = ConfigLoader._resolve_env_vars(raw_text)
        raw = yaml.safe_load(interpolated)
        return AppConfig(**raw)

    @staticmethod
    def _resolve_env_vars(raw_text: str) -> str:
        """Replace ${ENV_VAR} patterns with environment variable values."""

        def _replace(match: re.Match) -> str:
            var_name = match.group(1)
            return os.environ.get(var_name, "")

        return _ENV_VAR_PATTERN.sub(_replace, raw_text)

    @staticmethod
    def save_default(path: str | Path) -> None:
        """Write a default config file scaffold."""
        app = AppConfig()
        data = _tuples_to_lists(app.model_dump())
        yaml_text = yaml.dump(
            data, default_flow_style=False, allow_unicode=True, sort_keys=False
        )
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(yaml_text, encoding="utf-8")


def _tuples_to_lists(obj):
    """Recursively convert tuples to lists for safe YAML serialization."""
    if isinstance(obj, dict):
        return {k: _tuples_to_lists(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_tuples_to_lists(v) for v in obj]
    if isinstance(obj, tuple):
        return list(obj)
    return obj
