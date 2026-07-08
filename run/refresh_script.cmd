@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROJECT="
set "SCRIPT="
set "PROVIDER_ARGS="
set "DETAIL_EXPAND=--detail-expand"
set "EXPANDED_SCRIPT_OUT="
set "RUN_EXTRACT=1"
set "RUN_STORYBOARD="
set "EPISODES="

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
if "%~1"=="--script" (
  if "%~2"=="" goto missing_value
  set "SCRIPT=%~2"
  shift
  shift
  goto parse
)
if "%~1"=="--expanded-script-out" (
  if "%~2"=="" goto missing_value
  set "EXPANDED_SCRIPT_OUT=%~2"
  shift
  shift
  goto parse
)

if "%~1"=="--fake" (
  set "PROVIDER_ARGS=--provider fake"
  shift
  goto parse
)
if "%~1"=="--provider" (
  if "%~2"=="" goto missing_value
  if "%~2"=="fake" (
    set "PROVIDER_ARGS=--provider fake"
  ) else (
    set "PROVIDER_ARGS=--provider configured"
  )
  shift
  shift
  goto parse
)
if "%~1"=="--detail-expand" (
  set "DETAIL_EXPAND=--detail-expand"
  shift
  goto parse
)
if "%~1"=="--no-detail-expand" (
  set "DETAIL_EXPAND="
  shift
  goto parse
)
if "%~1"=="--skip-extract" (
  set "RUN_EXTRACT="
  shift
  goto parse
)
if "%~1"=="--storyboard" (
  set "RUN_STORYBOARD=1"
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
if "%~1"=="-h" goto help
if "%~1"=="--help" goto help

echo Unknown argument: %~1 1>&2
goto help_error

:missing_value
echo Missing value for argument: %~1 1>&2
goto help_error

:help
echo Usage:
echo   run\refresh_script.cmd [--config FILE] [--project ID] [--script FILE]
echo   run\refresh_script.cmd [--expanded-script-out FILE]
echo   run\refresh_script.cmd [--no-detail-expand] [--skip-extract] [--storyboard] [--episodes 1]
echo   run\refresh_script.cmd [--fake]
echo.
echo Default behavior:
echo   1. Import the mature script with --preserve-assets --detail-expand.
echo   2. Refresh script_novel_extract.
echo.
echo It preserves existing roles, props, layouts, images, audio, and videos.
echo It does not run dynamic generation or clip_video_generation.
goto end

:help_error
echo Usage: 1>&2
echo   run\refresh_script.cmd [--config FILE] [--project ID] [--script FILE] [--expanded-script-out FILE] [--fake] 1>&2
exit /b 2

:run
if not exist "%CONFIG%" (
  echo Config file not found: %CONFIG% 1>&2
  exit /b 2
)

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

set "PROJECT_ARGS="
if not "%PROJECT%"=="" set "PROJECT_ARGS=--project "%PROJECT%""

set "SCRIPT_ARGS="
if not "%SCRIPT%"=="" set "SCRIPT_ARGS=--script "%SCRIPT%""

set "EXPANDED_SCRIPT_OUT_ARGS="
if not "%EXPANDED_SCRIPT_OUT%"=="" set "EXPANDED_SCRIPT_OUT_ARGS=--expanded-script-out "%EXPANDED_SCRIPT_OUT%""

set "EPISODE_ARGS="
if not "%EPISODES%"=="" set "EPISODE_ARGS=--episodes "%EPISODES%""

call :log "config: %CONFIG%"
if not "%PROJECT%"=="" call :log "project: %PROJECT%"
if not "%SCRIPT%"=="" call :log "script: %SCRIPT%"
call :log "python: %AUTODRAMA_PYTHON%"
call :log "preserve assets: true"
if not "%DETAIL_EXPAND%"=="" call :log "detail expand: true"
if "%DETAIL_EXPAND%"=="" call :log "detail expand: false"
if not "%EXPANDED_SCRIPT_OUT%"=="" call :log "expanded script out: %EXPANDED_SCRIPT_OUT%"
if not "%RUN_EXTRACT%"=="" call :log "refresh script_novel_extract: true"
if "%RUN_EXTRACT%"=="" call :log "refresh script_novel_extract: false"
if not "%RUN_STORYBOARD%"=="" call :log "storyboard refresh: true"

"%AUTODRAMA_PYTHON%" -m autodrama.cli import-script --config "%CONFIG%" %PROJECT_ARGS% %SCRIPT_ARGS% %DETAIL_EXPAND% %EXPANDED_SCRIPT_OUT_ARGS% %PROVIDER_ARGS% --preserve-assets
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto fail

if not "%RUN_EXTRACT%"=="" (
  "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --until script_novel_extract %PROVIDER_ARGS%
  set "EXIT_CODE=!ERRORLEVEL!"
  if not "!EXIT_CODE!"=="0" goto fail
)

if not "%RUN_STORYBOARD%"=="" (
  "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --only clip_storyboard_prompt %EPISODE_ARGS% %PROVIDER_ARGS%
  set "EXIT_CODE=!ERRORLEVEL!"
  if not "!EXIT_CODE!"=="0" goto fail
  "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --only clip_storyboard_image_generation %EPISODE_ARGS% %PROVIDER_ARGS%
  set "EXIT_CODE=!ERRORLEVEL!"
  if not "!EXIT_CODE!"=="0" goto fail
  "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --only clip_manifest_generation %EPISODE_ARGS% %PROVIDER_ARGS%
  set "EXIT_CODE=!ERRORLEVEL!"
  if not "!EXIT_CODE!"=="0" goto fail
)

call :log "script refresh completed"
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
