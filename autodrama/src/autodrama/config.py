from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from autodrama.core.model_catalog import ModelCatalog, NodeModelSettings


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
    bgm_count: int = Field(default=3, ge=0)


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


class GenerationSettings(BaseModel):
    roleboard_style_reference_dir: Path | None = None
    visual_style_prompt: str = ""
    roleboard_style_prompt: str = ""
    prop_design_style_prompt: str = ""
    layout_design_style_prompt: str = ""


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
    api_keys: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)

    def secret(self, field_name: str) -> str | None:
        key_ref = getattr(self, field_name, None)
        if not key_ref:
            return None
        if not isinstance(key_ref, str):
            return None
        if key_ref in self.api_keys and self.api_keys[key_ref]:
            return self.api_keys[key_ref]
        if isinstance(key_ref, str) and key_ref.startswith("sk-"):
            return key_ref
        return os.getenv(key_ref)


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    output: OutputSettings = Field(default_factory=OutputSettings)
    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    budget: BudgetSettings = Field(default_factory=BudgetSettings)
    generation: GenerationSettings = Field(default_factory=GenerationSettings)
    providers: dict[str, ProviderSettings] = Field(default_factory=dict)
    routing: dict[str, dict[str, str]] = Field(default_factory=dict)
    model_catalog_file: Path | None = Path("./model_catalog.yaml")
    nodes: dict[str, NodeModelSettings] = Field(default_factory=dict)
    apikeys_file: Path | None = Path("./apikeys.yaml")
    api_keys: dict[str, str] = Field(default_factory=dict, exclude=True, repr=False)
    model_catalog: ModelCatalog = Field(default_factory=ModelCatalog, exclude=True, repr=False)
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


def _flatten_api_keys(value: Any, *, prefix: str | None = None) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}

    flattened: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        full_key = f"{prefix}.{key}" if prefix else key
        if isinstance(raw_value, dict):
            flattened.update(_flatten_api_keys(raw_value, prefix=full_key))
            continue
        if raw_value is None:
            continue
        flattened[full_key] = str(raw_value)
        flattened[full_key.replace(".", "_").upper()] = str(raw_value)
    return flattened


def _load_api_keys(settings: Settings, config_path: Path) -> dict[str, str]:
    apikeys_path = settings.apikeys_file
    if apikeys_path is None:
        return {}
    if not apikeys_path.is_absolute():
        apikeys_path = (config_path.parent / apikeys_path).resolve()
    if not apikeys_path.exists():
        return {}

    data = yaml.safe_load(apikeys_path.read_text(encoding="utf-8")) or {}
    return _flatten_api_keys(data)


def _resolve_optional_path(path: Path | None, config_path: Path) -> Path | None:
    if path is None:
        return None
    if not path.is_absolute():
        return (config_path.parent / path).resolve()
    return path.resolve()


def _load_model_catalog(settings: Settings, config_path: Path) -> ModelCatalog:
    catalog_path = _resolve_optional_path(settings.model_catalog_file, config_path)
    settings.model_catalog_file = catalog_path
    if catalog_path is None:
        if settings.nodes:
            raise ValueError("nodes configuration requires model_catalog_file")
        return ModelCatalog()
    if not catalog_path.exists():
        if settings.nodes:
            raise FileNotFoundError(f"Model catalog file not found: {catalog_path}")
        return ModelCatalog()

    data = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    return ModelCatalog.from_mapping(data)


def _validate_node_model_settings(settings: Settings) -> None:
    if not settings.nodes:
        return
    for node_name, node_settings in settings.nodes.items():
        spec = settings.model_catalog.validate_node_settings(node_name, node_settings)
        provider = spec.provider_name
        if not provider:
            raise ValueError(f"Model catalog entry {spec.id} must declare or imply a provider")
        if provider != "fake" and provider not in settings.providers:
            raise ValueError(
                f"nodes.{node_name}.model uses provider {provider!r}, "
                "but that provider is not configured under providers"
            )


def load_settings(config_path: str | Path) -> Settings:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    settings = Settings.model_validate(data)
    settings.config_path = path
    settings.api_keys = _load_api_keys(settings, path)
    settings.model_catalog = _load_model_catalog(settings, path)
    _validate_node_model_settings(settings)
    for provider in settings.providers.values():
        provider.api_keys = settings.api_keys

    if not settings.output.root_dir.is_absolute():
        settings.output.root_dir = (path.parent / settings.output.root_dir).resolve()
    else:
        settings.output.root_dir = settings.output.root_dir.resolve()

    if settings.project.script_outline_file and not settings.project.script_outline_file.is_absolute():
        settings.project.script_outline_file = (path.parent / settings.project.script_outline_file).resolve()
    if (
        settings.generation.roleboard_style_reference_dir
        and not settings.generation.roleboard_style_reference_dir.is_absolute()
    ):
        settings.generation.roleboard_style_reference_dir = (
            path.parent / settings.generation.roleboard_style_reference_dir
        ).resolve()
    settings.apikeys_file = _resolve_optional_path(settings.apikeys_file, path)

    return settings
