# Pregen Nodes

Last updated: 2026-07-01

This file records the current `PREGEN_NODES` order used by the default
`run pregen` workflow.

Source of truth:

- `autodrama/src/autodrama/workflows/nodes/__init__.py`
- `autodrama/src/autodrama/workflows/pregen.py`

Default stop node:

- `clip_manifest_generation`

## Default PREGEN_NODES Order

1. `script_outline`
2. `script_novel`
3. `director_prep`
4. `design_key_vision_prompt`
5. `design_key_vision_image`
6. `script_novel_extract`
7. `clip_segment`
8. `role_extract_primary`
9. `role_extract_functional`
10. `role_extract`
11. `role_episode_key_audit`
12. `role_duplicate_audit`
13. `roleboard_prompt`
14. `roleboard_generation`
15. `prop_extract`
16. `prop_dedupe`
17. `prop_prompt`
18. `prop_image_generation`
19. `layout_extract`
20. `layout_dedupe_review`
21. `layout_prompt`
22. `layout_image_generation`
23. `clip_prompt`
24. `storyboard_prompt`
25. `storyboard_generation`
26. `storyboard_keyframe_generation`
27. `clip_manifest_generation`

## Deferred Manual-Only Pregen Nodes

These nodes remain implemented and available through `run pregen --only NODE`,
but they are not part of the default `PREGEN_NODES` chain.

1. `ambient_entity_extract`
2. `role_subject_video_generation`
3. `role_subject_element_generation`
4. `role_voice_select`
5. `bgm_design`
6. `bgm_generation`

## Legacy `--only` Aliases

These aliases are accepted for compatibility, but they are not separate nodes in
the default chain.

- `prop_design` -> `prop_prompt`
- `prop_generation` -> `prop_image_generation`
