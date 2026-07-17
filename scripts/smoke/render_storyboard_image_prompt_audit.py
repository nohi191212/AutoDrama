from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.schemas import StoryboardPromptOutput  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.nodes.storyboard_asset_nodes import StoryboardPromptNode  # noqa: E402


SEVERE_PATTERNS = {
    "program_overlay_instruction": r"系统.{0,12}覆盖|后期.{0,12}覆盖|等待后期",
    "model_must_not_draw_numbers": r"不要自行绘制数字|不得绘制数字",
    "color_annotation_conflict": r"不要使用彩色标注|箭头全部使用黑色或灰色",
    "repeated_panel_label_instruction": (
        r"(?m)^\d{2}\s*\|\s*\d{2}：\s*左上角"
        r"(?=[^；;。]{0,100}黑色)(?=[^；;。]{0,100}红色)[^；;。]{0,100}[；;。]"
    ),
}


def make_node() -> StoryboardPromptNode:
    settings = load_settings(ROOT / "config.yaml")
    repo = ProjectRepository(settings)
    return StoryboardPromptNode(
        workflow=SimpleNamespace(),
        repo=repo,
        layout=repo.layout,
        router=None,
        script_service=None,
        asset_service=None,
        script_contents=None,
        prop_designs=None,
        media_store=None,
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default="huyao")
    args = parser.parse_args()

    project_dir = ROOT / "outputs" / args.project
    source_path = project_dir / "assets" / "json" / "nodes" / "clip_storyboard_prompt.json"
    output = StoryboardPromptOutput.model_validate_json(source_path.read_text(encoding="utf-8"))
    node = make_node()
    audit_dir = ROOT / ".tmp" / "storyboard_image_prompt_audit" / args.project
    audit_dir.mkdir(parents=True, exist_ok=True)

    findings: list[str] = []
    rendered_count = 0
    annotation_clips = {color: 0 for color in ("blue", "orange", "purple", "green", "yellow")}
    color_labels = {
        "blue": "蓝色",
        "orange": "橙色",
        "purple": "紫色",
        "green": "绿色",
        "yellow": "黄色",
    }
    for episode in output.storyboards:
        for clip in episode.clips:
            prompt = node.compose_storyboard_image_prompt(episode.episode_key, clip)
            target = audit_dir / f"{clip.clip_id}.txt"
            target.write_text(prompt + "\n", encoding="utf-8", newline="\n")
            rendered_count += 1
            for finding, pattern in SEVERE_PATTERNS.items():
                if re.search(pattern, prompt, flags=re.IGNORECASE | re.DOTALL):
                    findings.append(f"{clip.clip_id}: {finding}")
            panel_lines = re.findall(r"(?m)^(0[1-9]|1[0-2])\s*\|\s*(\d{2})：", prompt)
            if len(panel_lines) != 12:
                findings.append(f"{clip.clip_id}: expected 12 panel lines, got {len(panel_lines)}")
            if "generated_by_image_model" in prompt:
                findings.append(f"{clip.clip_id}: internal metadata leaked into prompt")
            panel_text = "\n".join(
                line for line in prompt.splitlines() if re.match(r"^(0[1-9]|1[0-2])\s*\|\s*\d{2}：", line)
            )
            for color, label in color_labels.items():
                if label in panel_text:
                    annotation_clips[color] += 1

    report_path = audit_dir / "AUDIT.txt"
    warnings: list[str] = []
    if annotation_clips["purple"] < rendered_count:
        warnings.append(
            "saved panel plans predate the new semantic-color template; "
            f"purple sound annotations appear in {annotation_clips['purple']}/{rendered_count} rendered prompts"
        )
    if annotation_clips["orange"] < rendered_count:
        warnings.append(
            "saved panel plans predate the new semantic-color template; "
            f"orange action annotations appear in {annotation_clips['orange']}/{rendered_count} rendered prompts"
        )
    report_lines = [
        f"project={args.project}",
        f"rendered_prompts={rendered_count}",
        f"severe_findings={len(findings)}",
        *(findings or ["none"]),
        f"warnings={len(warnings)}",
        *(warnings or ["none"]),
        *(f"annotation_clips_{color}={count}/{rendered_count}" for color, count in annotation_clips.items()),
    ]
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8", newline="\n")
    if findings:
        raise AssertionError("; ".join(findings))
    print(f"render_storyboard_image_prompt_audit: ok ({rendered_count} prompts)")
    print(audit_dir)


if __name__ == "__main__":
    main()
