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
8. `role_finalize`
9. `roleboard_prompt`
10. `roleboard_generation`
11. `prop_extract`
12. `prop_dedupe`
13. `layout_extract`
14. `layout_dedupe_review`
15. `clip_segment`
16. `prop_prompt`
17. `prop_image_generation`
18. `layout_prompt`
19. `layout_image_generation`
20. `clip_prompt`
21. `storyboard_prompt`
22. `storyboard_generation`
23. `storyboard_keyframe_generation`
24. `clip_manifest_generation`


`script_import`
`script_detail_expand`
`script_novel_extract`

`role_extract_primary`
`role_extract_functional`
`role_finalize`
`roleboard_prompt`
`roleboard_image_generation`

`prop_extract`
`prop_finalize`
`prop_prompt`
`prop_image_generation`

`layout_extract`
`layout_finalize`
`layout_prompt`
`layout_image_generation`

`key_vision_prompt`
`key_vision_image_generation`

`clip_segment`
`clip_prompt`
`clip_storyboard_prompt`
`clip_storyboard_image_generation`
`clip_storyboard_keyframe_generation`
`clip_manifest_generation`

## Deferred Manual-Only Pregen Nodes

These nodes remain implemented and available through `run pregen --only NODE`,
but they are not part of the default `PREGEN_NODES` chain.

1. `script_outline`
2. `script_novel`
3. `role_extract`
4. `role_episode_key_audit`
5. `role_duplicate_audit`
6. `ambient_entity_extract`
7. `role_subject_video_generation`
8. `role_subject_element_generation`
9. `role_voice_select`
10. `bgm_design`
11. `bgm_generation`

## Legacy `--only` Aliases

These aliases are accepted for compatibility, but they are not separate nodes in
the default chain.

- `prop_design` -> `prop_prompt`
- `prop_generation` -> `prop_image_generation`
