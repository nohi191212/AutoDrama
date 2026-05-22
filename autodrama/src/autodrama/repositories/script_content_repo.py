from __future__ import annotations

import json
from pathlib import Path

from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class ScriptContentRepository:
    """Read and write per-episode script content without changing project paths."""

    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def content_path(self, project_dir: Path, category: str, episode_key: str) -> Path:
        return self.layout.script_content_path(project_dir, category, episode_key)

    def novel_legacy_episode_path(self, project_dir: Path, episode_key: str) -> Path:
        return self.layout.script_novel_legacy_episode_path(project_dir, episode_key)

    def novel_legacy_full_path(self, project_dir: Path, episode_key: str) -> Path:
        return self.layout.script_novel_legacy_full_path(project_dir, episode_key)

    def project_relative(self, project_dir: Path, path: Path) -> str:
        return self.layout.project_relative(project_dir, path)

    @staticmethod
    def content_payload(
        *,
        node_name: str,
        episode_key: str,
        content: str,
        dependency_field: str | None = None,
        dependency_path: object | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "node_name": node_name,
            "episode_key": episode_key,
            "content": content,
        }
        if dependency_field and dependency_path:
            payload[dependency_field] = dependency_path
        return payload

    def write_content(
        self,
        project_dir: Path,
        category: str,
        episode_key: str,
        *,
        node_name: str,
        content: str,
        dependency_field: str | None = None,
        dependency_path: object | None = None,
    ) -> str:
        path = self.content_path(project_dir, category, episode_key)
        self.repo.write_json(
            path,
            self.content_payload(
                node_name=node_name,
                episode_key=episode_key,
                content=content,
                dependency_field=dependency_field,
                dependency_path=dependency_path,
            ),
        )
        return self.project_relative(project_dir, path)

    @staticmethod
    def load_content_ref(project_dir: Path, value: object) -> str | None:
        if value is False or value is None:
            return None
        text = str(value).strip()
        if not text:
            return None

        path = Path(text)
        if not path.is_absolute():
            path = project_dir / text
        if path.exists() and path.is_file():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"Invalid script content JSON: {path}")
            for key in ("content", "novel_full", "novel_text", "episode_outline"):
                content = str(payload.get(key) or "").strip()
                if content:
                    return content
            return None

        if text.endswith(".json") or "/" in text or "\\" in text:
            return None
        return text

    @classmethod
    def load_episode_text(cls, path: Path) -> str | None:
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid script_novel episode JSON: {path}")
        text = str(payload.get("content") or payload.get("novel_full") or payload.get("novel_text") or "").strip()
        return text or None

    @classmethod
    def load_contents(
        cls,
        project_dir: Path,
        refs: dict[str, object],
        episode_keys: list[str],
        *,
        label: str,
        allow_missing: bool = False,
    ) -> dict[str, str]:
        contents: dict[str, str] = {}
        for episode_key in episode_keys:
            content = cls.load_content_ref(project_dir, refs.get(episode_key))
            if content:
                contents[episode_key] = content
            elif not allow_missing:
                raise ValueError(f"{label} is missing content for {episode_key}")
        return contents


__all__ = ["ScriptContentRepository"]
