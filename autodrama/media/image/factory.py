"""Image generator factory."""

from __future__ import annotations

from autodrama.config.schema import ImageProviderConfig
from autodrama.media.image.base import BaseImageGenerator


class ImageFactory:
    """Create image generator instances by name."""

    _registry: dict[str, type[BaseImageGenerator]] = {}

    @classmethod
    def register(cls, name: str, klass: type[BaseImageGenerator]) -> None:
        cls._registry[name] = klass

    @classmethod
    def create(cls, name: str, config: ImageProviderConfig, **kwargs) -> BaseImageGenerator:
        if name not in cls._registry:
            raise KeyError(f"Unknown image provider '{name}'")
        return cls._registry[name](config, **kwargs)


# Auto-register built-in
def _register() -> None:
    from autodrama.media.image.dalle_generator import DALLEGenerator

    ImageFactory.register("openai", DALLEGenerator)


_register()
