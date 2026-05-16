@echo off
setlocal enabledelayedexpansion

set "ROOT_DIR=%~dp0.."
pushd "%ROOT_DIR%" >nul

set "CONFIG=config.yaml"
set "PROVIDER_ARGS="
set "UNTIL=role_voice_generation"
set "FORCE="

:parse
if "%~1"=="" goto run
if "%~1"=="--config" (
  set "CONFIG=%~2"
  shift
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
echo   run\start.cmd [--config FILE] [--fake] [--until NODE] [--force]
echo.
echo This is the native Windows entry point. It uses runtime.python.windows
echo from config.yaml when available, otherwise D:\miniforge3\envs\autodrama\python.exe.
goto end

:help_error
echo Usage:
echo   run\start.cmd [--config FILE] [--fake] [--until NODE] [--force] 1>&2
exit /b 2

:run
for /f "tokens=1,* delims=:" %%A in ('findstr /R /C:"^[ ][ ]*windows:" "%CONFIG%" 2^>nul') do (
  set "AUTODRAMA_PYTHON=%%B"
)

for /f "tokens=* delims= " %%A in ("%AUTODRAMA_PYTHON%") do set "AUTODRAMA_PYTHON=%%A"
set "AUTODRAMA_PYTHON=%AUTODRAMA_PYTHON:"=%"
set "AUTODRAMA_PYTHON=%AUTODRAMA_PYTHON:'=%"

if "%AUTODRAMA_PYTHON%"=="" set "AUTODRAMA_PYTHON=D:/miniforge3/envs/autodrama/python.exe"

set "PYTHONPATH=%ROOT_DIR%\autodrama\src;%PYTHONPATH%"

call :log "config: %CONFIG%"
call :log "python: %AUTODRAMA_PYTHON%"
call :log "running pregen until %UNTIL%"

"%AUTODRAMA_PYTHON%" -m autodrama.cli run pregen --config "%CONFIG%" --until "%UNTIL%" %PROVIDER_ARGS% %FORCE%
set "EXIT_CODE=%ERRORLEVEL%"

popd >nul
exit /b %EXIT_CODE%

:end
popd >nul
exit /b 0

:log
powershell -NoProfile -Command "Write-Host '[autodrama]' -ForegroundColor Blue -NoNewline; Write-Host ' %~1'"
exit /b 0
