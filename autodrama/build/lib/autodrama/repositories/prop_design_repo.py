from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autodrama.core.ids import normalize_id, slugify
from autodrama.core.schemas import Prop, PropAsset, PropExtractOutput
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class PropDesignRepository:
    """Read and write grouped prop asset records."""

    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def extract_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "prop_extract")

    def finalize_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "prop_finalize")

    def item_path(self, project_dir: Path, prop_id: str) -> Path:
        return self.layout.prop_design_path(project_dir, prop_id)

    def item_relative_path(self, project_dir: Path, prop_id: str) -> str:
        return self.layout.project_relative(project_dir, self.item_path(project_dir, prop_id))

    @staticmethod
    def status_key(value: object) -> str:
        status = str(value or "normal").strip().lower()
        return slugify(status, fallback="normal").lower() or "normal"

    @staticmethod
    def prop_id(name: str) -> str:
        return normalize_id("prop", name)

    @classmethod
    def asset_id(cls, prop_name: str, asset_name: str, status: object = "normal") -> str:
        prop_id = cls.prop_id(prop_name)
        asset_slug = slugify(asset_name, fallback="base")
        if asset_slug == "base":
            return f"{prop_id}__base"
        status_slug = cls.status_key(status)
        if status_slug != "normal" and status_slug != asset_slug:
            asset_slug = f"{asset_slug}_{status_slug}"
        return f"{prop_id}__{asset_slug}"

    def load_extract_output(self, project_dir: Path) -> PropExtractOutput:
        path = self.extract_output_path(project_dir)
        if not path.exists():
            raise FileNotFoundError(
                "prop_extract output is missing; run pregen --only prop_extract before prop_finalize"
            )
        return PropExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def save_prop_record(
        self,
        project_dir: Path,
        prop: Prop,
        *,
        node_name: str,
        extra_payload: dict[str, Any] | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "node_name": node_name,
            "prop_id": prop.id,
            "prop_name": prop.name,
            "source": prop.source,
            "owner_role_id": prop.owner_role_id,
            "owner_role_name": prop.owner_role_name,
            "content": prop.model_dump(mode="json"),
        }
        if extra_payload:
            payload.update(extra_payload)
        path = self.item_path(project_dir, prop.id)
        self.repo.write_json(path, payload)
        prop.design_path = self.layout.project_relative(project_dir, path)
        return prop.design_path

    def load_prop_content(self, project_dir: Path, prop: Prop) -> dict[str, Any]:
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
                raise ValueError(f"Invalid prop JSON: {path}")
            content = payload.get("content", payload)
            if not isinstance(content, dict):
                raise ValueError(f"Invalid prop content JSON: {path}")
            return content
        return {}

    def update_asset_prompt(self, project_dir: Path, prop: Prop, asset: PropAsset, prompt: str) -> None:
        asset.prompt = prompt
        self.save_prop_record(
            project_dir,
            prop,
            node_name="prop_prompt",
            extra_payload={"source_prop_finalize_path": "assets/json/nodes/prop_finalize.json"},
        )

    def update_asset_image_result(self, project_dir: Path, prop: Prop, asset: PropAsset, result, asset_path: str) -> None:
        asset.asset_id = asset.id
        asset.asset_path = asset_path
        image_urls = getattr(result, "image_urls", None)
        asset.asset_url = image_urls[0] if image_urls else asset.asset_url
        asset.provider = result.provider
        asset.model = result.model
        asset.request_id = result.request_id
        asset.usage = result.usage
        payload_extra = {
            "last_image_generation": {
                "asset_id": asset.asset_id,
                "asset_path": asset.asset_path,
                "asset_url": asset.asset_url,
                "provider": result.provider,
                "model": result.model,
                "request_id": result.request_id,
                "usage": result.usage,
                "raw_response": result.raw_response,
            }
        }
        self.save_prop_record(project_dir, prop, node_name="prop_image_generation", extra_payload=payload_extra)


__all__ = ["PropDesignRepository"]
