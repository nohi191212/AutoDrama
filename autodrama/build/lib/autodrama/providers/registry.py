from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ProviderFactory = Callable[..., Any]


@dataclass(slots=True)
class ProviderRegistration:
    capability: str
    names: set[str]
    factory: ProviderFactory


class ProviderRegistry:
    def __init__(self) -> None:
        self._items: list[ProviderRegistration] = []

    def register(self, capability: str, names: set[str], factory: ProviderFactory) -> None:
        self._items.append(ProviderRegistration(capability=capability, names=set(names), factory=factory))

    def factory_for(self, capability: str, provider_name: str) -> ProviderFactory | None:
        for item in self._items:
            if item.capability == capability and provider_name in item.names:
                return item.factory
        return None


__all__ = ["ProviderFactory", "ProviderRegistration", "ProviderRegistry"]
