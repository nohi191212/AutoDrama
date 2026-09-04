from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from autodrama.config import Settings
from autodrama.core.ids import make_project_id
from autodrama.core.schemas import BudgetState, ProjectState, Role, ScriptBundle
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.services.script_chapter_service import (
    ScriptChapter,
    combine_chapter_contents,
    load_script_chapters,
)
from autodrama.visual_styles import load_visual_style


class ProjectRepository:
    AUDIT_REJECTION_LOG_HEADER = (
        "# Audit rejection log\n\n"
        "This document is append-only. Each section records a rejected audit "
        "and is available as feedback for later prompt iterations.\n"
    )

    DROPPED_STATE_METADATA_KEYS = {        "prop_extract",
        "dynamic_assets",
        "simple_script",
        "global_script",
    }

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.layout = ProjectLayout(settings)

    def resolve_project_dir(self, project: str) -> Path:
        candidate = Path(project).expanduser()
        if candidate.exists():
            return candidate.resolve()
        return self.layout.project_dir(project).resolve()

    def resolve_active_project_dir(self, project: str | None = None) -> Path:
        if project:
            return self.resolve_project_dir(project)

        configured_project_id = self.settings.configured_project_id()
        if configured_project_id:
            return self.layout.project_dir(configured_project_id).resolve()

        current = self.load_current_project()
        if current and current.get("project_dir"):
            return Path(str(current["project_dir"])).resolve()
        if current and current.get("project_id"):
            return self.layout.project_dir(str(current["project_id"])).resolve()

        raise ValueError("No project specified. Set project.id in config.yaml or run init first.")

    def create_project_from_config(
        self,
        *,
        title: str | None = None,
        chapters_dir: str | Path | None = None,
        project_id: str | None = None,
    ) -> Path:
        resolved_title = title or self.settings.project.title
        resolved_chapters_dir = (
            Path(chapters_dir).expanduser().resolve()
            if chapters_dir
            else self.settings.project.script_chapters_dir
        )
        resolved_project_id = project_id or self.settings.project.id

        if not resolved_title:
            raise ValueError("Missing project title. Set project.title in config.yaml or pass --title.")
        if not resolved_chapters_dir:
            raise ValueError(
                "Missing script chapters directory. Set project.script_chapters_dir in config.yaml "
                "or pass --chapters-dir."
            )
        chapters = load_script_chapters(resolved_chapters_dir)

        return self.create_project(
            title=resolved_title,
            raw_script=combine_chapter_contents(chapters),
            project_id=resolved_project_id,
            source_chapters_dir=resolved_chapters_dir,
            source_chapters=chapters,
            episode_count=len(chapters),
        )

    def create_project(
        self,
        *,
        title: str,
        raw_script: str,
        project_id: str | None = None,
        source_chapters_dir: Path | None = None,
        source_chapters: list[ScriptChapter] | None = None,
        episode_count: int | None = None,
        episode_duration_seconds: int | None = None,
    ) -> Path:
        project_id = project_id or make_project_id(title, self.settings.output.project_dir_template)
        project_dir = self.layout.project_dir(project_id)
        self._create_project_dirs(project_dir)
        resolved_episode_count = episode_count or self.settings.project.episode_count
        resolved_episode_duration_seconds = episode_duration_seconds or self.settings.project.episode_duration_seconds
        initial_script_refs = {
            f"episode_{index:03d}": False
            for index in range(1, resolved_episode_count + 1)
        }

        visual_style_name = self.settings.generation.visual_style
        visual_style_prompt = load_visual_style(visual_style_name)
        state = ProjectState(
            project_id=project_id,
            title=title,
            raw_script=raw_script,
            script=ScriptBundle(
                raw_script=raw_script,
                episode_outlines=initial_script_refs.copy(),
                novel_full=initial_script_refs.copy(),
            ),
            budget=BudgetState.model_validate(self.settings.budget.model_dump()),
            metadata={
                "created_by": "autodrama",
                "source_chapters_dir": str(source_chapters_dir) if source_chapters_dir else None,
                "source_chapters": [
                    {
                        "number": chapter.number,
                        "title": chapter.title,
                        "filename": chapter.filename,
                        "path": str(chapter.path),
                    }
                    for chapter in (source_chapters or [])
                ],
                "config_path": str(self.settings.config_path) if self.settings.config_path else None,
                "episode_count": resolved_episode_count,
                "episode_duration_seconds": resolved_episode_duration_seconds,
                "bgm_count": self.settings.project.bgm_count,
                "visual_style_name": visual_style_name,
                "visual_style_prompt": visual_style_prompt,
                "roleboard_style_prompt": self.settings.generation.roleboard_style_prompt,
                "prop_design_style_prompt": self.settings.generation.prop_design_style_prompt,
                "layout_design_style_prompt": self.settings.generation.layout_design_style_prompt,
            },
        )
        self.save_state(project_dir, state)
        self.write_json(self.layout.project_json_path(project_dir), {"project_id": project_id, "title": title})
        self.ensure_audit_rejection_log(project_dir)
        self.save_current_project(project_dir, state)
        return project_dir

    def _create_project_dirs(self, project_dir: Path) -> None:
        project_dir.mkdir(parents=True, exist_ok=True)
        for subdir in self.layout.required_subdirs(project_dir):
            subdir.mkdir(parents=True, exist_ok=True)

    def load_state(self, project_dir: Path) -> ProjectState:
        path = self.layout.state_path(project_dir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid state JSON: {path}")
        role_refs = self._load_role_refs(payload.get("roles", {}))
        payload["roles"] = {}
        state = ProjectState.model_validate(payload)
        for key in self.DROPPED_STATE_METADATA_KEYS:
            state.metadata.pop(key, None)
        state.metadata["role_refs"] = role_refs
        for role_name, role_ref in role_refs.items():
            role_path = self._project_path_from_ref(project_dir, role_ref)
            if not role_path.exists():
                raise FileNotFoundError(f"Role JSON for {role_name} is missing: {role_ref}")
            role_payload = json.loads(role_path.read_text(encoding="utf-8"))
            if not isinstance(role_payload, dict):
                raise ValueError(f"Invalid role JSON: {role_path}")
            payload_role_name = str(role_payload.get("role_name") or "").strip()
            if payload_role_name != role_name:
                raise ValueError(
                    f"Role JSON name mismatch for state.roles[{role_name}]: {payload_role_name or '-'}"
                )
            state_role = role_payload.get("state_role")
            if state_role is None:
                continue
            if not isinstance(state_role, dict):
                raise ValueError(f"Invalid state_role in role JSON: {role_path}")
            role = Role.model_validate(state_role)
            if role.name != role_name:
                raise ValueError(f"state_role name mismatch for state.roles[{role_name}]: {role.name}")
            if not role.design_path:
                role.design_path = role_ref
            state.roles[role.id] = role
        return state

    def save_state(self, project_dir: Path, state: ProjectState) -> None:
        state.updated_at = datetime.now()
        role_refs = self._role_refs_for_state(project_dir, state)
        state.metadata["role_refs"] = role_refs
        self._write_role_state_records(project_dir, state, role_refs)

        payload = state.model_dump(mode="json")
        metadata = dict(payload.get("metadata") or {})
        metadata.pop("role_refs", None)
        for key in self.DROPPED_STATE_METADATA_KEYS:
            metadata.pop(key, None)
            state.metadata.pop(key, None)
        payload["metadata"] = metadata
        payload["roles"] = role_refs
        self.write_json(self.layout.state_path(project_dir), payload)
        self.save_current_project(project_dir, state)

    @staticmethod
    def _load_role_refs(value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("state.roles must be a mapping of role name to role JSON path")
        role_refs: dict[str, str] = {}
        for raw_name, raw_path in value.items():
            role_name = str(raw_name or "").strip()
            if not role_name:
                raise ValueError("state.roles contains an empty role name")
            if not isinstance(raw_path, str):
                raise ValueError(f"state.roles[{role_name}] must be a role JSON path string")
            role_path = raw_path.strip().replace("\\", "/")
            if not role_path:
                raise ValueError(f"state.roles[{role_name}] is empty")
            role_refs[role_name] = role_path
        return role_refs

    def _project_path_from_ref(self, project_dir: Path, path_ref: str) -> Path:
        path = Path(path_ref)
        return path if path.is_absolute() else project_dir / path

    def _project_relative_role_ref(self, project_dir: Path, path_ref: str) -> str:
        path = Path(path_ref)
        if not path.is_absolute():
            return str(path_ref).replace("\\", "/")
        try:
            return self.layout.project_relative(project_dir, path)
        except ValueError as exc:
            raise ValueError(f"Role JSON path must be inside the project directory: {path_ref}") from exc

    def _role_refs_for_state(self, project_dir: Path, state: ProjectState) -> dict[str, str]:
        role_refs: dict[str, str] = {}
        existing_refs = state.metadata.get("role_refs")
        if existing_refs is not None:
            role_refs.update(self._load_role_refs(existing_refs))

        for role in state.roles.values():
            role_ref = role.design_path
            if role_ref:
                role_ref = self._project_relative_role_ref(project_dir, role_ref)
            else:
                role_ref = self.layout.project_relative(project_dir, self.layout.role_record_path(project_dir, role.id))
            role.design_path = role_ref
            role_refs[role.name] = role_ref
        return role_refs

    def _write_role_state_records(
        self,
        project_dir: Path,
        state: ProjectState,
        role_refs: dict[str, str],
    ) -> None:
        for role in state.roles.values():
            role_ref = role_refs.get(role.name)
            if not role_ref:
                continue
            role_path = self._project_path_from_ref(project_dir, role_ref)
            if not role_path.exists():
                raise FileNotFoundError(f"Role JSON for {role.name} is missing: {role_ref}")
            payload = json.loads(role_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid role JSON: {role_path}")
            payload["role_id"] = role.id
            payload["role_name"] = role.name
            payload["state_role"] = role.model_dump(mode="json")
            self.write_json(role_path, payload)

    def save_node_output(self, project_dir: Path, node_name: str, data: BaseModel | dict[str, Any]) -> Path:
        path = self.layout.node_output_path(project_dir, node_name)
        self.write_json(path, data)
        return path

    def ensure_audit_rejection_log(self, project_dir: Path) -> Path:
        path = self.layout.audit_rejection_log_path(project_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(self.AUDIT_REJECTION_LOG_HEADER, encoding="utf-8", newline="\n")
        return path

    def append_audit_rejection(
        self,
        project_dir: Path,
        *,
        node_name: str,
        asset_id: str,
        attempt: int,
        issues: list[str],
        rationale: str,
        current_prompt: str,
        revised_prompt: str = "",
    ) -> Path:
        """Append one durable rejection record without replacing prior feedback."""
        path = self.ensure_audit_rejection_log(project_dir)
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
        issue_lines = "\n".join(f"- {issue}" for issue in issues) or "- 未提供结构化原因"
        section = (
            f"\n## {timestamp} | {node_name} | {asset_id} | attempt {attempt}\n\n"
            f"- 审计节点：`{node_name}`\n"
            f"- 资产：`{asset_id}`\n"
            f"- 本轮尝试：`{attempt}`\n"
            "- 拒绝原因：\n"
            f"{issue_lines}\n"
            f"- 审计说明：{rationale or '未提供'}\n\n"
            "### 本轮生成提示词\n\n"
            "```text\n"
            f"{current_prompt.rstrip()}\n"
            "```\n"
        )
        if revised_prompt:
            section += (
                "\n### 审计返回的修订提示词\n\n"
                "```text\n"
                f"{revised_prompt.rstrip()}\n"
                "```\n"
            )
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(section)
            handle.flush()
        return path

    def current_project_path(self) -> Path:
        return self.layout.current_project_path()

    def save_current_project(self, project_dir: Path, state: ProjectState) -> None:
        payload = {
            "project_id": state.project_id,
            "project_dir": str(project_dir.resolve()),
            "state_path": str(self.layout.state_path(project_dir).resolve()),
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
        # 写前快照：目标文件若已存在，先把旧内容备份到同目录 _history 下（带时间戳），
        # 保证任何重跑/覆盖都不丢失历史过程产物（用户要求）。
        if path.exists():
            try:
                history_dir = path.parent / "_history"
                history_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S_%f")
                backup = history_dir / f"{path.stem}.{ts}{path.suffix}"
                backup.write_bytes(path.read_bytes())
            except Exception:
                # 备份失败不应阻断写入，降级为直接覆盖
                pass
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
