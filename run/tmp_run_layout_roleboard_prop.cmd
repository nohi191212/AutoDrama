@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "START_CMD=%SCRIPT_DIR%start.cmd"
set "ROOT_DIR=%SCRIPT_DIR%.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROJECT="
set "EPISODES="
set "PROVIDER_ARGS="
set "FORCE=--force"
set "RUN_KEY_VISION=1"
set "RUN_ROLEBOARD=1"
set "RUN_PROP=1"
set "RUN_LAYOUT=1"

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
if "%~1"=="--fake" (
  set "PROVIDER_ARGS=--fake"
  shift
  goto parse
)
if "%~1"=="--force" (
  set "FORCE=--force"
  shift
  goto parse
)
if "%~1"=="--no-force" (
  set "FORCE="
  shift
  goto parse
)
if "%~1"=="--skip-key-vision" (
  set "RUN_KEY_VISION=0"
  shift
  goto parse
)
if "%~1"=="--skip-roleboard" (
  set "RUN_ROLEBOARD=0"
  shift
  goto parse
)
if "%~1"=="--skip-prop" (
  set "RUN_PROP=0"
  shift
  goto parse
)
if "%~1"=="--skip-layout" (
  set "RUN_LAYOUT=0"
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
echo   run\tmp_run_layout_roleboard_prop.cmd [--config FILE] [--project ID_OR_DIR] [--episodes LIST] [--fake] [--force^|--no-force]
echo.
echo Default behavior:
echo   Re-run key visual, roleboard, prop, and layout pregen nodes in order.
echo   --force is enabled by default.
echo.
echo Node sequence:
echo   key_vision_prompt
echo   key_vision_image_generation
echo   roleboard_prompt
echo   roleboard_image_generation
echo   prop_extract
echo   prop_prompt
echo   prop_image_generation
echo   layout_extract
echo   layout_prompt
echo   layout_image_generation
echo.
echo Options:
echo   --config FILE             Config file. Default: config.yaml
echo   --project ID_OR_DIR       Project id or project directory.
echo   --episodes LIST           Episode filter for supported nodes.
echo   --episode LIST            Alias for --episodes.
echo   --fake                    Use fake providers.
echo   --force                   Force rerun. This is the default.
echo   --no-force                Do not force rerun completed nodes.
echo   --skip-key-vision         Keep existing key visual prompt/image.
echo   --skip-roleboard          Skip roleboard_prompt and roleboard_image_generation.
echo   --skip-prop               Skip prop_extract, prop_prompt, and prop_image_generation.
echo   --skip-layout             Skip layout_extract, layout_prompt, and layout_image_generation.
echo.
echo Notes:
echo   --episodes is passed only to nodes that support episode filtering:
echo   roleboard_prompt, roleboard_image_generation, prop_prompt, prop_image_generation,
echo   and layout_image_generation. Extract/project-level prompt nodes run without --episodes.
goto end

:help_error
echo Usage: 1>&2
echo   run\tmp_run_layout_roleboard_prop.cmd [--config FILE] [--project ID_OR_DIR] [--episodes LIST] [--fake] [--force^|--no-force] 1>&2
exit /b 2

:run
if not exist "%CONFIG%" (
  echo Config file not found: %CONFIG% 1>&2
  exit /b 2
)
if not exist "%START_CMD%" (
  echo start.cmd not found: %START_CMD% 1>&2
  exit /b 2
)

set "PROJECT_ARGS="
if not "%PROJECT%"=="" set "PROJECT_ARGS=--project "%PROJECT%""

set "EPISODE_ARGS="
if not "%EPISODES%"=="" set "EPISODE_ARGS=--episodes "%EPISODES%""

echo [autodrama] config: %CONFIG%
if not "%PROJECT%"=="" echo [autodrama] project: %PROJECT%
if not "%EPISODES%"=="" echo [autodrama] episodes: %EPISODES%
if "%FORCE%"=="--force" (
  echo [autodrama] force: enabled
) else (
  echo [autodrama] force: disabled
)

if not "%RUN_KEY_VISION%"=="1" goto skip_key_vision
echo [autodrama] node: key_vision_prompt
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only key_vision_prompt %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
echo [autodrama] node: key_vision_image_generation
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only key_vision_image_generation %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
goto after_key_vision

:skip_key_vision
echo [autodrama] skip: key vision

:after_key_vision
if not "%RUN_ROLEBOARD%"=="1" goto skip_roleboard
echo [autodrama] node: roleboard_prompt
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only roleboard_prompt %EPISODE_ARGS% %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
echo [autodrama] node: roleboard_image_generation
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only roleboard_image_generation %EPISODE_ARGS% %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
goto after_roleboard

:skip_roleboard
echo [autodrama] skip: roleboard

:after_roleboard
if not "%RUN_PROP%"=="1" goto skip_prop
echo [autodrama] node: prop_extract
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only prop_extract %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
echo [autodrama] node: prop_prompt
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only prop_prompt %EPISODE_ARGS% %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
echo [autodrama] node: prop_image_generation
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only prop_image_generation %EPISODE_ARGS% %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
goto after_prop

:skip_prop
echo [autodrama] skip: prop

:after_prop
if not "%RUN_LAYOUT%"=="1" goto skip_layout
echo [autodrama] node: layout_extract
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only layout_extract %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
echo [autodrama] node: layout_prompt
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only layout_prompt %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
echo [autodrama] node: layout_image_generation
call "%START_CMD%" --config "%CONFIG%" %PROJECT_ARGS% --only layout_image_generation %EPISODE_ARGS% %PROVIDER_ARGS% %FORCE%
if errorlevel 1 goto fail
goto after_layout

:skip_layout
echo [autodrama] skip: layout

:after_layout
echo [autodrama] layout/roleboard/prop rerun completed
popd >nul
exit /b 0

:fail
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="0" set "EXIT_CODE=1"
echo [autodrama] failed with exit code %EXIT_CODE%
popd >nul
exit /b %EXIT_CODE%

:end
popd >nul
exit /b 0
