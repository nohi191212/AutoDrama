from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import Prop, PropDesignItem, PropDesignOutput, PropExtractItem, PropExtractOutput
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class PropDesignRepository:
    """Read and write prop design records without changing the project layout."""

    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def extract_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "prop_extract")

    def design_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "prop_design")

    def item_path(self, project_dir: Path, prop_id: str) -> Path:
        return self.layout.prop_design_path(project_dir, prop_id)

    def item_relative_path(self, project_dir: Path, prop_id: str) -> str:
        return self.layout.project_relative(project_dir, self.item_path(project_dir, prop_id))

    @staticmethod
    def prop_status_key(value: object) -> str:
        status = str(value or "normal").strip().lower()
        return slugify(status, fallback="normal").lower() or "normal"

    @classmethod
    def prop_asset_id(cls, name: str, status: object) -> str:
        prop_id = normalize_id("prop", name)
        status_key = cls.prop_status_key(status)
        if status_key != "normal" and not prop_id.endswith(f"_{status_key}"):
            prop_id = f"{prop_id}_{status_key}"
        return prop_id

    def load_extract_output(self, project_dir: Path) -> PropExtractOutput:
        path = self.extract_output_path(project_dir)
        if not path.exists():
            raise FileNotFoundError(
                "prop_extract output is missing; run pregen --only prop_extract before prop_design"
            )
        return PropExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def save_extract_item(self, project_dir: Path, item: PropExtractItem) -> str:
        prop_id = self.prop_asset_id(item.name, item.status)
        path = self.item_path(project_dir, prop_id)
        extract_content = item.model_dump(mode="json")

        payload: dict[str, Any] = {}
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                payload = loaded

        is_design_payload = payload.get("node_name") in {"prop_design", "prop_prompt"} or payload.get("source") in {
            "prop_design",
            "prop_prompt",
        }
        if is_design_payload:
            payload.update(
                {
                    "prop_id": prop_id,
                    "prop_name": item.name,
                    "source_prop_extract_path": "assets/json/nodes/prop_extract.json",
                    "extract_content": extract_content,
                }
            )
        else:
            payload = {
                "node_name": "prop_extract",
                "prop_id": prop_id,
                "prop_name": item.name,
                "source": "prop_extract",
                "content": extract_content,
                "source_prop_extract_path": "assets/json/nodes/prop_extract.json",
            }

        self.repo.write_json(path, payload)
        return self.layout.project_relative(project_dir, path)

    @staticmethod
    def load_design_item(path: Path) -> PropDesignItem:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid prop design JSON: {path}")
        content = payload.get("content", payload)
        if not isinstance(content, dict):
            raise ValueError(f"Invalid prop design content JSON: {path}")
        return PropDesignItem.model_validate(content)

    def load_existing_output(self, project_dir: Path) -> PropDesignOutput | None:
        prop_items: list[PropDesignItem] = []
        props_dir = project_dir / "assets" / "json" / "props"
        if props_dir.exists():
            for path in sorted(props_dir.glob("prop_*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("node_name") not in {"prop_design", "prop_prompt"} and payload.get("source") not in {
                        "prop_design",
                        "prop_prompt",
                    }:
                        continue
                    prop_items.append(self.load_design_item(path))
                except Exception as exc:
                    get_logger().warning("prop_design ignored invalid prop design JSON %s: %s", path, exc)
            if prop_items:
                return PropDesignOutput(props=prop_items)

        path = self.design_output_path(project_dir)
        if not path.exists():
            return None
        return PropDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))

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
        if prop.asset_url:
            content["image_asset_url"] = prop.asset_url
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
        extract_content: dict[str, Any] | None = None
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_extract = loaded.get("extract_content")
                if isinstance(existing_extract, dict):
                    extract_content = existing_extract
                elif loaded.get("node_name") == "prop_extract":
                    existing_content = loaded.get("content")
                    if isinstance(existing_content, dict):
                        extract_content = existing_content
        if extract_content is not None:
            payload["extract_content"] = extract_content

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
        image_urls = getattr(result, "image_urls", None)
        asset_url = image_urls[0] if image_urls else prop.asset_url
        if asset_url:
            content["image_asset_url"] = asset_url
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
                    "asset_url": asset_url,
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
