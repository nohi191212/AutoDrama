from __future__ import annotations

import json
from pathlib import Path

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import Role, RoleDesignItem, RoleDesignOutput, RoleExtractOutput
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class RoleDesignRepository:
    """Persistence helper for role extract/design JSON compatibility."""

    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def extract_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "role_extract")

    def design_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "role_design")

    def item_path(self, project_dir: Path, role_id: str) -> Path:
        return self.layout.role_design_path(project_dir, role_id)

    def item_relative_path(self, project_dir: Path, role_id: str) -> str:
        return self.layout.project_relative(project_dir, self.item_path(project_dir, role_id))

    def load_extract_output(self, project_dir: Path) -> RoleExtractOutput:
        path = self.extract_output_path(project_dir)
        if not path.exists():
            raise FileNotFoundError(
                "role_extract output is missing; run pregen --only role_extract before role_design"
            )
        return RoleExtractOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def load_existing_output(self, project_dir: Path) -> RoleDesignOutput | None:
        role_items: list[RoleDesignItem] = []
        roles_dir = project_dir / "assets" / "json" / "roles"
        if roles_dir.exists():
            for path in sorted(roles_dir.glob("role_*.json")):
                try:
                    role_items.append(self.load_item(path))
                except Exception as exc:
                    get_logger().warning("role_design ignored invalid role design JSON %s: %s", path, exc)
            if role_items:
                return RoleDesignOutput(roles=role_items)

        path = self.design_output_path(project_dir)
        if not path.exists():
            return None
        return RoleDesignOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def save_item(self, project_dir: Path, item: RoleDesignItem) -> str:
        role_id = normalize_id("role", item.name)
        path = self.item_path(project_dir, role_id)
        self.repo.write_json(
            path,
            {
                "node_name": "role_design",
                "role_id": role_id,
                "role_name": item.name,
                "content": item.model_dump(mode="json"),
                "source_role_extract_path": "assets/json/nodes/role_extract.json",
            },
        )
        return self.layout.project_relative(project_dir, path)

    @staticmethod
    def load_item(path: Path) -> RoleDesignItem:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid role design JSON: {path}")
        content = payload.get("content", payload)
        if not isinstance(content, dict):
            raise ValueError(f"Invalid role design content JSON: {path}")
        return RoleDesignItem.model_validate(content)

    def load_item_for_role(self, project_dir: Path, role: Role) -> RoleDesignItem | None:
        candidates: list[Path] = []
        if role.design_path:
            path = Path(role.design_path)
            candidates.append(path if path.is_absolute() else project_dir / path)
        candidates.append(self.item_path(project_dir, role.id))
        for path in candidates:
            if path.exists():
                return self.load_item(path)
        return None


__all__ = ["RoleDesignRepository"]
