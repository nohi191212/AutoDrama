from __future__ import annotations

import json
from pathlib import Path

from autodrama.core.ids import normalize_id
from autodrama.core.schemas import (
    Role,
    RoleExtractItem,
    RoleExtractOutput,
    RoleFinalizeOutput,
    RoleboardPromptItem,
    RoleboardPromptOutput,
)
from autodrama.logging import get_logger
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class RoleboardPromptRepository:
    """Persistence helper for per-role extract and roleboard prompt records."""

    SCHEMA_VERSION = 3

    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def finalize_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "role_finalize")

    def prompt_output_path(self, project_dir: Path) -> Path:
        return self.layout.node_output_path(project_dir, "roleboard_prompt")

    def item_path(self, project_dir: Path, role_id: str) -> Path:
        return self.layout.role_record_path(project_dir, role_id)

    def item_relative_path(self, project_dir: Path, role_id: str) -> str:
        return self.layout.project_relative(project_dir, self.item_path(project_dir, role_id))

    def item_relative_path_for_name(self, project_dir: Path, role_name: str) -> str:
        return self.item_relative_path(project_dir, normalize_id("role", role_name))

    def load_extract_output(self, project_dir: Path) -> RoleExtractOutput:
        path = self.finalize_output_path(project_dir)
        if not path.exists():
            raise FileNotFoundError(
                "role_finalize output is missing; run pregen until role_finalize before roleboard_prompt"
            )
        output = RoleFinalizeOutput.model_validate_json(path.read_text(encoding="utf-8"))
        return RoleExtractOutput(roles=output.final_roles)

    def load_existing_output(self, project_dir: Path) -> RoleboardPromptOutput | None:
        role_items: list[RoleboardPromptItem] = []
        roles_dir = project_dir / "assets" / "json" / "roles"
        if roles_dir.exists():
            for path in sorted(roles_dir.glob("role_*.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    if not isinstance(payload, dict):
                        raise ValueError(f"Invalid role JSON: {path}")
                    if payload.get("roleboard_prompts") is None:
                        continue
                    role_items.extend(self.load_items(path))
                except Exception as exc:
                    get_logger().warning("roleboard_prompt ignored invalid role JSON %s: %s", path, exc)
            if role_items:
                return RoleboardPromptOutput(prompts=role_items)
        return None

    def save_extract_item(self, project_dir: Path, item: RoleExtractItem) -> str:
        role_id = normalize_id("role", item.name)
        path = self.item_path(project_dir, role_id)
        self.repo.write_json(
            path,
            {
                "schema_version": self.SCHEMA_VERSION,
                "role_id": role_id,
                "role_name": item.name,
                "extract": item.model_dump(mode="json"),
                "roleboard_prompts": [],
                "state_role": None,
                "source": {
                    "role_finalize_path": self.layout.project_relative(project_dir, self.finalize_output_path(project_dir)),
                    "roleboard_prompt_path": None,
                },
            },
        )
        return self.layout.project_relative(project_dir, path)

    def save_prompt_items(
        self,
        project_dir: Path,
        *,
        extract_item: RoleExtractItem,
        prompt_items: list[RoleboardPromptItem],
        role: Role,
    ) -> str:
        role_id = normalize_id("role", extract_item.name)
        path = self.item_path(project_dir, role_id)
        relative_path = self.layout.project_relative(project_dir, path)
        role.design_path = relative_path
        dumped_items = [item.model_dump(mode="json") for item in prompt_items]
        self.repo.write_json(
            path,
            {
                "schema_version": self.SCHEMA_VERSION,
                "role_id": role_id,
                "role_name": extract_item.name,
                "extract": extract_item.model_dump(mode="json"),
                "roleboard_prompts": dumped_items,
                "state_role": role.model_dump(mode="json"),
                "source": {
                    "role_finalize_path": self.layout.project_relative(project_dir, self.finalize_output_path(project_dir)),
                    "roleboard_prompt_path": self.layout.project_relative(project_dir, self.prompt_output_path(project_dir)),
                },
            },
        )
        return relative_path

    def save_prompt_item(
        self,
        project_dir: Path,
        *,
        extract_item: RoleExtractItem,
        prompt_item: RoleboardPromptItem,
        role: Role,
    ) -> str:
        return self.save_prompt_items(
            project_dir,
            extract_item=extract_item,
            prompt_items=[prompt_item],
            role=role,
        )

    @staticmethod
    def load_items(path: Path) -> list[RoleboardPromptItem]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid roleboard prompt JSON: {path}")
        contents = payload.get("roleboard_prompts")
        if isinstance(contents, list):
            return [RoleboardPromptItem.model_validate(item) for item in contents if isinstance(item, dict)]
        raise ValueError(f"Invalid roleboard prompt content JSON: {path}")

    @staticmethod
    def load_item(path: Path) -> RoleboardPromptItem:
        items = RoleboardPromptRepository.load_items(path)
        if not items:
            raise ValueError(f"Roleboard prompt JSON has no items: {path}")
        return items[0]

    def load_items_for_role(self, project_dir: Path, role: Role) -> list[RoleboardPromptItem]:
        candidates: list[Path] = []
        if role.design_path:
            path = Path(role.design_path)
            candidates.append(path if path.is_absolute() else project_dir / path)
        candidates.append(self.item_path(project_dir, role.id))
        for path in candidates:
            if path.exists():
                return self.load_items(path)
        return []

    def load_item_for_role(self, project_dir: Path, role: Role) -> RoleboardPromptItem | None:
        items = self.load_items_for_role(project_dir, role)
        return items[0] if items else None


__all__ = ["RoleboardPromptRepository"]
