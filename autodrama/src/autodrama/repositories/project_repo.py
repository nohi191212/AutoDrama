from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autodrama.config import Settings
from autodrama.core.ids import make_project_id
from autodrama.core.schemas import BudgetState, ProjectState, ScriptBundle


class ProjectRepository:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def resolve_project_dir(self, project: str) -> Path:
        candidate = Path(project).expanduser()
        if candidate.exists():
            return candidate.resolve()
        return self.settings.project_dir(project).resolve()

    def create_project(
        self,
        *,
        title: str,
        raw_script: str,
        project_id: str | None = None,
    ) -> Path:
        project_id = project_id or make_project_id(title, self.settings.output.project_dir_template)
        project_dir = self.settings.project_dir(project_id)
        self._create_project_dirs(project_dir)

        state = ProjectState(
            project_id=project_id,
            title=title,
            raw_script=raw_script,
            script=ScriptBundle(raw_script=raw_script),
            budget=BudgetState.model_validate(self.settings.budget.model_dump()),
            metadata={"created_by": "autodrama"},
        )
        self.save_state(project_dir, state)
        self.write_json(project_dir / "project.json", {"project_id": project_id, "title": title})
        return project_dir

    def _create_project_dirs(self, project_dir: Path) -> None:
        project_dir.mkdir(parents=True, exist_ok=True)
        for subdir in self.settings.output.subdirs.values():
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)
        (project_dir / "assets" / "json" / "nodes").mkdir(parents=True, exist_ok=True)
        (project_dir / "assets" / "json" / "scripts").mkdir(parents=True, exist_ok=True)
        (project_dir / "assets" / "json" / "roles").mkdir(parents=True, exist_ok=True)

    def load_state(self, project_dir: Path) -> ProjectState:
        path = project_dir / "state.json"
        return ProjectState.model_validate_json(path.read_text(encoding="utf-8"))

    def save_state(self, project_dir: Path, state: ProjectState) -> None:
        state.updated_at = datetime.now()
        self.write_json(project_dir / "state.json", state)

    def save_node_output(self, project_dir: Path, node_name: str, data: BaseModel | dict[str, Any]) -> Path:
        path = project_dir / "assets" / "json" / "nodes" / f"{node_name}.json"
        self.write_json(path, data)
        return path

    @staticmethod
    def write_json(path: Path, data: BaseModel | dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, BaseModel):
            payload = data.model_dump(mode="json")
        else:
            payload = data
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
