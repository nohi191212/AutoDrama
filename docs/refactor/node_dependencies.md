# Pregen Node Dependency Inventory

This inventory captures the phase A0 baseline from `schedule2.md`. It reflects the current registry in
`workflows/nodes/__init__.py`; removed compatibility-only role voice and role appearance entry points are no
longer included.

## Registry Owners

| Node | Target owner | Primary services/providers | Repositories/layout/media helpers | State writes | Node output |
| --- | --- | --- | --- | --- | --- |
| `script_outline` | `workflows/nodes/script_nodes.py` (`ScriptOutlineNode`) | `ProviderRouter.text("script")`, `ScriptService.script_outline` | `ScriptContentRepository`, `ProjectRepository.save_node_output` | `state.script.outline`, `state.script.episode_outlines`, `state.budget.used_text_calls` | `assets/json/nodes/script_outline.json`; per-episode `assets/json/scripts/outlines/{episode_key}.json` |
| `script_novel` | `workflows/nodes/script_nodes.py` (`ScriptNovelNode`) | `ProviderRouter.text("script")`, `ScriptService.script_novel_episode` | `ScriptContentRepository`, legacy novel fallback paths | `state.script.novel_full`, `state.metadata.script_novel_*`, `state.budget.used_text_calls` | `assets/json/nodes/script_novel.json`; per-episode `assets/json/scripts/novel_full/{episode_key}.json` |
| `director_prep` | `workflows/nodes/director_nodes.py` (`DirectorPrepNode`) | `ProviderRouter.text("director")` with `text.script` fallback, `DirectorService.director_prep` | `ScriptContentRepository`, `ProjectRepository.save_node_output` | `state.metadata.director_prep*`, `state.budget.used_text_calls` | `assets/json/nodes/director_prep.json` |
| `script_novel_extract` | `workflows/nodes/script_nodes.py` (`ScriptNovelExtractNode`) | `ProviderRouter.text("script")`, `ScriptService.script_novel_extract_batch` | `ScriptContentRepository` | `state.script.novel_extract`, `state.metadata.script_novel_extract_batch_size`, `state.budget.used_text_calls` | `assets/json/nodes/script_novel_extract.json`; per-episode `assets/json/scripts/novel_extract/{episode_key}.json` |
| `role_extract` | `workflows/nodes/role_nodes.py` | `ProviderRouter.text("role")`, `RoleService.role_extract` | Script content helpers for novel text, `RoleDesignRepository` | `state.roles` refs via per-role JSON, `state.budget.used_text_calls` | `assets/json/nodes/role_extract.json`; per-role `assets/json/roles/{role_id}.json` |
| `role_design` | `workflows/nodes/role_nodes.py` | `ProviderRouter.text("role")`, `ProviderRouter.audio("speech")`, `RoleService.role_design` | Role design JSON helpers, prop design helpers | `state.roles`, role-bound `state.props`, `state.metadata.role_design_*`, `state.budget.used_text_calls` | `assets/json/nodes/role_design.json`; per-role `assets/json/roles/{role_id}.json`; role-bound prop JSON |
| `role_voice_generation` | `workflows/nodes/voice_nodes.py` | `ProviderRouter.audio("speech")`, voice creation/clone/reuse/synthesis provider APIs | `MediaStore.write_preview_audio`, role design hydration helpers | `state.roles[*].audio`, role voice binding fields | `assets/json/nodes/role_voice_generation.json`; preview audio under configured audio preview path |
| `role_full_body_generation` | `workflows/nodes/static_asset_nodes.py` (`RoleFullBodyGenerationNode`) | `ProviderRouter.image("role")` | `MediaStore.write_first_generated_image`, role design hydration | role full-body image fields | `assets/json/nodes/role_full_body_generation.json`; `assets/images/roles/*` |
| `role_multiview_generation` | `workflows/nodes/static_asset_nodes.py` (`RoleMultiviewGenerationNode`) | `ProviderRouter.image("role")` | `MediaStore.write_first_generated_image`, role design hydration, full-body reference resolver, prop design update | role multiview image fields, role-bound prop asset fields | `assets/json/nodes/role_multiview_generation.json`; `assets/images/roles/*`; `assets/images/props/*` |
| `role_intro_video_prompt` | `workflows/nodes/static_asset_nodes.py` (`RoleIntroVideoPromptNode`) | prompt rendering from role design state | role design hydration, intro prompt cache | intro video prompt records | `assets/json/nodes/role_intro_video_prompt.json` |
| `role_intro_video_generation` | `workflows/nodes/static_asset_nodes.py` (`RoleIntroVideoGenerationNode`) | `ProviderRouter.video("role"|"shot")` | `MediaStore.write_generated_video`, multiview reference resolver | role intro video fields | `assets/json/nodes/role_intro_video_generation.json`; `assets/videos/roles/*` |
| `prop_extract` | `workflows/nodes/static_asset_nodes.py` | `ProviderRouter.text("prop")`, `AssetService.prop_extract` | Script content helpers, role-bound prop state, `PropDesignRepository` | `state.budget.used_text_calls` | `assets/json/nodes/prop_extract.json`; per-prop `assets/json/props/{prop_id}.json` |
| `prop_design` | `workflows/nodes/static_asset_nodes.py` | `ProviderRouter.text("prop")`, `AssetService.prop_design` | `PropDesignRepository`; script content helpers | `state.props`, `state.metadata.prop_design_*`, `state.budget.used_text_calls` | `assets/json/nodes/prop_design.json`; per-prop `assets/json/props/{prop_id}.json` |
| `prop_generation` | `workflows/nodes/static_asset_nodes.py` | `ProviderRouter.image("prop")` | `MediaStore.write_first_generated_image`, prop design update helpers, reference resolver candidate | `state.props[*].asset_*`, provider/model/request fields | `assets/json/nodes/prop_generation.json`; `assets/images/props/*` |
| `layout_extract` | `workflows/nodes/static_asset_nodes.py` | `ProviderRouter.text("layout")`, `AssetService.layout_extract` | Script content helpers | `state.budget.used_text_calls` | `assets/json/nodes/layout_extract.json` |
| `layout_design` | `workflows/nodes/static_asset_nodes.py` | `ProviderRouter.text("layout")`, `AssetService.layout_design` | Script content helpers, `layout_extract` node output | `state.layouts`, `state.budget.used_text_calls` | `assets/json/nodes/layout_design.json` |
| `layout_dedupe_review` | `workflows/nodes/static_asset_nodes.py` | `ProviderRouter.text("layout")`, `AssetService.layout_dedupe_review` | none beyond node output repository | `state.layouts`, `state.budget.used_text_calls` | `assets/json/nodes/layout_dedupe_review.json` |
| `layout_image_generation` | `workflows/nodes/static_asset_nodes.py` | `ProviderRouter.image("layout")` | `MediaStore.write_first_generated_image` | `state.layouts[*].asset_*`, provider/model/request fields | `assets/json/nodes/layout_image_generation.json`; `assets/images/layouts/*` |
| `bgm_design` | `workflows/nodes/bgm_nodes.py` | `ProviderRouter.text("bgm_plan")`, `AssetService.bgm_design` | Script content helpers | `state.bgms`, `state.budget.used_text_calls` | `assets/json/nodes/bgm_design.json` |
| `bgm_generation` | `workflows/nodes/bgm_nodes.py` | `ProviderRouter.music("bgm")` | `MediaStore.write_generated_music`, `ProjectLayout.music_asset_path` | `state.bgms[*].asset_*`, duration/lyrics/provider/model/request fields | `assets/json/nodes/bgm_generation.json`; `assets/audios/bgms/*` |

## Shared Helper Extraction Candidates

| Helper group | Current dependency | Proposed owner | Notes |
| --- | --- | --- | --- |
| Episode selection and expected keys | `PregenWorkflow._expected_episode_keys`, `_active_episode_keys_in_order` | `workflows/shared.py` or a small workflow helper | Needed by pregen, generation, and editing. Keep node ordering stable. |
| Script content loading | `_load_script_contents`, `_episode_stories`, `_novel_full_contents` | `repositories/script_content_repo.py` | `ScriptContentRepository` now owns path and content JSON compatibility for script nodes; shared wrappers remain in `PregenWorkflow`. |
| Role design files | `_role_design_json_path`, `_save_role_design_item`, `_load_role_design_item*` | `repositories/role_design_repo.py` | Must preserve per-role JSON format and fallback to legacy `nodes/role_design.json`. |
| Role design merge/hydration | `_select_role_design_item`, `_merge_role_extract_into_design`, `_ordered_role_design_items`, `_apply_role_design_item`, `_hydrate_roles_from_design_files` | `services/role_design_merge.py` | Highest risk in `run pregen --only role_design --episodes ...`. |
| Prop design files/references | `_save_prop_design_record`, `_load_prop_design_content`, `_prop_reference_refs`, `_role_bound_prop_reference_refs` | `repositories/prop_design_repo.py`, `services/asset_reference_resolver.py` | Must preserve variant ordering and role-bound reference ordering. |
| Media writes | `_write_generated_*` | `services/media_store.py` | Already delegated to `MediaStore`; wrappers remain for generation/editing delegate compatibility. |
| Storyboard IO | `_load_storyboard_episode`, `_save_storyboard_episode`, `_iter_storyboard_episodes` | `repositories/storyboard_repo.py` plus shared workflow helper | Required before removing `PregenWorkflowDelegateMixin`. |
| Shot reference resolution | `_shot_ref_asset_refs`, `_shot_video_refs` | `services/shot_reference_service.py` | Must preserve provider reference ordering and reference mode behavior. |
| Dialogue audio | `_role_for_dialogue_line`, `_generate_shot_dialogue_audio`, role synthesis helpers | `services/dialogue_audio_service.py` | Depends on role lookup and voice provider capability differences. |
| Voice catalog/generation | `_available_speakers*`, role synthesis helpers | `services/voice_catalog.py`, `services/voice_generation_service.py` | Provider capability differences make this a later, higher-risk phase. |

## Migration Status

| Phase item | Status |
| --- | --- |
| A0 dependency inventory | Complete in this document. |
| A1 script node migration | `script_outline`, `script_novel`, and `script_novel_extract` are owned by `Script*Node` classes; `director_prep` is owned by `DirectorPrepNode` and inserted between `script_novel` and `script_novel_extract`; `PregenWorkflow` wrappers remain for compatibility. |
| A2 role node migration | `role_extract` and `role_design` are owned by `Role*Node` classes; role design JSON persistence is in `RoleDesignRepository`; merge/hydrate helpers remain as shared compatibility methods. |
| A5 BGM node migration | `bgm_design` and `bgm_generation` are owned by `BGM*Node` classes; media writing goes directly through `MediaStore`. |
| A4 static asset node migration | Registered static asset nodes are owned by `StaticAsset*Node` classes; prop design JSON persistence is in `PropDesignRepository`; old `role_appearance_design` and combined role appearance generation entry points have been removed. |
| A3 voice node migration | `voice_select` is owned by `VoiceSelectNode`; `role_voice_generation` is owned by `RoleVoiceGenerationNode`; old standalone role voice design and voice generation wrapper entry points have been removed. |
| E1 pregen node boundary smoke | `scripts/smoke/pregen_node_boundary_smoke.py` checks script node class ownership and registry order. |
| E1 project layout contract smoke | `scripts/smoke/project_layout_contract_smoke.py` checks critical output paths used by migrated repositories/helpers. |
