from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any


GENERATION_TASKS_FILENAME = "generation_tasks.json"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def generation_tasks_path(project_dir: Path) -> Path:
    return project_dir / GENERATION_TASKS_FILENAME


def empty_generation_tasks(project_id: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_id": project_id,
        "updated_at": now_iso(),
        "tasks": [],
    }


def load_generation_tasks(project_dir: Path, *, project_id: str | None = None) -> dict[str, Any]:
    path = generation_tasks_path(project_dir)
    if not path.exists():
        return empty_generation_tasks(project_id)

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        payload = empty_generation_tasks(project_id)
    payload.setdefault("schema_version", 1)
    if project_id is not None:
        payload["project_id"] = project_id
    payload.setdefault("project_id", project_id)
    payload.setdefault("updated_at", now_iso())
    if not isinstance(payload.get("tasks"), list):
        payload["tasks"] = []
    return payload


def save_generation_tasks(repo: Any, project_dir: Path, registry: dict[str, Any]) -> Path:
    registry["updated_at"] = now_iso()
    path = generation_tasks_path(project_dir)
    repo.write_json(path, registry)
    return path


def find_generation_task(registry: dict[str, Any], task_key: str) -> dict[str, Any] | None:
    for task in registry.get("tasks", []):
        if isinstance(task, dict) and task.get("task_key") == task_key:
            return task
    return None


def upsert_generation_task(registry: dict[str, Any], task_item: dict[str, Any]) -> dict[str, Any]:
    task_key = str(task_item["task_key"])
    existing = find_generation_task(registry, task_key)
    timestamp = now_iso()
    if existing is None:
        inserted = dict(task_item)
        inserted.setdefault("submitted_at", timestamp)
        inserted["updated_at"] = timestamp
        registry.setdefault("tasks", []).append(inserted)
        return inserted

    submitted_at = existing.get("submitted_at")
    existing.update(task_item)
    if submitted_at and not existing.get("submitted_at"):
        existing["submitted_at"] = submitted_at
    existing["updated_at"] = timestamp
    return existing


def task_status(task: dict[str, Any] | None) -> str:
    if not task:
        return ""
    return str(task.get("task_status") or "").strip().lower()
