@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROJECT="
set "EPISODES=1"
set "SHOTS=1-3"
set "UNTIL=dynamic_asset_solidification"
set "ONLY="
set "PROVIDER_ARGS="
set "FORCE=--force"

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
if "%~1"=="--episode" (
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
if "%~1"=="--until" (
  if "%~2"=="" goto missing_value
  set "UNTIL=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--only" (
  if "%~2"=="" goto missing_value
  set "ONLY=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--node" (
  if "%~2"=="" goto missing_value
  set "ONLY=%~2"
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
if "%~1"=="-h" goto help
if "%~1"=="--help" goto help

echo Unknown argument: %~1 1>&2
goto help_error

:missing_value
echo Missing value for argument: %~1 1>&2
goto help_error

:help
echo Usage:
echo   run\generate.cmd --project PROJECT --episode 1 --shots 2-3
echo   run\generate.cmd [--config FILE] [--project ID_OR_DIR] [--episodes LIST] [--shots LIST] [--fake] [--force]
echo.
echo What it runs:
echo   No --shots:
echo     generation shot_video_generation -^> dynamic_asset_solidification
echo.
echo   With --shots:
echo     generation shot_video_generation -^> dynamic_asset_solidification scoped to selected shots.
echo.
echo Examples:
echo   run\generate.cmd --project xcj-2 --episode 1 --shots 1-3
echo   run\generate.cmd --project xcj-2 --episode 1 --shots 2-3
echo   run\generate.cmd --project xcj-2 --episode episode_001 --shots episode_001_shot_002
echo.
echo Notes:
echo   --episode is an alias for --episodes.
echo   --only/--node is for low-level debugging and runs exactly one node.
goto end

:help_error
echo Usage: 1>&2
echo   run\generate.cmd --project PROJECT --episode 1 --shots 2-3 1>&2
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

set "ONLY_ARGS="
if not "%ONLY%"=="" set "ONLY_ARGS=--only "%ONLY%""

call :log "config: %CONFIG%"
if not "%PROJECT%"=="" call :log "project: %PROJECT%"
call :log "python: %AUTODRAMA_PYTHON%"
call :log "workflow: generation"
call :log "until: %UNTIL%"
if not "%EPISODES%"=="" call :log "episodes: %EPISODES%"
if not "%SHOTS%"=="" call :log "shots: %SHOTS%"
if not "%ONLY%"=="" call :log "only: %ONLY%"
if not "%SHOTS%"=="" if "%ONLY%"=="" call :log "mode: selected shots"
if "%SHOTS%"=="" if "%ONLY%"=="" call :log "mode: selected episodes"

"%AUTODRAMA_PYTHON%" -m autodrama.cli run generation --config "%CONFIG%" %PROJECT_ARGS% --until "%UNTIL%" %ONLY_ARGS% %EPISODE_ARGS% %SHOT_ARGS% %PROVIDER_ARGS% %FORCE%
set "EXIT_CODE=%ERRORLEVEL%"

if not "%EXIT_CODE%"=="0" goto fail
call :log "generation completed"
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
