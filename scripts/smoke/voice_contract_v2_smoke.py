from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPOSITORY_ROOT / "autodrama" / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from autodrama.core.schemas import Role, RoleVoiceRequirements, SemanticProvenance
from autodrama.core.voice_catalog import (
    RoleVoiceSelectionItem,
    VoiceCatalogManifest,
    VoiceCatalogProfile,
    VoiceCatalogVoiceItem,
)
from autodrama.logging import get_logger
from autodrama.providers.local.mock.fake import FakeTextProvider, FakeVoiceDesignProvider
from autodrama.repositories.voice_catalog_repo import VoiceCatalogRepository
from autodrama.services.voice_catalog_service import VoiceCatalogService
from autodrama.workflows.nodes.voice_nodes import RoleVoiceSelectNode
from autodrama.utils.prompts import PromptStore


def _role(
    *,
    intro: str,
    gender: str = "unspecified",
    language: str = "unspecified",
) -> Role:
    return Role(
        id="role-1",
        name="测试角色",
        intro=intro,
        personality="她和他的主管都在场",
        voice_requirements=RoleVoiceRequirements(
            language=language,
            gender_presentation=gender,
            provenance=SemanticProvenance(
                source="model",
                evidence=["structured voice contract fixture"],
                confidence=0.9,
                model="smoke",
            ),
        ),
    )


def _manifest() -> VoiceCatalogManifest:
    return VoiceCatalogManifest(
        catalog_version="voice-contract-v2-smoke",
        provider="fake",
        model="fake-tts",
        sample_emotions=["normal"],
        voices=[
            VoiceCatalogVoiceItem(
                voice_label="Alpha",
                voice_type="voice-a",
                voice_catalog_key="fake:fake-tts:voice-a",
                official={"language": "中文", "gender": "female"},
            ),
            VoiceCatalogVoiceItem(
                voice_label="Beta",
                voice_type="voice-b",
                voice_catalog_key="fake:fake-tts:voice-b",
                official={"language": "中文", "gender": "male"},
            ),
            VoiceCatalogVoiceItem(
                voice_label="Unknown",
                voice_type="voice-u",
                voice_catalog_key="fake:fake-tts:voice-u",
                official={},
            ),
        ],
    )


def main() -> None:
    repo = VoiceCatalogRepository(REPOSITORY_ROOT / ".tmp" / "voice-contract-v2")
    service = VoiceCatalogService(repo)
    manifest = _manifest()

    assert manifest.voices[0].language == "zh"
    assert manifest.voices[0].gender_presentation == "female"
    assert manifest.voices[2].language == "unspecified"

    first, first_meta = service.role_voice_select_candidate_pool(
        _role(intro="她是主管，他负责汇报。"),
        manifest,
    )
    second, second_meta = service.role_voice_select_candidate_pool(
        _role(intro="完全不同且不含任何人物称谓的描述。"),
        manifest,
    )
    assert [item.voice_type for item in first] == [item.voice_type for item in second]
    assert first_meta == second_meta
    assert first_meta["require_same_gender"] is False

    female_candidates, female_meta = service.role_voice_select_candidate_pool(
        _role(intro="任意展示文本", gender="female", language="zh"),
        manifest,
    )
    female_types = [item.voice_type for item in female_candidates]
    assert "voice-a" in female_types
    assert "voice-b" not in female_types
    assert "voice-u" in female_types
    assert female_meta["skipped"]["gender_mismatch"] == 1

    renamed = manifest.model_copy(deep=True)
    renamed.voices[0].voice_label = "Renamed Without Semantic Effect"
    before, _ = service.role_voice_select_candidate_pool(
        _role(intro="任意展示文本", gender="female", language="zh"),
        manifest,
    )
    after, _ = service.role_voice_select_candidate_pool(
        _role(intro="任意展示文本", gender="female", language="zh"),
        renamed,
    )
    assert [item.voice_type for item in before] == [item.voice_type for item in after]
    assert service.profile_hash(
        manifest.voices[0],
        judge_name="fake",
    ) == service.profile_hash(
        renamed.voices[0],
        judge_name="fake",
    )

    provider = SimpleNamespace(
        settings=SimpleNamespace(options={"role_speakers": {"role-1": "voice-b"}})
    )
    manual = service.manual_override_candidate(
        provider=provider,
        manifest=manifest,
        role=_role(intro="任意展示文本", gender="female"),
    )
    assert manual is not None and manual.voice_type == "voice-b"

    fake_provider = FakeVoiceDesignProvider()
    unspecified_voice = fake_provider.resolve_role_voice(
        role_id="role-1",
        role_name="她是女性主管",
        voice_requirements=RoleVoiceRequirements().model_dump(mode="json"),
    )
    assert unspecified_voice == "fake_male_voice"
    assert fake_provider.resolve_role_voice(
        role_id="role-1",
        role_name="任意文本",
        voice_requirements=RoleVoiceRequirements(
            gender_presentation="female"
        ).model_dump(mode="json"),
    ) == "fake_female_voice"

    extraction_role = Role(
        id="role-fake-female",
        name="Fixture",
        intro="Only the extraction boundary may interpret this text.",
    )
    extraction_node = object.__new__(RoleVoiceSelectNode)
    extraction_node.workflow = SimpleNamespace(prompts=PromptStore())
    extraction_node.router = SimpleNamespace(
        text=lambda *args, **kwargs: FakeTextProvider()
    )
    extraction_node.logger = get_logger()
    asyncio.run(extraction_node.ensure_role_voice_requirements(extraction_role))
    assert extraction_role.voice_requirements.gender_presentation == "female"
    assert extraction_role.voice_requirements.provenance.source == "model"
    assert not extraction_node.voice_requirements_need_extraction(extraction_role)

    migrated_profile = VoiceCatalogProfile.model_validate(
        {
            "summary": "legacy",
            "gender_presentation": None,
            "age_impression": "young_adult_to_adult",
        }
    )
    assert migrated_profile.schema_version == 2
    assert migrated_profile.gender_presentation == "unspecified"
    assert migrated_profile.age_impression == "young_adult"

    old_selection = RoleVoiceSelectionItem(
        role_id="role-1",
        role_name="测试角色",
        selected_voice_label="Alpha",
        selected_voice_type="voice-a",
        selected_voice_catalog_key="fake:fake-tts:voice-a",
        selected_reason="legacy",
        selection_source="catalog_heuristic",
        role_profile_hash="hash",
        catalog_version=manifest.catalog_version,
        catalog_hash="catalog-hash",
        provider=manifest.provider,
        model=manifest.model,
        raw_response={
            "selection_prompt_version": RoleVoiceSelectNode.selection_prompt_version,
            "voice_contract_version": RoleVoiceSelectNode.voice_contract_version,
        },
    )
    node = object.__new__(RoleVoiceSelectNode)
    assert node.cached_selection(
        existing=old_selection,
        role_profile_hash="hash",
        manifest=manifest,
        catalog_hash="catalog-hash",
        force=False,
        catalog_repo=repo,
    ) is None

    repeat, _ = service.role_voice_select_candidate_pool(
        _role(intro="任意展示文本", gender="female", language="zh"),
        manifest,
    )
    assert [item.model_dump(mode="json") for item in before] == [
        item.model_dump(mode="json") for item in repeat
    ]

    print("voice contract v2 smoke passed")


if __name__ == "__main__":
    main()
