"""TTS generator factory."""

from __future__ import annotations

from autodrama.config.schema import TTSProviderConfig
from autodrama.media.audio.base import BaseTTSGenerator


class TTSFactory:
    """Create TTS generator instances by name."""

    _registry: dict[str, type[BaseTTSGenerator]] = {}

    @classmethod
    def register(cls, name: str, klass: type[BaseTTSGenerator]) -> None:
        cls._registry[name] = klass

    @classmethod
    def create(cls, name: str, config: TTSProviderConfig, **kwargs) -> BaseTTSGenerator:
        if name not in cls._registry:
            raise KeyError(f"Unknown TTS provider '{name}'")
        return cls._registry[name](config, **kwargs)


# Auto-register
def _register() -> None:
    from autodrama.media.audio.edge_tts_generator import EdgeTTSGenerator

    TTSFactory.register("edge_tts", EdgeTTSGenerator)


_register()
