from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from autodrama.core.schemas import DynamicAssetIndex, DynamicAssetSolidificationItem
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class DynamicAssetRepository:
    """Persistence helper for the cumulative dynamic asset index."""

    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def index_path(self, project_dir: Path) -> Path:
        return self.layout.dynamic_assets_index_path(project_dir)

    def load_index(self, project_dir: Path) -> list[DynamicAssetSolidificationItem]:
        path = self.index_path(project_dir)
        if not path.exists():
            return []
        payload = DynamicAssetIndex.model_validate_json(path.read_text(encoding="utf-8"))
        return list(payload.assets)

    def save_index(
        self,
        project_dir: Path,
        items: Iterable[DynamicAssetSolidificationItem],
    ) -> Path:
        path = self.index_path(project_dir)
        self.repo.write_json(path, DynamicAssetIndex(assets=list(items)))
        return path

    def merge_episode_assets(
        self,
        project_dir: Path,
        episode_key: str,
        new_items: Iterable[DynamicAssetSolidificationItem],
    ) -> list[DynamicAssetSolidificationItem]:
        return self.merge_assets(project_dir, new_items, replace_episode_keys={episode_key})

    def merge_assets(
        self,
        project_dir: Path,
        new_items: Iterable[DynamicAssetSolidificationItem],
        *,
        replace_episode_keys: set[str] | None = None,
        replace_keys: set[tuple[str, str]] | None = None,
    ) -> list[DynamicAssetSolidificationItem]:
        normalized_items = [
            DynamicAssetSolidificationItem.model_validate(item)
            for item in new_items
        ]
        existing_items = self.load_index(project_dir)

        if replace_episode_keys:
            existing_items = [
                item
                for item in existing_items
                if item.episode_key not in replace_episode_keys
            ]
        if replace_keys:
            existing_items = [
                item
                for item in existing_items
                if (item.episode_key, item.asset_id) not in replace_keys
            ]

        merged = [*existing_items, *normalized_items]
        self.save_index(project_dir, merged)
        return merged


__all__ = ["DynamicAssetRepository"]
