# AutoDrama

AutoDrama builds short-drama projects in two stages: pre-generation for script/static assets/BGM, then generation for per-episode dynamic shot assets.

The Python package uses a nested `src` layout. Run commands from the repository root and use `autodrama/src` as `PYTHONPATH`; the package source is under `autodrama/src/autodrama`.

Implemented scope:

1. `script_outline`
2. `script_novel`
3. `script_novel_extract`
4. `key_vision_prompt`
5. `key_vision_image_generation`
6. `role_extract_primary`
7. `role_extract_functional`
8. `role_finalize`
9. `roleboard_prompt`
10. `roleboard_image_generation`
11. `prop_extract`
12. `prop_finalize`
13. `layout_extract`
14. `layout_finalize`
15. `layout_prop_boundary_review`
16. `prop_prompt`
17. `layout_prompt`
18. `prop_image_generation`
19. `layout_image_generation`
19. `clip_segment`
20. `clip_prompt`
21. `clip_storyboard_prompt`
22. `clip_storyboard_image_generation`
23. `clip_storyboard_keyframe_generation`
24. `clip_manifest_generation`
25. `role_subject_video_generation`
26. `role_subject_element_generation`
27. `role_voice_select`
28. `bgm_design`
29. `bgm_generation`
`run pregen` currently covers script, role identity boards, prop/layout static assets, shot-level 12-panel storyboard sheets, and per-episode shot manifests. It stops at `clip_manifest_generation` by default. `role_subject_*`, `role_voice_select`, and `bgm_*` nodes remain implemented but are deferred from the default chain and can be run manually with `--only`.

Script episode content is stored as per-episode JSON files:

- `script_outline`: `assets/json/scripts/outlines/episode_XXX.json`
- `script_novel`: `assets/json/scripts/novel_full/episode_XXX.json`
- `script_novel_extract`: `assets/json/scripts/novel_extract/episode_XXX.json`

After `script_novel_extract`, `key_vision_prompt` writes the global key-vision prompt from the full script context and configured project visual prompts. `key_vision_image_generation` renders the key visual original to `assets/images/key_visions/key_vision_original.png` and records the provider image URL when one is returned. Role identity-board generation uses this key visual as a style and character-world reference image.

Each per-episode file keeps only `node_name`, `episode_key`, `content`, and at most one direct source path such as `source_novel_full_path`. The state stores the JSON path when an episode is generated, or `false` when it is not generated yet.

`role_extract_primary` reads the complete `novel_full` set and recursively extracts only primary roles. `role_extract_functional` then receives separated `primary_roles` and `functional_roles` lists and recursively extracts short-lived functional roles outside the primary set. `role_finalize` merges those outputs in stable order, performs one batch finalization audit for missing `episode_keys/source_chapters`, duplicate roles, and invalid functional roles, then writes `role_finalize.json` and per-role JSON records. `roleboard_prompt` then runs one role at a time, loading only that role's `novel_full` episodes plus the key-vision context, and writes prompt-only role identity-board prompts. Code generates `role_id` and `appearance_id`; model output must not include IDs, paths, URLs, filenames, node names, or project IDs. `roleboard_image_generation` uses the roleboard prompt and the `key_vision_image_generation` result as reference to render a reusable role identity board with front, side, back, expression, action, and costume-detail views. The generated board keeps a small edge label such as `角色：<role name> | <appearance name>` plus optional view labels, while still forbidding unrelated text, subtitles, watermarks, logos, ids, filenames, project names, dialogue, wrong names, and garbled text. No separate legacy role-visual stage remains before or after roleboard generation. The default chain then runs prop extraction, finalization, prompt writing, and image generation before moving on to layout extraction, finalization, prompt writing, and image generation; `clip_segment` starts only after both prop and layout static assets are ready. `clip_segment` reads complete episode text, all-episode summaries, and current-episode role/prop/layout indexes formatted as `索引名: 一句话介绍`; it splits each episode into suggested 8-15 second clip text units, each carrying only `text`, `role_names`, `prop_names`, and `layout_names`; episode duration is only a rhythm reference and no longer forces a computed clip count. `clip_prompt` resolves each clip's role/layout/prop assets and generates a concise shot-by-shot video prompt plus target duration. `clip_storyboard_prompt` then strictly follows the clip segment count/order and combines project constraints, episode text, episode summaries, clip segments, `clip_prompt` output, roleboard context, and generated prop/layout context into storyboard clips. Each clip contains 1-4 internal `Camera Shot` ranges, recommended 2-4, plus a P01-P12 panel plan where every storyboard panel maps to concrete content. P01-P12 are visual rhythm panels, not one panel per second. Cut boundaries between internal camera shots are marked for the storyboard image with clear red diagonal cut marks. The pregen `clip_storyboard_image_generation` renders those clips as 3840x2160, 16:9 black-and-white 12-panel storyboard sheets under `assets/images/storyboards/`; each 4x3 grid cell is 4:3, and code overlays black panel numbers, red Camera Shot numbers, and red cut marks. `clip_storyboard_keyframe_generation` then renders clean cinematic start/end keyframes from P01/P12: the first clip gets start and end, later clips only get their own end. `clip_manifest_generation` compiles the storyboard clips, per-clip 12-panel storyboard sheet paths, keyframe paths, role/prop/layout ids, dialogue, and fused `video_prompt` into `shots/{episode_key}.json` for dynamic generation. `role_subject_*` and `role_voice_select` remain available through `--only`; when run, `role_voice_select` reads the reusable `.assets/voice_catalog` manifest, references the role identity board, filters candidate voices before a `deepseek-v4-flash` top-3 text shortlist, and uses the Qwen3.5-Omni-Plus audio judge when candidate samples are complete before binding the final provider `voice_type`. Functional roles with `has_dialogue=false` do not select voice.

`prop_extract` reads all complete episode text as `novel_full_all_episodes` plus the existing prop map and maintains reusable prop introductions without image prompts. `prop_finalize` receives the extracted prop list, performs a single default finalization audit to merge duplicate props and normalize base/variant assets, then writes the final prop records. Set `nodes.prop_finalize.params.max_iterations` above 1 only when you explicitly want the older extract/finalize convergence loop. `layout_prop_boundary_review` runs after both prop and layout finalizers to remove cross-type duplicates before prompts. `prop_prompt` then turns each one-line prop intro plus configured `visual_tone` into pure text prompts; state variants such as `道具名_状态` produce reference-image change prompts. `prop_image_generation` renders base props first, then renders state variants with the base prop image as reference when the provider supports reference images.

`layout_extract` reads all complete episode text as `novel_full_all_episodes` plus existing structured `layouts` and maintains reusable scene visual assets without image prompts. Each layout is now explicit `base` or `variant`: base assets lock the reusable space structure, while variants point to `reference_layout_name` and describe only the state delta. `layout_finalize` receives structured `layouts`, performs a single default finalization audit to merge duplicate spaces and fix base/variant relationships, then writes the final layout records. Set `nodes.layout_finalize.params.max_iterations` above 1 only when you explicitly want the older extract/finalize convergence loop. `layout_prop_boundary_review` runs after both prop and layout finalizers to remove cross-type duplicates before prompts. `layout_prompt` then turns each structured layout plus configured `visual_tone` into prompt items: base layouts use `prompt_type=text_to_image`, and variants use `prompt_type=image_edit`. `layout_image_generation` renders base layouts before variants and requires variant layouts to use their referenced base scene image.

`run pregen --only clip_segment --episodes ...` reruns only selected episode clip maps. Each episode is stored independently at `assets/json/nodes/clip_segment/<episode_key>.json`, while files for other episodes are preserved. `clip_prompt` follows the same per-episode layout at `assets/json/nodes/clip_prompt/<episode_key>.json`; the old aggregate `clip_prompt.json` is read only as a migration fallback. `run pregen --only roleboard_prompt --episodes ...` and `run pregen --only roleboard_image_generation --episodes ...` rerun only roles whose `role_finalize.final_roles[].episode_keys` include the selected episode(s), preserving existing roleboard prompts/assets outside that episode unless `--force` targets them. `clip_prompt`, `clip_storyboard_prompt`, pregen `clip_storyboard_image_generation`, `clip_storyboard_keyframe_generation`, and `clip_manifest_generation` also support `--episodes` and rerun only the selected episode storyboard scripts/sheets/keyframes/manifests. `clip_storyboard_image_generation` and `clip_storyboard_keyframe_generation` additionally support `--clips 1-3`, comma/range selectors, or full clip IDs while preserving outputs outside the selection. `role_voice_select`, `prop_prompt`, `prop_image_generation`, and `layout_image_generation` also support `--episodes`; they rerun only roles/props/layouts whose `episode_keys` intersect the selected episode(s). The legacy `--only prop_design` and `--only prop_generation` names are accepted as aliases for `prop_prompt` and `prop_image_generation`.

`clip_storyboard_prompt` persists both the downstream video `video_prompt` and an image-only `storyboard_image_prompt` containing the fixed storyboard template and P01-P12 panel content. The image prompt does not embed `video_prompt`, episode/clip identifiers, or workflow explanations. `clip_storyboard_image_generation` submits that stored image prompt verbatim with its image references and saves the result; it no longer composes or safety-rewrites prompts. Legacy storyboard-prompt outputs without `storyboard_image_prompt` must be regenerated first.

Dynamic shot-level assets now live in a separate workflow:

1. `shot_dialogue_audio_generation`
2. `clip_video_generation`
3. `dynamic_asset_solidification`

`run generation` processes selected episodes in episode order. It reads existing `shots/{episode_key}.json` files, generates dialogue audio when a clip has dialogue, generates clip videos, and writes solidified dynamic asset metadata back into those clip records before moving to the next episode. Dialogue remains a structured field for TTS, but the spoken line still appears in `video_prompt`. `clip_manifest_generation` prepares the fixed `shot_video_inputs` list and model-specific `final_video_prompt`; `clip_video_generation` only submits those prepared inputs. Shot video generation uses `clip_start_frame`, `clip_end_frame`, then the clip-level 12-panel storyboard sheet before roleboard image(s), layout image(s), and prop image(s). First clip start/end both come from itself; later clip starts from the previous clip end frame and immediately hard cuts to current P01. It must not pass Kling subject element refs as the character input. It reads `generation_checklist.json` when present and supports `--episodes` to target specific episodes.

The only remaining `clip_storyboard_image_generation` node belongs to pregen. It renders the 12-panel black-and-white storyboard sheet and uses the internal image binding `nodes.clip_storyboard_image_generation`. Generation no longer has same-name storyboard or standalone image-reference nodes. Shot video generation prioritizes start frame, end frame, and pregen clip-level storyboard sheet as the first three static image anchors. Optional roleboard/layout/prop refs are kept after those anchors when provider limits allow. Optional audio refs are added only when the video provider supports them. The key visual remains an optional style reference when extra image slots are available, but it is not a default shot-video anchor.

## Provider routing

The default production routing in `config.yaml` is:

- `text.bgm_plan: aliyun` for `bgm_design`.
- `music.bgm: minimax` for `bgm_generation` with MiniMax `music-2.6`.
- `audio.speech: volcengine` for role TTS.
- `image.key_vision`: `toapi` for the global key visual original.
- `image.role`: `toapi` for `roleboard_image_generation`. The routing capability name is still `role` for compatibility, but provider model/size options are keyed by `roleboard`. Roleboard prompt templates are selected from `prompts/roleboard_prompt/` by the bound image provider/model, and `nodes.roleboard_image_generation.params.roleboard_image_generation_concurrency` controls concurrent roleboard image generation.
- `image.storyboard`, `image.prop`, and `image.layout`: `toapi` for GPT-Image-2 image generation. Key visuals, role identity boards, 12-panel storyboard sheets, props, and layouts are saved both as local image files and as the provider returned image URL when available. The pregen storyboard sheet image uses the `nodes.clip_storyboard_image_generation` binding.
- `video.shot: volcengine` for shot videos. In normal reference modes, the video workflow prioritizes `clip_start_frame`, `clip_end_frame`, and the current clip-level storyboard sheet before roleboard/layout/prop references. The storyboard sheet controls current-clip composition/action/camera/order; the keyframes control first/last frame continuity. Key visual and prop images are optional extra references only when additional image slots are configured. Non-first clips start from the previous clip end frame and immediately hard cut to current P01. Role/dialogue audio anchors are passed only when the selected video provider supports audio references. When generated images are reused as references elsewhere, the workflow passes the saved network URL first and falls back to the local file only when no URL is available.

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
- `project.episode_count` and `project.episode_duration_seconds`. `project.episode_count` is an initialization/fallback value; normal `script_outline` output decides the actual episode count through `episode_outlines`.
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

Run pre-generation only. This creates/resumes the project and runs through `clip_manifest_generation`:

```powershell
run\start.cmd --config config.yaml
run\start.cmd --config config.yaml --project <project_id>
```

Run dynamic generation only. This starts at `shot_dialogue_audio_generation` and runs through `dynamic_asset_solidification`:

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
run\dynamic_assets.cmd --config config.yaml --project <project_id> --pregen-until clip_manifest_generation --generation-until clip_video_generation --episodes 1
```

Run one dynamic generation node through `dynamic_assets.cmd`:

```powershell
run\dynamic_assets.cmd --config config.yaml --project <project_id> --generation-only shot_dialogue_audio_generation --episodes 1 --skip-pregen
run\dynamic_assets.cmd --config config.yaml --project <project_id> --generation-only clip_video_generation --episodes 1 --skip-pregen
run\dynamic_assets.cmd --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1-3
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

`--force-samples` now builds only the `normal` sample by default. Add `--sample-emotion all` when you need the full `normal/angry/sad/happy/low` set, or repeat `--sample-emotion <name>` for a custom subset. `--force-profiles` asks the configured Omni/audio judge to regenerate a natural-language listening sketch for each selected voice under `.assets/voice_catalog/<provider>/<model>/profiles/<voice_type>.json` and also updates the manifest; `--miss-profiles` builds only missing or stale manifest profiles and skips the judge for existing matching profiles. Both profile modes call the judge with concurrency 5. Use `--judge-provider <name>` only when you need to override that configured judge. Use `--voice-type <voice_type>` or `--limit N` when you want to build a small catalog slice before processing the full provider list. Project-level `role_voice_select` filters candidates to Chinese, same-gender, Doubao TTS 2.0 voices for Volcengine catalogs, assigns short internal `candidate_id` values, and uses one `deepseek-v4-flash` text call with thinking disabled to shortlist top 3 for each role. Roles run concurrently and each completed role is saved immediately; complete candidate sample sets are then passed to `routing.judge.role_voice_select` for Qwen3.5-Omni-Plus audio final selection by default.

Stop a workflow at a node:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --until roleboard_image_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --until clip_storyboard_image_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --until clip_storyboard_keyframe_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --until clip_manifest_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --until clip_video_generation --episodes 1
```

Run exactly one node:

```powershell
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only roleboard_prompt
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only roleboard_image_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only clip_storyboard_prompt --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only clip_storyboard_image_generation --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only clip_storyboard_image_generation --episodes 1 --clips 1-3 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only clip_storyboard_keyframe_generation --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only clip_manifest_generation --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only role_voice_select
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only prop_prompt --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only prop_image_generation --episodes 1 --force
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run pregen --config config.yaml --project <project_id> --only bgm_generation
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --only shot_dialogue_audio_generation --episodes 1 --shots 1-3
D:/miniforge3/envs/autodrama/python.exe -m autodrama.cli run generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1-3
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

For pregen, `--episodes` is supported only with `--only clip_segment`, `--only roleboard_prompt`, `--only roleboard_image_generation`, `--only clip_prompt`, `--only clip_storyboard_prompt`, `--only clip_storyboard_image_generation`, `--only clip_storyboard_keyframe_generation`, `--only clip_manifest_generation`, `--only role_voice_select`, `--only prop_prompt`, `--only prop_image_generation`, and `--only layout_image_generation`. Prop/layout extract and dedupe nodes remain project-level when run with `--only`. `prop_design` and `prop_generation` are accepted as legacy aliases for `prop_prompt` and `prop_image_generation`. For generation, `--episodes` selects the dynamic episodes to process.

`--shots` accepts shot indexes, ranges, and shot ids inside selected episodes:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1,3
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes episode_001 --shots shot_003
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes episode_001 --shots episode_001_shot_1
```

`--shots` can only be used with generation nodes that support shot selection, such as `shot_dialogue_audio_generation`, `clip_video_generation`, and `dynamic_asset_solidification`.

### 5. Common resume and rerun cases

Resume generation from the checklist or current project state:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id>
```

Force rerun a workflow or node:

```powershell
run\start.cmd --config config.yaml --project <project_id> --force
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --force
```

Regenerate only the pregen storyboard sheet for one episode:

```powershell
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_image_generation --episodes 1 --force
run\start.cmd --config config.yaml --project <project_id> --only clip_storyboard_keyframe_generation --episodes 1 --force
```

Resume or rerun shot videos for selected shots:

```powershell
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1-3
run\start.cmd --generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1-3 --force
```

Solidify dynamic asset metadata after videos are ready:

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
scripts/run_pregen.sh --config config.yaml --project <project_id> --until roleboard_image_generation
scripts/run_pregen.sh --config config.yaml --project <project_id> --until clip_storyboard_image_generation
scripts/run_pregen.sh --config config.yaml --project <project_id> --until clip_storyboard_keyframe_generation
scripts/run_pregen.sh --config config.yaml --project <project_id> --until clip_manifest_generation
scripts/run_pregen.sh --config config.yaml --project <project_id> --only bgm_generation --force
scripts/run_pregen_fake.sh --config config.yaml --project <project_id> --force
scripts/inspect_state.sh --config config.yaml
scripts/inspect_nodes.sh --config config.yaml
```

There is no Bash wrapper for `run generation`; use direct CLI for generation on Bash:

```bash
export PYTHONPATH="autodrama/src${PYTHONPATH:+:$PYTHONPATH}"
python3 -m autodrama.cli run generation --config config.yaml --project <project_id> --episodes 1-2
python3 -m autodrama.cli run generation --config config.yaml --project <project_id> --only clip_video_generation --episodes 1 --shots 1-3
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
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/roleboard_pregen_contract_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/config_roleboard_load_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/prop_episode_scoping_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/dynamic_assets_fake_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/only_node_episode_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/episode_serial_generation_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/minimax_music_payload_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedream_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/toapi_image_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/shot_selector_smoke.py
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_payload_smoke.py --config config.yaml.example
D:/miniforge3/envs/autodrama/python.exe scripts/smoke/seedance_router_smoke.py
```
