from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[2]
SRC_DIR = ROOT_DIR / "autodrama" / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from autodrama.config import load_settings  # noqa: E402
from autodrama.core.errors import ProviderBadResponseError  # noqa: E402
from autodrama.core.schemas import Role, RoleAppearance  # noqa: E402
from autodrama.providers.base import AssetRef, SubjectElementResult  # noqa: E402
from autodrama.repositories.project_repo import ProjectRepository  # noqa: E402
from autodrama.workflows.pregen import PregenWorkflow  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class AlreadyExistsSubjectElementProvider:
    name = "kling_omni"
    subject_element_model = "advanced-custom-elements"
    supports_subject_elements = True
    _TERMINAL_SUCCESS = {"succeed"}
    _TERMINAL_FAILURE = {"failed"}

    def __init__(self) -> None:
        self.settings = SimpleNamespace(options={"subject_reference_type": "video_refer"})
        self.max_polls = 1
        self.poll_interval_seconds = 0
        self.generate_count = 0
        self.query_count = 0
        self.external_task_id: str | None = None
        self.metadata: dict[str, Any] = {}
        self.video_url: str | None = None

    async def generate_subject_element(
        self,
        *,
        element_name: str,
        element_description: str,
        reference_type: str,
        video_url: str | None = None,
        image_refs: list[AssetRef] | None = None,
        wait: bool = True,
        metadata: dict[str, Any] | None = None,
    ) -> SubjectElementResult:
        del element_name, element_description, image_refs, wait
        self.generate_count += 1
        self.metadata = dict(metadata or {})
        self.external_task_id = str(self.metadata.get("external_task_id") or "")
        self.video_url = video_url
        require(reference_type == "video_refer", f"unexpected reference_type: {reference_type}")
        raise ProviderBadResponseError(
            "Kling subject element submit failed with HTTP 400: "
            f'{{"code":1201,"message":"External_task_id {self.external_task_id} already exists"}}'
        )

    async def query_subject_element(
        self,
        *,
        task_id: str | None = None,
        external_task_id: str | None = None,
    ) -> SubjectElementResult:
        self.query_count += 1
        require(task_id is None, f"recovery should query by external_task_id, got task_id={task_id}")
        require(
            external_task_id == self.external_task_id,
            f"expected external task query, got {external_task_id}",
        )
        return SubjectElementResult(
            provider=self.name,
            model=self.subject_element_model,
            task_id="123456789",
            task_status="succeed",
            element_id="123456789",
            request_id="request-existing-subject",
            raw_response={
                "data": {
                    "task_id": "123456789",
                    "task_status": "succeed",
                    "task_info": {"external_task_id": external_task_id},
                    "task_result": {
                        "elements": [
                            {
                                "element_id": 123456789,
                                "element_name": "Lin Zhou",
                                "reference_type": "video_refer",
                            }
                        ]
                    },
                }
            },
        )


class AlreadyExistsRouter:
    def __init__(self, provider: AlreadyExistsSubjectElementProvider) -> None:
        self.provider = provider

    def video(self, purpose: str, *, node_name: str | None = None) -> AlreadyExistsSubjectElementProvider:
        del purpose, node_name
        return self.provider


async def main_async() -> int:
    settings = load_settings(ROOT_DIR / "config.yaml.example")
    settings.output.root_dir = ROOT_DIR / ".tmp" / "smoke"
    repo = ProjectRepository(settings)
    project_id = f"role_subject_element_already_exists_smoke_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    project_dir = repo.create_project(
        title="Role Subject Element Already Exists Smoke",
        raw_script="Lin Zhou has an existing Kling subject element task.",
        project_id=project_id,
        episode_count=1,
        episode_duration_seconds=30,
    )
    state = repo.load_state(project_dir)

    appearance = RoleAppearance(
        id="role_lin_zhou_base",
        role_id="role_lin_zhou",
        name="base",
        subject_video_asset_url="https://example.invalid/linzhou-subject.mp4",
    )
    role = Role(
        id="role_lin_zhou",
        name="Lin Zhou",
        intro="Office worker",
        visual_reuse_required=True,
        appearances={appearance.id: appearance},
    )
    state.roles[role.id] = role
    repo.write_json(
        repo.layout.role_record_path(project_dir, role.id),
        {
            "role_id": role.id,
            "role_name": role.name,
            "state_role": role.model_dump(mode="json"),
        },
    )
    repo.save_state(project_dir, state)

    provider = AlreadyExistsSubjectElementProvider()
    workflow = PregenWorkflow(repo=repo, router=AlreadyExistsRouter(provider))

    await workflow.run(project_dir, only="role_subject_element_generation")

    restored_state = repo.load_state(project_dir)
    restored_appearance = restored_state.roles[role.id].appearances[appearance.id]
    require(provider.generate_count == 1, f"expected one submit attempt, got {provider.generate_count}")
    require(provider.query_count == 1, f"expected one external task query, got {provider.query_count}")
    require(
        provider.video_url == "https://example.invalid/linzhou-subject.mp4",
        f"unexpected subject video URL: {provider.video_url}",
    )
    require(
        provider.external_task_id == f"{project_id}_{appearance.id}_subject_element",
        f"unexpected external_task_id: {provider.external_task_id}",
    )
    require(restored_appearance.subject_element_id == "123456789", "subject element id was not saved")
    require(restored_appearance.subject_element_task_id == "123456789", "subject element task id was not saved")
    require(restored_appearance.subject_element_task_status == "succeed", "subject element status was not saved")

    node_output_path = project_dir / "assets" / "json" / "nodes" / "role_subject_element_generation.json"
    node_output = json.loads(node_output_path.read_text(encoding="utf-8"))
    generated = node_output["generated_subject_elements"]
    require(len(generated) == 1, f"expected one recovered subject element item, got {len(generated)}")
    require(generated[0]["element_id"] == restored_appearance.subject_element_id, "node output element_id mismatch")
    require(generated[0]["task_id"] == restored_appearance.subject_element_task_id, "node output task_id mismatch")

    print("role_subject_element_already_exists_smoke=ok")
    print(f"project_dir={project_dir}")
    print(f"external_task_id={provider.external_task_id}")
    print(f"element_id={restored_appearance.subject_element_id}")
    return 0


def main() -> int:
    return asyncio.run(main_async())


if __name__ == "__main__":
    raise SystemExit(main())
