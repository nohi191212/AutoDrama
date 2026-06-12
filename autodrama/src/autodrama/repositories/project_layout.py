from __future__ import annotations

from pathlib import Path

from autodrama.config import Settings


class ProjectLayout:
    """Stable project file layout helpers.

    This centralizes paths that are part of the on-disk project contract. The
    methods intentionally mirror the existing layout so callers can be migrated
    without changing generated projects.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def project_dir(self, project_id: str) -> Path:
        return self.settings.output.root_dir / project_id

    def state_path(self, project_dir: Path) -> Path:
        return project_dir / "state.json"

    def project_json_path(self, project_dir: Path) -> Path:
        return project_dir / "project.json"

    def current_project_path(self) -> Path:
        return self.settings.output.root_dir / "current_project.json"

    def node_output_path(self, project_dir: Path, node_name: str) -> Path:
        return project_dir / "assets" / "json" / "nodes" / f"{node_name}.json"

    def dynamic_assets_index_path(self, project_dir: Path) -> Path:
        return project_dir / "assets" / "json" / "assets" / "dynamic_assets.json"

    def ambient_entities_path(self, project_dir: Path) -> Path:
        return project_dir / "assets" / "json" / "assets" / "ambient_entities.json"

    def script_content_path(self, project_dir: Path, category: str, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "scripts" / category / f"{episode_key}.json"

    def script_novel_legacy_episode_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "scripts" / f"{episode_key}.json"

    def script_novel_legacy_full_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "scripts" / "novel" / f"{episode_key}.json"

    def role_record_path(self, project_dir: Path, role_id: str) -> Path:
        return project_dir / "assets" / "json" / "roles" / f"{role_id}.json"

    def prop_design_path(self, project_dir: Path, prop_id: str) -> Path:
        return project_dir / "assets" / "json" / "props" / f"{prop_id}.json"

    def shot_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "shots" / f"{episode_key}.json"

    def image_asset_path(self, project_dir: Path, asset_type: str, asset_id: str) -> Path:
        return project_dir / "assets" / "images" / asset_type / f"{asset_id}.png"

    def music_asset_path(self, project_dir: Path, asset_id: str, audio_format: str | None) -> Path:
        extension = (audio_format or "mp3").lower().lstrip(".")
        if extension not in {"mp3", "wav", "m4a", "aac", "ogg"}:
            extension = "mp3"
        return project_dir / "assets" / "audios" / "bgms" / f"{asset_id}.{extension}"

    def audio_asset_path(self, project_dir: Path, asset_type: str, asset_id: str, audio_format: str | None) -> Path:
        extension = (audio_format or "mp3").lower().lstrip(".")
        extension = {"ogg_opus": "opus"}.get(extension, extension)
        if extension not in {"wav", "mp3", "pcm", "opus", "ogg", "m4a", "aac"}:
            extension = "bin"
        return project_dir / "assets" / "audios" / asset_type / f"{asset_id}.{extension}"

    def video_asset_path(self, project_dir: Path, asset_type: str, asset_id: str) -> Path:
        return project_dir / "assets" / "videos" / asset_type / f"{asset_id}.mp4"

    def edit_plan_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "assets" / "json" / "edit_plans" / f"{episode_key}.json"

    def episode_output_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "videos" / f"{episode_key}.mp4"

    def subtitle_srt_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "subtitles" / f"{episode_key}.srt"

    def subtitle_ass_path(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / "outputs" / "subtitles" / f"{episode_key}.ass"

    def editing_tmp_dir(self, project_dir: Path, episode_key: str) -> Path:
        return project_dir / ".tmp" / "editing" / episode_key

    def project_relative(self, project_dir: Path, path: Path) -> str:
        return str(path.relative_to(project_dir)).replace("\\", "/")

    def absolute_project_path(self, project_dir: Path, relative_path: str) -> str:
        return str(project_dir / relative_path)

    def existing_project_file(self, project_dir: Path, path: str | Path | None) -> str | None:
        if not path:
            return None
        resolved_path = Path(path)
        if not resolved_path.is_absolute():
            resolved_path = project_dir / resolved_path
        if not resolved_path.is_file() or resolved_path.stat().st_size <= 0:
            return None
        return self.project_relative(project_dir, resolved_path)

    def required_subdirs(self, project_dir: Path) -> list[Path]:
        configured = [project_dir / subdir for subdir in self.settings.output.subdirs.values()]
        fixed = [
            project_dir / "assets" / "json" / "nodes",
            project_dir / "assets" / "json" / "scripts",
            project_dir / "assets" / "json" / "scripts" / "outlines",
            project_dir / "assets" / "json" / "scripts" / "novel_full",
            project_dir / "assets" / "json" / "scripts" / "novel_extract",
            project_dir / "assets" / "json" / "assets",
            project_dir / "assets" / "json" / "roles",
            project_dir / "assets" / "json" / "props",
            project_dir / "assets" / "images" / "roles",
            project_dir / "assets" / "images" / "key_visions",
            project_dir / "assets" / "images" / "storyboards",
            project_dir / "assets" / "images" / "props",
            project_dir / "assets" / "images" / "layouts",
            project_dir / "assets" / "images" / "ref_frames",
            project_dir / "assets" / "audios" / "bgms",
            project_dir / "assets" / "audios" / "shot_dialogues",
            project_dir / "assets" / "videos" / "roles",
            project_dir / "assets" / "videos" / "shots",
            project_dir / "shots",
        ]
        return list(dict.fromkeys(configured + fixed))
