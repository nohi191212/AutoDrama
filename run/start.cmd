@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROJECT="
set "WORKFLOW=pregen"
set "PROVIDER_ARGS="
set "UNTIL=bgm_generation"
set "ONLY="
set "EPISODES="
set "SHOTS="
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
  if "%UNTIL%"=="bgm_generation" set "UNTIL=dynamic_asset_solidification"
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
if "%~1"=="--shots" (
  set "SHOTS=%~2"
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
echo   run\start.cmd [--only NODE] [--episodes 1,3] [--shots 1-3]
echo   run\start.cmd --generation [--config FILE] [--project ID_OR_DIR] [--episodes episode_001,episode_003] [--shots 1-3] [--only NODE] [--fake] [--force]
echo   --episode is accepted as an alias for --episodes.
echo   run\start.cmd --workflow pregen^|generation [options]
echo.
echo This is the native Windows entry point. It uses runtime.python.windows
echo from config.yaml when available, otherwise D:\miniforge3\envs\autodrama\python.exe.
echo.
echo Defaults:
echo   workflow: pregen
echo   bgm_generation
echo.
echo Notes:
echo   pregen writes reusable/static assets through bgm_generation.
echo   pregen role audio chain is role_design, voice_select, then role_voice_generation.
echo   generation starts with storyboard_generation, then processes selected episodes.
goto end

:help_error
echo Usage: 1>&2
echo   run\start.cmd [--config FILE] [--project ID_OR_DIR] [--fake] [--force] 1>&2
echo   run\start.cmd --generation [--config FILE] [--project ID_OR_DIR] [--episodes episode_001,episode_003] [--shots 1-3] [--only NODE] [--fake] [--force] 1>&2
echo   --episode is accepted as an alias for --episodes. 1>&2
exit /b 2

:run
if /I "%WORKFLOW%"=="pregen" goto workflow_ok
if /I "%WORKFLOW%"=="generation" goto workflow_ok
echo Unsupported workflow: %WORKFLOW% 1>&2
echo Expected pregen or generation. 1>&2
exit /b 2

:workflow_ok
if /I "%WORKFLOW%"=="generation" (
  if "%UNTIL%"=="bgm_generation" set "UNTIL=dynamic_asset_solidification"
)

for /f "tokens=1,* delims=:" %%A in ('findstr /R /C:"^[ ][ ]*windows:" "%CONFIG%" 2^>nul') do (
  set "AUTODRAMA_PYTHON=%%B"
)

for /f "tokens=* delims= " %%A in ("%AUTODRAMA_PYTHON%") do set "AUTODRAMA_PYTHON=%%A"
set "AUTODRAMA_PYTHON=%AUTODRAMA_PYTHON:"=%"
set "AUTODRAMA_PYTHON=%AUTODRAMA_PYTHON:'=%"

if "%AUTODRAMA_PYTHON%"=="" set "AUTODRAMA_PYTHON=D:/miniforge3/envs/autodrama/python.exe"

set "PYTHONPATH=%ROOT_DIR%\autodrama\src;%PYTHONPATH%"

call :log "config: %CONFIG%"
if not "%PROJECT%"=="" call :log "project: %PROJECT%"
call :log "python: %AUTODRAMA_PYTHON%"
call :log "workflow: %WORKFLOW%"
call :log "until: %UNTIL%"
if not "%EPISODES%"=="" call :log "episodes: %EPISODES%"
if not "%SHOTS%"=="" call :log "shots: %SHOTS%"
if not "%ONLY%"=="" call :log "only: %ONLY%"

set "PROJECT_ARGS="
if not "%PROJECT%"=="" set "PROJECT_ARGS=--project "%PROJECT%""

set "EPISODE_ARGS="
if not "%EPISODES%"=="" set "EPISODE_ARGS=--episodes "%EPISODES%""

set "SHOT_ARGS="
if /I "%WORKFLOW%"=="generation" if not "%SHOTS%"=="" set "SHOT_ARGS=--shots "%SHOTS%""

set "ONLY_ARGS="
if not "%ONLY%"=="" set "ONLY_ARGS=--only "%ONLY%""

"%AUTODRAMA_PYTHON%" -m autodrama.cli run %WORKFLOW% --config "%CONFIG%" %PROJECT_ARGS% --until "%UNTIL%" %ONLY_ARGS% %EPISODE_ARGS% %SHOT_ARGS% %PROVIDER_ARGS% %FORCE%
set "EXIT_CODE=%ERRORLEVEL%"

popd >nul
exit /b %EXIT_CODE%

:end
popd >nul
exit /b 0

:log
powershell -NoProfile -Command "Write-Host '[autodrama]' -ForegroundColor Blue -NoNewline; Write-Host ' %~1'"
exit /b 0
