@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROJECT="
set "WORKFLOW=pregen"
set "PROVIDER_ARGS="
set "UNTIL=shot_manifest_generation"
set "UNTIL_EXPLICIT="
set "ONLY="
set "NODE_GROUP="
set "EPISODES="
set "SHOTS="
set "CLIPS="
set "ROLES="
set "FORCE="

:parse
if "%~1"=="" goto run
if "%~1"=="--config" (
  set "CONFIG=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--project" (
  set "PROJECT=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--workflow" (
  set "WORKFLOW=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--generation" (
  set "WORKFLOW=generation"
  if not defined UNTIL_EXPLICIT set "UNTIL=dynamic_asset_solidification"
  shift
  goto parse
)
if "%~1"=="--postgen" (
  set "WORKFLOW=postgen"
  if not defined UNTIL_EXPLICIT set "UNTIL=postgen_final_audit"
  shift
  goto parse
)
if "%~1"=="--pregen" (
  set "WORKFLOW=pregen"
  shift
  goto parse
)
if "%~1"=="--fake" (
  set "PROVIDER_ARGS=--provider fake"
  shift
  goto parse
)
if "%~1"=="--until" (
  set "UNTIL=%~2"
  set "UNTIL_EXPLICIT=1"
  shift
  shift
  goto parse
)
if "%~1"=="--only" (
  set "ONLY=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--node_group" (
  set "NODE_GROUP=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--node-group" (
  set "NODE_GROUP=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--node" (
  set "ONLY=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--episodes" (
  set "EPISODES=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--episode" (
  set "EPISODES=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--roles" (
  set "ROLES=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--role" (
  set "ROLES=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--shots" (
  set "SHOTS=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--clips" (
  set "CLIPS=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--clip" (
  set "CLIPS=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--force" (
  set "FORCE=--force"
  shift
  goto parse
)
if "%~1"=="-h" goto help
if "%~1"=="--help" goto help

echo Unknown argument: %~1 1>&2
goto help_error

:help
echo Usage:
echo   run\start.cmd [--config FILE] [--project ID_OR_DIR] [--fake] [--force]
echo   run\start.cmd [--only NODE] [--node_group key_vision^|key_vision_edit^|role_extract^|prop_layout_extract^|roleboard_gen^|prop_gen^|layout_gen] [--episodes 1,3] [--roles ROLE1,ROLE2] [--clips 1-3]
echo   run\start.cmd --generation [--config FILE] [--project ID_OR_DIR] [--episodes episode_001,episode_003] [--shots 1-3] [--only NODE] [--fake] [--force]
echo   run\start.cmd --postgen [--config FILE] [--project ID_OR_DIR] [--episodes episode_001,episode_003] [--shots 1-9] [--only NODE] [--fake] [--force]
echo   --episode is accepted as an alias for --episodes.
echo   --role is accepted as an alias for --roles.
echo   --clip is accepted as an alias for --clips.
echo   --node-group is accepted as an alias for --node_group.
echo   run\start.cmd --workflow pregen^|generation^|postgen [options]
echo.
echo This is the native Windows entry point. It uses runtime.python.windows
echo from config.yaml when available, otherwise D:\miniforge3\envs\autodrama\python.exe.
echo.
echo Defaults:
echo   workflow: pregen
echo   pregen stop node: shot_manifest_generation
echo   generation stop node: dynamic_asset_solidification
echo   postgen stop node: postgen_final_audit
echo.
echo Notes:
echo   pregen writes roleboards, prop/layout assets, spatial references, keyframes, and shot manifests through shot_manifest_generation.
echo   ambient entity, role subject, role voice selection, and BGM nodes are optional and can be run with --only.
echo   pregen shot chain is clip_segment, clip_to_shots, scene_multiview_plan, scene_multiview_image_generation,
echo   layout_to_background_prompt, shot_background_image_generation, shot_blocking_plan,
echo   shot_blocking_control_render, shot_keyframe_prompt, shot_keyframe_stage_generation,
echo   shot_keyframe_image_generation, then shot_manifest_generation.
echo   pregen --roles is supported with --only role_voice_select.
echo   pregen --clips is supported with --only clip_to_shots.
echo   --node_group key_vision runs script_worldview_extract, key_vision_prompt, key_vision_image_generation, and key_vision_image_audit as one group.
echo   --node_group key_vision_edit edits the current key vision with the latest project audit feedback, then audits it.
echo   --node_group role_extract runs role_extract_primary, role_extract_functional, and role_finalize as one group.
echo   --node_group prop_layout_extract runs prop_extract, prop_finalize, layout_extract, layout_finalize, and layout_prop_boundary_review as one group.
echo   --node_group roleboard_gen runs roleboard_prompt, roleboard_image_generation, and roleboard_image_audit as one group; role_extract must be complete first.
echo   --node_group prop_gen runs prop_prompt, prop_image_generation, and prop_image_audit as one group; prop_layout_extract must be complete first.
echo   --node_group layout_gen runs layout_prompt, layout_image_generation, and layout_image_audit as one group; prop_layout_extract must be complete first.
echo   generation starts with shot_dialogue_audio_generation, then shot_video_generation, shot_video_audit, and dynamic_asset_solidification.
echo   postgen audits source clips, edits with native audio, optionally aligns voices and subtitles, then audits the final video.
goto end

:help_error
echo Usage: 1>&2
echo   run\start.cmd [--config FILE] [--project ID_OR_DIR] [--fake] [--force] 1>&2
echo   run\start.cmd [--only NODE] [--node_group key_vision^|key_vision_edit^|role_extract^|prop_layout_extract^|roleboard_gen^|prop_gen^|layout_gen] [--episodes 1,3] [--roles ROLE1,ROLE2] [--clips 1-3] 1>&2
echo   run\start.cmd --generation [--config FILE] [--project ID_OR_DIR] [--episodes episode_001,episode_003] [--shots 1-3] [--only NODE] [--fake] [--force] 1>&2
echo   run\start.cmd --postgen [--config FILE] [--project ID_OR_DIR] [--episodes episode_001,episode_003] [--shots 1-9] [--only NODE] [--fake] [--force] 1>&2
echo   --episode is accepted as an alias for --episodes. 1>&2
echo   --role is accepted as an alias for --roles. 1>&2
echo   --clip is accepted as an alias for --clips. 1>&2
echo   --node-group is accepted as an alias for --node_group. 1>&2
exit /b 2

:run
if /I "%WORKFLOW%"=="pregen" goto workflow_ok
if /I "%WORKFLOW%"=="generation" goto workflow_ok
if /I "%WORKFLOW%"=="postgen" goto workflow_ok
echo Unsupported workflow: %WORKFLOW% 1>&2
echo Expected pregen, generation, or postgen. 1>&2
exit /b 2

:workflow_ok
if /I "%WORKFLOW%"=="generation" (
  if not defined UNTIL_EXPLICIT set "UNTIL=dynamic_asset_solidification"
)
if /I "%WORKFLOW%"=="postgen" (
  if not defined UNTIL_EXPLICIT set "UNTIL=postgen_final_audit"
)

set "AUTODRAMA_PYTHON="
for /f "tokens=1,* delims=:" %%A in ('findstr /R /C:"^[ ][ ]*windows:" "%CONFIG%" 2^>nul') do (
  set "AUTODRAMA_PYTHON=%%B"
)

if defined AUTODRAMA_PYTHON (
  for /f "tokens=* delims= " %%A in ("!AUTODRAMA_PYTHON!") do set "AUTODRAMA_PYTHON=%%A"
  set "AUTODRAMA_PYTHON=!AUTODRAMA_PYTHON:"=!"
  set "AUTODRAMA_PYTHON=!AUTODRAMA_PYTHON:'=!"
)

if not defined AUTODRAMA_PYTHON set "AUTODRAMA_PYTHON=D:/miniforge3/envs/autodrama/python.exe"

set "PYTHONPATH=%ROOT_DIR%\autodrama\src;%PYTHONPATH%"

call :log "config: %CONFIG%"
if not "%PROJECT%"=="" call :log "project: %PROJECT%"
call :log "python: %AUTODRAMA_PYTHON%"
call :log "workflow: %WORKFLOW%"
call :log "until: %UNTIL%"
if not "%EPISODES%"=="" call :log "episodes: %EPISODES%"
if not "%SHOTS%"=="" call :log "shots: %SHOTS%"
if not "%CLIPS%"=="" call :log "clips: %CLIPS%"
if not "%ROLES%"=="" call :log "roles: %ROLES%"
if not "%ONLY%"=="" call :log "only: %ONLY%"
if not "%NODE_GROUP%"=="" call :log "node_group: %NODE_GROUP%"

set "PROJECT_ARGS="
if not "%PROJECT%"=="" set "PROJECT_ARGS=--project "%PROJECT%""

set "EPISODE_ARGS="
if not "%EPISODES%"=="" set "EPISODE_ARGS=--episodes "%EPISODES%""

set "SHOT_ARGS="
if /I "%WORKFLOW%"=="generation" if not "%SHOTS%"=="" set "SHOT_ARGS=--shots "%SHOTS%""
if /I "%WORKFLOW%"=="postgen" if not "%SHOTS%"=="" set "SHOT_ARGS=--shots "%SHOTS%""

set "CLIP_ARGS="
if /I "%WORKFLOW%"=="pregen" if not "%CLIPS%"=="" set "CLIP_ARGS=--clips "%CLIPS%""

set "ROLE_ARGS="
if /I "%WORKFLOW%"=="pregen" if not "%ROLES%"=="" set "ROLE_ARGS=--roles "%ROLES%""

set "ONLY_ARGS="
if not "%ONLY%"=="" set "ONLY_ARGS=--only "%ONLY%""

set "NODE_GROUP_ARGS="
if not "%NODE_GROUP%"=="" set "NODE_GROUP_ARGS=--node-group "%NODE_GROUP%""

"%AUTODRAMA_PYTHON%" -m autodrama.cli run %WORKFLOW% --config "%CONFIG%" %PROJECT_ARGS% --until "%UNTIL%" %ONLY_ARGS% %NODE_GROUP_ARGS% %EPISODE_ARGS% %ROLE_ARGS% %CLIP_ARGS% %SHOT_ARGS% %PROVIDER_ARGS% %FORCE%
set "EXIT_CODE=%ERRORLEVEL%"

popd >nul
exit /b %EXIT_CODE%

:end
popd >nul
exit /b 0

:log
powershell -NoProfile -Command "Write-Host '[autodrama]' -ForegroundColor Blue -NoNewline; Write-Host ' %~1'"
exit /b 0
