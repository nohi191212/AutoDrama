from __future__ import annotations

from pathlib import Path

from autodrama.core.schemas import StoryboardEpisodeOutput
from autodrama.repositories.project_layout import ProjectLayout
from autodrama.repositories.project_repo import ProjectRepository


class StoryboardRepository:
    def __init__(self, repo: ProjectRepository, layout: ProjectLayout) -> None:
        self.repo = repo
        self.layout = layout

    def path(self, project_dir: Path, episode_key: str) -> Path:
        return self.layout.shot_path(project_dir, episode_key)

    def load(self, project_dir: Path, episode_key: str) -> StoryboardEpisodeOutput:
        path = self.path(project_dir, episode_key)
        if not path.exists():
            raise FileNotFoundError(f"Storyboard shot file not found: {path}")
        return StoryboardEpisodeOutput.model_validate_json(path.read_text(encoding="utf-8"))

    def save(self, project_dir: Path, episode: StoryboardEpisodeOutput) -> None:
        self.repo.write_json(
            self.path(project_dir, episode.episode_key),
            episode.model_dump(mode="json", exclude_none=True),
        )
