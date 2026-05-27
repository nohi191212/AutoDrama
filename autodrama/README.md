# AutoDrama

AutoDrama builds short-drama projects in two stages: pre-generation for script/static assets/BGM, then generation for per-episode dynamic shot assets.

The Python package uses a nested `src` layout. Run commands from the repository root and use `autodrama/src` as `PYTHONPATH`; the package source is under `autodrama/src/autodrama`.

Implemented scope:

1. `script_outline`
2. `script_novel`
3. `script_novel_extract`
4. `role_extract_primary`
5. `role_extract_functional`
6. `role_extract`
7. `role_episode_key_audit`
8. `ambient_entity_extract`
9. `role_design`
10. `voice_select`
11. `role_voice_generation`
12. `role_full_body_generation`
13. `role_multiview_generation`
14. `role_intro_video_generation`
15. `prop_extract`
16. `prop_design`
17. `prop_generation`
18. `layout_extract`
19. `layout_design`
20. `layout_dedupe_review`
21. `layout_image_generation`
22. `bgm_design`
23. `bgm_generation`

`run pregen` covers script, reusable static assets, BGM design, and BGM audio generation. It stops at `bgm_generation` by default.

Script episode content is stored as per-episode JSON files:

- `script_outline`: `assets/json/scripts/outlines/episode_XXX.json`
- `script_novel`: `assets/json/scripts/novel_full/episode_XXX.json`
- `script_novel_extract`: `assets/json/scripts/novel_extract/episode_XXX.json`

Each per-episode file keeps only `node_name`, `episode_key`, `content`, and at most one direct source path such as `source_novel_full_path`. The state stores the JSON path when an episode is generated, or `false` when it is not generated yet.

`role_extract_primary` reads the complete `novel_full` set and recursively extracts only primary roles. `role_extract_functional` then receives separated `primary_roles` and `functional_roles` lists and recursively extracts short-lived functional roles outside the primary set. `role_extract` merges those outputs in stable order, with primary roles first and background/ambient entities excluded from `roles`. `role_episode_key_audit` then checks every role JSON with up to 30 concurrent role audits and only appends missing `episode_keys/source_chapters` to role extract/design/state records. `ambient_entity_extract` writes background entities to `assets/json/assets/ambient_entities.json` for scene/storyboard use without entering the role asset chain. `role_design` then runs one role at a time, loading only that role's `novel_full` episodes, and writes identity, relationships, voice needs/sample text, full-body prompt, multiview prompt, intro video prompt, and role-bound prop design into the active role state. `voice_select` reads the reusable `.assets/voice_catalog` manifest, filters candidate voices before a `deepseek-v4-flash` top-3 text shortlist, and uses the Qwen3.5-Omni-Plus audio judge when candidate samples are complete before binding the final provider `voice_type`; `role_voice_generation` then uses that `voice_type` for synthesis. `role_full_body_generation` renders a natural front-facing full-body reference, `role_multiview_generation` renders the three-view role sheet plus bound props using the full-body image as reference, and `role_intro_video_generation` renders the role intro video from the multiview sheet. Functional roles use a lighter asset policy: no voice when `has_dialogue=false`, and no intro video by default.

`prop_extract` reads the complete `novel_full` set and records global prop candidates/statuses without image prompts. Each extracted prop/status is written to its own `assets/json/props/{prop_id}.json` file. `prop_design` then runs one prop/status at a time, loading only the relevant `novel_full` episodes and updating that per-prop JSON with design content while preserving `extract_content`. `prop_generation` renders the final prop images from those saved prompts.

`layout_extract` reads the complete `novel_full` set and records reusable scene/location candidates without image prompts. `layout_design` then designs empty-scene image prompts from those extracted layouts plus the episode story context, `layout_dedupe_review` merges near-duplicate spaces, and `layout_image_generation` renders the final layout images.

`run pregen --only role_design --episodes ...` is supported for role-scoped reruns. It only regenerates roles whose `role_extract.episode_keys` include the selected episode(s), while preserving existing role designs outside that episode when `assets/json/nodes/role_design.json` exists. `voice_select`, `role_voice_generation`, `role_full_body_generation`, `role_multiview_generation`, `role_intro_video_generation`, `prop_design`, `prop_generation`, and `layout_image_generation` also support `--episodes`; they rerun only roles/props/layouts whose `episode_keys` intersect the selected episode(s). The legacy `--only prop_image_generation` name is accepted as an alias for `prop_generation`.

Dynamic shot-level assets now live in a separate workflow:

1. `storyboard_generation`
2. `ref_frame_generation`
3. `shot_video_generation`
4. `dynamic_asset_solidification`

`run generation` processes selected episodes in episode order. For each episode it writes the storyboard shot to `shots/{episode_key}.json`, generates reference frames, shot videos, and solidified dynamic asset metadata back into that shot before moving to the next episode. Completed storyboard summaries are stored in `assets/json/storyboard_history.json` and injected into later storyboard prompts so following episodes can preserve continuity. It reads `generation_checklist.json` when present and supports `--episodes` to target specific episodes.

`ref_frame_generation` performs an internal spatial-continuity planning step before each reference frame. The planner uses `routing.text.ref_frame_spatial`, normally DeepSeek `deepseek-v4-flash` with thinking disabled, to decide whether the current shot is in the same physical space as the previous shot. Same-space shots reuse the previous reference frame, and when available the previous two same-space frames, as topology anchors. If the previous shot is not the same physical space, the planner can reuse older same-space reference frames from `assets/json/spatial_ref_frame_index.json`, capped at 10 images. The shot JSON stores `physical_space_key`, `physical_space_note`, `spatial_reference_shot_ids`, `spatial_structure_summary`, and `spatial_constraints`; these fields guide the image prompt so character/object/crowd left-right and foreground-background relationships do not drift unless the script says they moved.

## Provider routing

The default production routing in `config.yaml` is:

- `text.bgm_plan: aliyun` for `bgm_design`.
- `text.ref_frame_spatial: deepseek` for reference-frame spatial continuity planning.
- `music.bgm: minimax` for `bgm_generation` with MiniMax `music-2.6`.
- `audio.speech: volcengine` for role TTS.
- `image.role`, `image.prop`, `image.layout`, and `image.ref_frame`: `toapi` for GPT-Image-2 image generation. Role full-body images, role multiview sheets, props, layouts, and shot reference frames are saved both as local image files and as the provider returned image URL.
- `video.shot: volcengine` for shot videos. The default `providers.volcengine.options.video_reference_mode: ref_frame_role_prop_previous_video` sends the saved shot reference-frame image URL, current-shot role/prop design images, role/dialogue audio anchors, and the previous generated shot video when it belongs to the same physical scene. It does not additionally send layout sheets or role intro videos. When generated images are reused as references elsewhere, the workflow passes the saved network URL first and falls back to the local file only when no URL is available. The generated video prompt still adds a Seedance-specific instruction to remove real-person portrait traits from the reference frame as much as possible while preserving anime/CG character identity, including smoother stylized skin, reduced pores/sweat/micro-vessels/skin spots, stable eye shape, hair shape, costume silhouette, accessories, motion rhythm, and spatial relationships.

Volcengine TTS should keep `instruction_mode: none` unless a provider-level instruction carrier is verified. This prevents instruction-prefix text from being synthesized as speech.

MiniMax music generation uses its own long timeout option, `providers.minimax.options.music_timeout_seconds`, because BGM generation can take longer than normal text or image calls.

## Start commands

Run commands from the repository root. The expected Windows Python is:

```powershell
D:/miniforge3/envs/autodrama/python.exe
```

`run\start.cmd` and `run\dynamic_assets.cmd` set `PYTHONPATH` automatically and read `runtime.python.windows` from `config.yaml` when present. Direct `python -m autodrama.cli ...` commands need `PYTHONPATH` set first.

### 0. Prepare config

```powershell
Copy-Item config.yaml.example config.yaml
```

Edit `config.yaml` before production runs:

- `project.id`: stable project id, recommended for resume.
- `project.title`: project title.
- `project.script_outline_file`: input story outline file, default `./inputs/story_outline.md`.
- `project.episode_count` and `project.episode_duration_seconds`.
- provider API keys via `apikeys.yaml` or environment variables.

### 1. Initialize or inspect a project

Explicitly create or rewrite the configured project state:

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config config.yaml
```

Override title, input script, or project id from CLI:

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli init --config config.yaml --title "30秒逆袭短片" --script-file inputs/story_outline.md --project-id review_demo
```

Inspect current state and generated node outputs:

```powershell
$env:PYTHONPATH="autodrama/src"
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli inspect state --config config.yaml --project <project_id>
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli inspect nodes --config config.yaml --project <project_id>
```

If `--project` is omitted, the CLI uses `project.id` from `config.yaml` or `outputs/current_project.json`.

### 2. Recommended Windows shortcuts

Run pre-generation only. This creates/resumes the project and runs through `bgm_generation`:

```powershell
run\start.cmd --config config.yaml
run\start.cmd --config config.yaml --project <project_id>
```

Run dynamic generation only. This starts at `storyboard_generation` and runs through `dynamic_asset_solidification`:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id>
run\start.cmd --workflow generation --config config.yaml --project <project_id>
```

Run pre-generation and dynamic generation in one command:

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id>
```

Run everything with fake providers for a local smoke/demo pass:

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id> --fake --force
```

Skip pre-generation and only run dynamic assets:

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id> --skip-pregen
```

Stop the combined `dynamic_assets.cmd` flow at custom nodes:

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id> --pregen-until layout_image_generation --generation-until ref_frame_generation --episodes 1
```

Run one dynamic generation node through `dynamic_assets.cmd`:

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id> --generation-only ref_frame_generation --episodes 1 --skip-pregen
run\dynamic_assets.cmd --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1-3
```

`run\dynamic_assets.cmd --only NODE` is an alias for `--skip-pregen --generation-only NODE`.

### 3. Direct CLI commands

Use direct CLI commands when you want the most explicit form:

```powershell
$env:PYTHONPATH="autodrama/src"
```

Pre-generation with configured providers:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id>
```

Pre-generation with fake providers:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --provider fake --force
```

Dynamic generation with configured providers:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id>
```

Dynamic generation with fake providers:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --provider fake --force
```

Build or inspect the reusable voice catalog manifest:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-samples --sample-emotion all
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --force-profiles
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog build --config config.yaml --provider volcengine --miss-profiles
D:/miniforge3/envs/autodrama/python.exe -m autodrama voice-catalog inspect --config config.yaml --provider volcengine
```

`--force-samples` now builds only the `normal` sample by default. Add `--sample-emotion all` when you need the full `normal/angry/sad/happy/low` set, or repeat `--sample-emotion <name>` for a custom subset. `--force-profiles` asks the configured Omni/audio judge to regenerate a natural-language listening sketch for each selected voice under `.assets/voice_catalog/<provider>/<model>/profiles/<voice_type>.json` and also updates the manifest; `--miss-profiles` builds only missing or stale manifest profiles and skips the judge for existing matching profiles. Both profile modes call the judge with concurrency 5. Use `--judge-provider <name>` only when you need to override that configured judge. Use `--voice-type <voice_type>` or `--limit N` when you want to build a small catalog slice before processing the full provider list. Project-level `voice_select` filters candidates to Chinese, same-gender, Doubao TTS 2.0 voices for Volcengine catalogs, assigns short internal `candidate_id` values, and uses one `deepseek-v4-flash` text call with thinking disabled to shortlist top 3 for each role. Roles run concurrently and each completed role is saved immediately; complete candidate sample sets are then passed to `routing.judge.voice_select` for Qwen3.5-Omni-Plus audio final selection by default.

Stop a workflow at a node:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --until layout_image_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --until ref_frame_generation --episodes 1
```

Run exactly one node:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only voice_select
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only role_voice_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only prop_design --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only prop_generation --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only bgm_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --only storyboard_generation --episodes 1
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes 1,3
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1-3
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --only dynamic_asset_solidification --episodes episode_001
```

### 4. Episode and shot selection

`--episodes` accepts comma lists, ranges, numbers, and episode keys:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1,3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --episodes episode_001,episode_003
```

For pregen, `--episodes` is supported only with `--only role_design`, `--only voice_select`, `--only role_voice_generation`, `--only role_full_body_generation`, `--only role_multiview_generation`, `--only role_intro_video_generation`, `--only prop_design`, `--only prop_generation`, and `--only layout_image_generation` (or legacy alias `--only prop_image_generation`). Other static asset nodes are still project-level. For generation, `--episodes` selects the dynamic episodes to process.

`--shots` accepts shot indexes, ranges, and shot ids inside selected episodes:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes 1 --shots 1
run\start.cmd --generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes 1 --shots 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1,3
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes episode_001 --shots shot_003
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes episode_001 --shots episode_001_shot_1
```

`--shots` can only be used with generation nodes after `storyboard_generation`, such as `ref_frame_generation`, `shot_video_generation`, and `dynamic_asset_solidification`.

### 5. Common resume and rerun cases

Resume generation from the checklist or current project state:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id>
```

Force rerun a workflow or node:

```powershell
run\start.cmd --config config.yaml --project <project_id> --force
run\start.cmd --generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes 1 --force
```

Regenerate only storyboard for one episode:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only storyboard_generation --episodes 1 --force
```

Regenerate reference frames for selected shots:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only ref_frame_generation --episodes 1 --shots 1-3 --force
```

Resume or rerun shot videos for selected shots:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1-3 --force
```

Solidify dynamic asset metadata after videos/reference frames are ready:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only dynamic_asset_solidification --episodes 1
```

Adopt an existing Seedance video task into a shot:

```powershell
D:/miniforge3/envs/autodrama/python.exe scripts/adopt_seedance_task.py --config config.yaml --project <project_id> --episode 1 --shot 1 --task-id <seedance_task_id>
```

### 6. Bash helper scripts

These scripts are useful on macOS/Linux or Git Bash. They source `scripts/env.sh`, choose Python from `runtime.python.<platform>`, and set `PYTHONPATH`.

```bash
scripts/init_project.sh --config config.yaml
scripts/run_pregen.sh --config config.yaml --project <project_id>
scripts/run_pregen.sh --config config.yaml --project <project_id> --until layout_image_generation
scripts/run_pregen.sh --config config.yaml --project <project_id> --only bgm_generation --force
scripts/run_pregen_fake.sh --config config.yaml --project <project_id> --force
scripts/inspect_state.sh --config config.yaml
scripts/inspect_nodes.sh --config config.yaml
```

There is no Bash wrapper for `run generation`; use direct CLI for generation on Bash:

```bash
export PYTHONPATH="autodrama/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m autodrama.cli run generation --config config.yaml --project <project_id> --episodes 1-2
python3 -m autodrama.cli run generation --config config.yaml --project <project_id> --only shot_video_generation --episodes 1 --shots 1-3
```

Override Python for Bash helpers:

```bash
AUTODRAMA_PYTHON=/path/to/python scripts/run_pregen.sh --config config.yaml
```

### 7. Validation commands

Do not use pytest in this repository. Use compile checks and focused smoke scripts.

```powershell
D:/miniforge3/envs/autodrama/python.exe -m compileall autodrama/src/autodrama
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/refactor_boundaries_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_iterative_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_partial_persistence_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_extract_design_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/role_episode_key_audit_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/functional_role_asset_policy_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/prop_episode_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedream_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/ref_frame_image_provider_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
```
