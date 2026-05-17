from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator

from autodrama.core.visual_style import VisualStyle, normalize_visual_style


class AppSettings(BaseModel):
    env: str = "dev"
    default_quality_preset: Literal["cheap", "balanced", "quality"] = "cheap"
    enable_human_review: bool = False


class ProjectSettings(BaseModel):
    id: str | None = None
    title: str | None = None
    script_outline_file: Path | None = None
    episode_count: int = Field(default=1, ge=1)
    episode_duration_seconds: int = Field(default=30, ge=1)
    visual_style: VisualStyle = "live_action"

    @field_validator("visual_style", mode="before")
    @classmethod
    def normalize_visual_style_value(cls, value: object) -> VisualStyle:
        return normalize_visual_style(value)


class OutputSettings(BaseModel):
    root_dir: Path = Path("./outputs")
    project_dir_template: str = "{date}_{slug}"
    db_filename: str = "autodrama.sqlite"
    subdirs: dict[str, str] = Field(
        default_factory=lambda: {
            "logs": "logs",
            "assets": "assets",
            "outputs": "outputs",
            "images": "assets/images",
            "videos": "assets/videos",
            "audios": "assets/audios",
            "json": "assets/json",
        }
    )


class RuntimeSettings(BaseModel):
    python: dict[str, str] = Field(default_factory=dict)
    max_text_retry: int = 3
    max_media_retry: int = 2
    request_timeout_seconds: int = 120
    ffmpeg_path: str = "ffmpeg"


class BudgetSettings(BaseModel):
    max_total_cny: float = 100
    max_image_count: int = 20
    max_video_seconds: float = 45
    max_music_count: int = 5
    max_text_calls: int = 200


class ProviderSettings(BaseModel):
    base_url: str | None = None
    api_key_env: str | None = None
    app_id_env: str | None = None
    app_key_env: str | None = None
    group_id_env: str | None = None
    access_key_env: str | None = None
    secret_key_env: str | None = None
    region: str | None = None
    models: dict[str, str] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)

    def secret(self, field_name: str) -> str | None:
        env_name = getattr(self, field_name, None)
        if not env_name:
            return None
        if isinstance(env_name, str) and env_name.startswith("sk-"):
            return env_name
        return os.getenv(env_name)


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    routing: dict[str, dict[str, str]] = Field(default_factory=dict)
    config_path: Path | None = None

    def project_dir(self, project_id: str) -> Path:
        return self.output.root_dir / project_id

    def configured_project_id(self) -> str | None:
        return self.project.id

    def provider_for(self, capability: str, purpose: str) -> str:
        try:
            return self.routing[capability][purpose]
        except KeyError as exc:
            raise KeyError(f"Missing provider routing for {capability}.{purpose}") from exc


def load_settings(config_path: str | Path) -> Settings:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = Settings.model_validate(data)
    settings.config_path = path

    if not settings.output.root_dir.is_absolute():
        settings.output.root_dir = (path.parent / settings.output.root_dir).resolve()
    else:
        settings.output.root_dir = settings.output.root_dir.resolve()

    if settings.project.script_outline_file and not settings.project.script_outline_file.is_absolute():
        settings.project.script_outline_file = (path.parent / settings.project.script_outline_file).resolve()

    return settings
