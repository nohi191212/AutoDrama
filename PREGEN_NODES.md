# Pregen Nodes

Last updated: 2026-07-06

This file records the current `PREGEN_NODES` order used by the default
`run pregen` workflow.

Source of truth:

- `autodrama/src/autodrama/workflows/nodes/__init__.py`
- `autodrama/src/autodrama/workflows/pregen.py`

Default stop node:

- `clip_manifest_generation`

## Default PREGEN_NODES Order

1. `script_import`
2. `script_detail_expand`
3. `script_novel_extract`
4. `design_key_vision_prompt`
5. `design_key_vision_image`
6. `role_extract_primary`
7. `role_extract_functional`
8. `role_extract`
9. `role_episode_key_audit`
10. `role_duplicate_audit`
11. `roleboard_prompt`
12. `roleboard_generation`
13. `prop_extract`
14. `prop_dedupe`
15. `layout_extract`
16. `layout_dedupe_review`
17. `clip_segment`
18. `prop_prompt`
19. `prop_image_generation`
20. `layout_prompt`
21. `layout_image_generation`
22. `clip_prompt`
23. `storyboard_prompt`
24. `storyboard_generation`
25. `storyboard_keyframe_generation`
26. `clip_manifest_generation`

## Deferred Manual-Only Pregen Nodes

These nodes remain implemented and available through `run pregen --only NODE`,
but they are not part of the default `PREGEN_NODES` chain.

1. `script_outline`
2. `script_novel`
3. `ambient_entity_extract`
4. `role_subject_video_generation`
5. `role_subject_element_generation`
6. `role_voice_select`
7. `bgm_design`
8. `bgm_generation`

## Legacy `--only` Aliases

These aliases are accepted for compatibility, but they are not separate nodes in
the default chain.

- `prop_design` -> `prop_prompt`
- `prop_generation` -> `prop_image_generation`
