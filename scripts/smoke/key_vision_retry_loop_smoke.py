from __future__ import annotations

import asyncio
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "autodrama" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autodrama.core.schemas import (  # noqa: E402
    BudgetState,
    ImageAuditDimensionAssessment,
    ImageAuditRegion,
    ProjectState,
    StaticAssetGenerationItem,
)
from autodrama.image_audit_rubrics import load_key_vision_audit_rubric_bundle  # noqa: E402
from autodrama.services.director_service import DirectorService  # noqa: E402
from autodrama.utils.prompts import PromptStore  # noqa: E402
from autodrama.workflows.nodes.image_audit_nodes import (  # noqa: E402
    KeyVisionAuditDecision,
    KeyVisionImageAuditNode,
)


PROJECT_DIR = ROOT / ".tmp" / "key-vision-retry-loop-smoke"


class FakeLayout:
    @staticmethod
    def audit_rejection_log_path(project_dir: Path) -> Path:
        return project_dir / "docs" / "audit_rejections.md"

    @staticmethod
    def project_relative(project_dir: Path, path: Path) -> str:
        return str(path.relative_to(project_dir)).replace("\\", "/")


class FakeLogger:
    def warning(self, *_args: object, **_kwargs: object) -> None:
        return None


class FakeProvider:
    def __init__(self, *, reject_count: int) -> None:
        self.reject_count = reject_count
        self.calls = 0

    async def generate_json(self, _prompt: str, schema: type, **_kwargs: object) -> object:
        self.calls += 1
        bundle = load_key_vision_audit_rubric_bundle()
        reject = self.calls <= self.reject_count
        assessments = [
            ImageAuditDimensionAssessment(
                dimension_id=dimension.id,
                category=dimension.category,
                weight=dimension.weight,
                gate=dimension.gate,
                applicable=True,
                score=0 if reject and dimension.id == "continuous_space" else 10,
                severity="critical" if reject and dimension.id == "continuous_space" else "none",
                evidence=(
                    "人物与建筑相对尺度不成立。"
                    if reject and dimension.id == "continuous_space"
                    else "可见空间和构图证据完整。"
                ),
                defect="建筑与人物比例明显失真。" if reject and dimension.id == "continuous_space" else "",
                regions=(
                    [ImageAuditRegion(label="比例缺陷", x1=0.2, y1=0.2, x2=0.8, y2=0.8)]
                    if reject and dimension.id == "continuous_space"
                    else []
                ),
            )
            for dimension in bundle.dimensions
        ]
        return schema(
            approved=not reject,
            issues=["人物与建筑比例不协调"] if reject else [],
            revised_prompt="",
            rationale="比例关系需要修复。" if reject else "空间逻辑成立。",
            assessments=assessments,
        )


class SmokeRepo:
    def __init__(self) -> None:
        from autodrama.repositories.project_repo import ProjectRepository

        self._repo = ProjectRepository.__new__(ProjectRepository)
        self._repo.layout = FakeLayout()
        self.settings = SimpleNamespace(nodes={})
        self._repo.settings = self.settings

    def append_audit_rejection(self, *args: object, **kwargs: object):
        return self._repo.append_audit_rejection(*args, **kwargs)

    def save_state(self, *_args: object, **_kwargs: object) -> None:
        return None


class SmokeKeyVisionAuditNode(KeyVisionImageAuditNode):
    def __init__(self, repo: SmokeRepo, item: StaticAssetGenerationItem) -> None:
        self.repo = repo
        self.layout = FakeLayout()
        self.prompts = PromptStore(SRC / "autodrama" / "prompts")
        self.logger = FakeLogger()
        self.workflow = SimpleNamespace()
        self.item = item
        self.regenerations = 0

    def _asset_ref(self, _project_dir: Path, item: StaticAssetGenerationItem):
        from autodrama.providers.base import AssetRef

        return AssetRef(id=item.asset_id, type="image", url="https://example.invalid/key-vision.png")

    def _source_items(self, _project_dir: Path) -> list[StaticAssetGenerationItem]:
        return [self.item]

    async def _regenerate_one(self, _project_dir: Path, _state: ProjectState, _asset_id: str) -> None:
        self.regenerations += 1
        self.item = self.item.model_copy(update={"prompt": f"repair prompt {self.regenerations}"})


def make_state() -> ProjectState:
    return ProjectState.model_construct(
        project_id="key-vision-retry-loop-smoke",
        metadata={
            "visual_style_prompt": "Eastern xuanhuan stylized 3D CG animation.",
            "key_vision_prompt": {
                "shot_contract": "One continuous space with a readable character and building scale relationship.",
                "scene_style_contract": "Stylized 3D CG animation.",
                "prompt": "A continuous cinematic frame.",
            },
        },
        budget=BudgetState(),
    )


def make_item() -> StaticAssetGenerationItem:
    return StaticAssetGenerationItem(
        asset_id="key_vision_original",
        asset_type="key_vision",
        owner_id="project",
        name="主视觉原图",
        prompt="A continuous cinematic frame.",
        provider="fake",
        model="fake",
    )


def main() -> int:
    if PROJECT_DIR.exists():
        shutil.rmtree(PROJECT_DIR)
    PROJECT_DIR.mkdir(parents=True)

    repo = SmokeRepo()
    state = make_state()
    node = SmokeKeyVisionAuditNode(repo, make_item())
    accepted = asyncio.run(
        node._audit_one(
            provider=FakeProvider(reject_count=4),
            project_dir=PROJECT_DIR,
            state=state,
            initial_item=node.item,
        )
    )
    assert accepted.approved is True
    assert accepted.attempts == 4
    assert node.regenerations == 3
    assert state.metadata["key_vision_audit_forced_acceptance"]["attempt"] == 4
    assert len(state.metadata["key_vision_audit_feedback"]) == 4
    feedback = DirectorService.key_vision_audit_feedback(state)
    assert "人物与建筑比例不协调" in feedback

    log_path = PROJECT_DIR / "docs" / "audit_rejections.md"
    log_text = log_path.read_text(encoding="utf-8")
    assert log_text.count("\n## 20") == 4
    assert "人物与建筑比例不协调" in log_text

    passing_state = make_state()
    passing_node = SmokeKeyVisionAuditNode(repo, make_item())
    passing = asyncio.run(
        passing_node._audit_one(
            provider=FakeProvider(reject_count=1),
            project_dir=PROJECT_DIR,
            state=passing_state,
            initial_item=passing_node.item,
        )
    )
    assert passing.approved is True
    assert passing.attempts == 2
    assert passing_node.regenerations == 1
    print("key vision retry loop smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
