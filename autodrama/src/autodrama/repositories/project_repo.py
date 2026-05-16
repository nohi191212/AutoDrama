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

    def resolve_active_project_dir(self, project: str | None = None) -> Path:
        if project:
            return self.resolve_project_dir(project)

        configured_project_id = self.settings.configured_project_id()
        if configured_project_id:
            return self.settings.project_dir(configured_project_id).resolve()

        current = self.load_current_project()
        if current and current.get("project_dir"):
            return Path(str(current["project_dir"])).resolve()
        if current and current.get("project_id"):
            return self.settings.project_dir(str(current["project_id"])).resolve()

        raise ValueError("No project specified. Set project.id in config.yaml or run init first.")

    def create_project_from_config(
        self,
        *,
        title: str | None = None,
        script_file: str | Path | None = None,
        project_id: str | None = None,
    ) -> Path:
        resolved_title = title or self.settings.project.title
        resolved_script_file = Path(script_file).expanduser().resolve() if script_file else self.settings.project.script_outline_file
        resolved_project_id = project_id or self.settings.project.id

        if not resolved_title:
            raise ValueError("Missing project title. Set project.title in config.yaml or pass --title.")
        if not resolved_script_file:
            raise ValueError(
                "Missing script outline file. Set project.script_outline_file in config.yaml or pass --script-file."
            )
        if not resolved_script_file.exists():
            raise FileNotFoundError(f"Script outline file not found: {resolved_script_file}")

        raw_script = resolved_script_file.read_text(encoding="utf-8")
        return self.create_project(
            title=resolved_title,
            raw_script=raw_script,
            project_id=resolved_project_id,
            source_script_file=resolved_script_file,
        )

    def create_project(
        self,
        *,
        title: str,
        raw_script: str,
        project_id: str | None = None,
        source_script_file: Path | None = None,
        episode_count: int | None = None,
        episode_duration_seconds: int | None = None,
    ) -> Path:
        project_id = project_id or make_project_id(title, self.settings.output.project_dir_template)
        project_dir = self.settings.project_dir(project_id)
        self._create_project_dirs(project_dir)
        resolved_episode_count = episode_count or self.settings.project.episode_count
        resolved_episode_duration_seconds = episode_duration_seconds or self.settings.project.episode_duration_seconds

        state = ProjectState(
            project_id=project_id,
            title=title,
            raw_script=raw_script,
            script=ScriptBundle(raw_script=raw_script),
            budget=BudgetState.model_validate(self.settings.budget.model_dump()),
            metadata={
                "created_by": "autodrama",
                "source_script_file": str(source_script_file) if source_script_file else None,
                "config_path": str(self.settings.config_path) if self.settings.config_path else None,
                "episode_count": resolved_episode_count,
                "episode_duration_seconds": resolved_episode_duration_seconds,
            },
        )
        self.save_state(project_dir, state)
        self.write_json(project_dir / "project.json", {"project_id": project_id, "title": title})
        self.save_current_project(project_dir, state)
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
        self.save_current_project(project_dir, state)

    def save_node_output(self, project_dir: Path, node_name: str, data: BaseModel | dict[str, Any]) -> Path:
        path = project_dir / "assets" / "json" / "nodes" / f"{node_name}.json"
        self.write_json(path, data)
        return path

    def current_project_path(self) -> Path:
        return self.settings.output.root_dir / "current_project.json"

    def save_current_project(self, project_dir: Path, state: ProjectState) -> None:
        payload = {
            "project_id": state.project_id,
            "project_dir": str(project_dir.resolve()),
            "state_path": str((project_dir / "state.json").resolve()),
            "title": state.title,
            "current_node": state.current_node,
            "completed_nodes": state.completed_nodes,
            "updated_at": state.updated_at.isoformat(),
        }
        self.write_json(self.current_project_path(), payload)

    def load_current_project(self) -> dict[str, Any] | None:
        path = self.current_project_path()
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def write_json(path: Path, data: BaseModel | dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, BaseModel):
            payload = data.model_dump(mode="json")
        else:
            payload = data
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
