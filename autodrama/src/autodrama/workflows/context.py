from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class WorkflowRunContext:
    workflow_name: str
    project_dir: Path
    until: str | None = None
    only: str | None = None
    force: bool = False
    selected_episode_keys: list[str] | None = None
    shot_selectors: set[str] = field(default_factory=set)
    burn_subtitles: bool = True

    def episode_selected(self, episode_key: str) -> bool:
        return self.selected_episode_keys is None or episode_key in self.selected_episode_key_set

    @property
    def selected_episode_key_set(self) -> set[str]:
        return set(self.selected_episode_keys or [])

    @property
    def has_shot_selectors(self) -> bool:
        return bool(self.shot_selectors)
