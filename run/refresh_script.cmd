@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROJECT="
set "PROVIDER_ARGS="
set "CINEMATIC_ADAPT=1"
set "RUN_EXTRACT=1"

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
if "%~1"=="--no-cinematic-adapt" (
  set "CINEMATIC_ADAPT="
  shift
  goto parse
)
if "%~1"=="--skip-extract" (
  set "RUN_EXTRACT="
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
echo   run\refresh_script.cmd [--config FILE] [--project ID] [--fake]
echo   run\refresh_script.cmd [--no-cinematic-adapt] [--skip-extract]
echo.
echo The chapter directory is read from project.script_chapters_dir.
echo Each file must be named chap####_chapter-title.txt.
echo.
echo Default behavior:
echo   1. Run the script_import node to import each chapter as one episode.
echo   2. Run script_cinematic_adapt and script_novel_extract.
goto end

:help_error
echo Usage: 1>&2
echo   run\refresh_script.cmd [--config FILE] [--project ID] [--fake] [--no-cinematic-adapt] [--skip-extract] 1>&2
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

call :log "config: %CONFIG%"
if not "%PROJECT%"=="" call :log "project: %PROJECT%"
call :log "python: %AUTODRAMA_PYTHON%"
call :log "script_import: true"
if not "%CINEMATIC_ADAPT%"=="" call :log "cinematic adapt: true"
if "%CINEMATIC_ADAPT%"=="" call :log "cinematic adapt: false"
if not "%RUN_EXTRACT%"=="" call :log "script_novel_extract: true"
if "%RUN_EXTRACT%"=="" call :log "script_novel_extract: false"

"%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --only script_import %PROVIDER_ARGS%
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" goto fail

if not "%CINEMATIC_ADAPT%"=="" (
  "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --until script_cinematic_adapt %PROVIDER_ARGS%
  set "EXIT_CODE=!ERRORLEVEL!"
  if not "!EXIT_CODE!"=="0" goto fail
)

if not "%RUN_EXTRACT%"=="" (
  if not "%CINEMATIC_ADAPT%"=="" (
    "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --until script_novel_extract %PROVIDER_ARGS%
  ) else (
    "%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" %PROJECT_ARGS% --only script_novel_extract %PROVIDER_ARGS%
  )
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
