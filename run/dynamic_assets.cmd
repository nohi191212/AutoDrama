@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROJECT="
set "PROVIDER_ARGS="
set "PREGEN_UNTIL=role_voice_select"
set "GENERATION_UNTIL=dynamic_asset_solidification"
set "GENERATION_ONLY="
set "EPISODES="
set "SHOTS="
set "FORCE="
set "SKIP_PREGEN="

:parse
if "%~1"=="" goto run
if "%~1"=="--config" (
  if "%~2"=="" goto missing_value
  set "CONFIG=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--project" (
  if "%~2"=="" goto missing_value
  set "PROJECT=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--episodes" (
  if "%~2"=="" goto missing_value
  set "EPISODES=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--shots" (
  if "%~2"=="" goto missing_value
  set "SHOTS=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--fake" (
  set "PROVIDER_ARGS=--provider fake"
  shift
  goto parse
)
if "%~1"=="--force" (
  set "FORCE=--force"
  shift
  goto parse
)
if "%~1"=="--skip-pregen" (
  set "SKIP_PREGEN=1"
  shift
  goto parse
)
if "%~1"=="--pregen-until" (
  if "%~2"=="" goto missing_value
  set "PREGEN_UNTIL=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--generation-until" (
  if "%~2"=="" goto missing_value
  set "GENERATION_UNTIL=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--generation-only" (
  if "%~2"=="" goto missing_value
  set "GENERATION_ONLY=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--only" (
  if "%~2"=="" goto missing_value
  set "GENERATION_ONLY=%~2"
  set "SKIP_PREGEN=1"
  shift
  shift
  goto parse
)
if "%~1"=="-h" goto help
if "%~1"=="--help" goto help

echo Unknown argument: %~1 1>&2
goto help_error

:missing_value
echo Missing value for argument: %~1 1>&2
goto help_error

:help
echo Usage:
echo   run\dynamic_assets.cmd [--config FILE] [--project ID_OR_DIR] [--fake] [--force]
echo   run\dynamic_assets.cmd [--episodes episode_001,episode_003] [--skip-pregen]
echo   run\dynamic_assets.cmd --only clip_video_generation --episodes 1,3 [--shots 1-3]
echo.
echo This advances a project through:
echo   1. pregen until role_voice_select, including roleboards, 12-panel storyboard sheets, shot manifests, and role voice_type bindings
echo   2. generation from shot_dialogue_audio_generation to dynamic_asset_solidification
echo.
echo Options:
echo   --config FILE           Config file. Default: config.yaml
echo   --project ID_OR_DIR     Project id or project directory.
echo   --episodes LIST         Comma-separated episode keys for dynamic generation.
echo   --shots LIST            Comma-separated shot indexes or ids inside selected episodes.
echo   --fake                  Use fake providers for local smoke runs.
echo   --force                 Re-run workflow nodes even if already completed.
echo   --skip-pregen           Run only dynamic generation.
echo   --pregen-until NODE     Override pregen stop node. Default: role_voice_select
echo   --generation-until NODE Override generation stop node. Default: dynamic_asset_solidification
echo   --generation-only NODE  Run one dynamic generation node.
echo   --only NODE             Alias for --skip-pregen --generation-only NODE.
goto end

:help_error
echo Usage: 1>&2
echo   run\dynamic_assets.cmd [--config FILE] [--project ID_OR_DIR] [--episodes episode_001,episode_003] [--shots 1-3] [--fake] [--force] [--skip-pregen] 1>&2
exit /b 2

:run
if not exist "%CONFIG%" (
  echo Config file not found: %CONFIG% 1>&2
  exit /b 2
)

for /f "tokens=1,* delims=:" %%A in ('findstr /R /C:"^[ ][ ]*windows:" "%CONFIG%" 2^>nul') do (
  set "AUTODRAMA_PYTHON=%%B"
)

for /f "tokens=* delims= " %%A in ("%AUTODRAMA_PYTHON%") do set "AUTODRAMA_PYTHON=%%A"
set "AUTODRAMA_PYTHON=%AUTODRAMA_PYTHON:"=%"
set "AUTODRAMA_PYTHON=%AUTODRAMA_PYTHON:'=%"

if "%AUTODRAMA_PYTHON%"=="" set "AUTODRAMA_PYTHON=D:/miniforge3/envs/autodrama/python.exe"

set "PYTHONPATH=%ROOT_DIR%\autodrama\src;%PYTHONPATH%"

set "PROJECT_ARGS="
if not "%PROJECT%"=="" set "PROJECT_ARGS=--project "%PROJECT%""

set "EPISODE_ARGS="
if not "%EPISODES%"=="" set "EPISODE_ARGS=--episodes "%EPISODES%""

set "SHOT_ARGS="
if not "%SHOTS%"=="" set "SHOT_ARGS=--shots "%SHOTS%""

set "GENERATION_ONLY_ARGS="
if not "%GENERATION_ONLY%"=="" set "GENERATION_ONLY_ARGS=--only "%GENERATION_ONLY%""

call :log "config: %CONFIG%"
if not "%PROJECT%"=="" call :log "project: %PROJECT%"
call :log "python: %AUTODRAMA_PYTHON%"

if "%SKIP_PREGEN%"=="1" (
  call :log "pregen: skipped"
) else (
  call :log "pregen until: %PREGEN_UNTIL%"
  "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --until "%PREGEN_UNTIL%" %PROVIDER_ARGS% %FORCE%
  set "EXIT_CODE=!ERRORLEVEL!"
  if not "!EXIT_CODE!"=="0" goto fail
)

call :log "generation until: %GENERATION_UNTIL%"
if not "%EPISODES%"=="" call :log "episodes: %EPISODES%"
if not "%SHOTS%"=="" call :log "shots: %SHOTS%"
if not "%GENERATION_ONLY%"=="" call :log "generation only: %GENERATION_ONLY%"
"%AUTODRAMA_PYTHON%" -m autodrama.cli run generation --config "%CONFIG%" %PROJECT_ARGS% --until "%GENERATION_UNTIL%" %GENERATION_ONLY_ARGS% %EPISODE_ARGS% %SHOT_ARGS% %PROVIDER_ARGS% %FORCE%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto fail

call :log "dynamic assets completed"
popd >nul
exit /b 0

:fail
call :log "failed with exit code %EXIT_CODE%"
popd >nul
exit /b %EXIT_CODE%

:end
popd >nul
exit /b 0

:log
powershell -NoProfile -Command "Write-Host '[autodrama]' -ForegroundColor Blue -NoNewline; Write-Host ' %~1'"
exit /b 0
