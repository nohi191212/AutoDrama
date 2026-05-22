from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autodrama.core.schemas import Prop
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class PropDesignRepository:
    """Read and write prop design records without changing the project layout."""

    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def item_path(self, project_dir: Path, prop_id: str) -> Path:
        return self.layout.prop_design_path(project_dir, prop_id)

    def item_relative_path(self, project_dir: Path, prop_id: str) -> str:
        return self.layout.project_relative(project_dir, self.item_path(project_dir, prop_id))

    def save_record(
        self,
        project_dir: Path,
        prop: Prop,
        *,
        prompt: str,
        node_name: str,
        extra_content: dict[str, Any] | None = None,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        content: dict[str, Any] = {
            "name": prop.name,
            "desc": prop.desc,
            "prompt": prompt,
            "status": prop.status,
            "episode_keys": prop.episode_keys,
        }
        if prop.asset_path:
            content["image_asset_path"] = prop.asset_path
        if extra_content:
            content.update({key: value for key, value in extra_content.items() if value not in (None, "", [])})

        payload: dict[str, Any] = {
            "node_name": node_name,
            "prop_id": prop.id,
            "prop_name": prop.name,
            "source": prop.source,
            "owner_role_id": prop.owner_role_id,
            "owner_role_name": prop.owner_role_name,
            "content": content,
        }
        if extra_payload:
            payload.update(extra_payload)

        path = self.item_path(project_dir, prop.id)
        self.repo.write_json(path, payload)
        return self.layout.project_relative(project_dir, path)

    def load_content(self, project_dir: Path, prop: Prop) -> dict[str, Any]:
        candidates: list[Path] = []
        if prop.design_path:
            design_path = Path(prop.design_path)
            candidates.append(design_path if design_path.is_absolute() else project_dir / design_path)
        default_path = self.item_path(project_dir, prop.id)
        if default_path not in candidates:
            candidates.append(default_path)

        for path in candidates:
            if not path.exists():
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid prop design JSON: {path}")
            content = payload.get("content", payload)
            if not isinstance(content, dict):
                raise ValueError(f"Invalid prop design content JSON: {path}")
            return content
        return {}

    def update_image_result(self, project_dir: Path, prop: Prop, result, asset_path: str) -> None:
        path = self.item_path(project_dir, prop.id)
        if prop.design_path:
            design_path = Path(prop.design_path)
            path = design_path if design_path.is_absolute() else project_dir / design_path

        payload: dict[str, Any] = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded
        content = payload.get("content")
        if not isinstance(content, dict):
            content = {
                "name": prop.name,
                "desc": prop.desc,
                "prompt": str(prop.prompt or ""),
                "status": prop.status,
                "episode_keys": prop.episode_keys,
            }
            payload["content"] = content

        content["image_asset_path"] = asset_path
        payload.update(
            {
                "prop_id": prop.id,
                "prop_name": prop.name,
                "source": prop.source,
                "owner_role_id": prop.owner_role_id,
                "owner_role_name": prop.owner_role_name,
                "image_generation": {
                    "asset_id": prop.asset_id or prop.id,
                    "asset_path": asset_path,
                    "provider": result.provider,
                    "model": result.model,
                    "request_id": result.request_id,
                    "usage": result.usage,
                    "raw_response": result.raw_response,
                },
            }
        )
        self.repo.write_json(path, payload)
        prop.design_path = self.layout.project_relative(project_dir, path)


__all__ = ["PropDesignRepository"]
