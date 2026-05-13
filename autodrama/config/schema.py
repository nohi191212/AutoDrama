"""Pydantic configuration models for AutoDrama."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class LLMProviderConfig(BaseModel):
    api_key: str = ""
    base_url: Optional[str] = None
    model: str = "gpt-4o"
    max_tokens: int = 4096
    temperature: float = 0.8
    timeout: int = 60


class ImageProviderConfig(BaseModel):
    api_key: str = ""
    model: str = "dall-e-3"
    size: str = "1792x1024"
    quality: str = "standard"


class TTSProviderConfig(BaseModel):
    api_key: str = ""
    voice: str = "zh-CN-XiaoxiaoNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    model: str = ""


class LLMConfig(BaseModel):
    default_provider: str = "openai"
    providers: dict[str, LLMProviderConfig] = Field(default_factory=dict)


class ImageConfig(BaseModel):
    default_provider: str = "openai"
    providers: dict[str, ImageProviderConfig] = Field(default_factory=dict)


class AudioConfig(BaseModel):
    default_provider: str = "edge_tts"
    providers: dict[str, TTSProviderConfig] = Field(default_factory=dict)


class MediaConfig(BaseModel):
    image: ImageConfig = Field(default_factory=ImageConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)


class PipelineConfig(BaseModel):
    max_retries: int = 3
    parallel_media_generation: bool = True
    subtitle_enabled: bool = True
    subtitle_font: str = "./fonts/NotoSansSC-Regular.ttf"
    subtitle_position: str = "bottom"
    subtitle_font_size: int = 48
    subtitle_color: str = "white"
    fps: int = 30
    resolution: tuple[int, int] = (1920, 1080)
    transition_duration: float = 0.5
    default_shot_duration: float = 3.0
    background_music_volume: float = 0.15
    dialogue_volume: float = 1.0
    cleanup_temp: bool = True
    temp_dir: str = "./temp"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "./logs/autodrama.log"
    format: str = "structured"


class ProjectConfig(BaseModel):
    name: str = "AutoDrama"
    output_dir: str = "./output"
    temp_dir: str = "./temp"


class AppConfig(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
