@echo off
REM ============================================================================
REM  KITSUNE // LIVING FLAME HUD  -  launcher for native Windows (cmd.exe)
REM ============================================================================
REM  Usage:
REM    run.bat               prepare .venv, install deps, start assistant
REM    run.bat --setup       only create .venv and install dependencies
REM    run.bat --foreground  start with a console window (Vosk logs / errors)
REM    run.bat --reinstall   reinstall dependencies
REM    run.bat --full        also install full-mode deps (Whisper/Silero/kokoro)
REM    run.bat --help        this help
REM ============================================================================

setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"

set "VENV_DIR=%~dp0.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"
set "REQ_FILE=%~dp0requirements.txt"
set "LOCK_FILE=%VENV_DIR%\.requirements.lock"
set "ENTRYPOINT=%~dp0main.py"

set "SETUP_ONLY=0"
set "FOREGROUND=0"
set "REINSTALL=0"
set "INSTALL_FULL=0"

:parse
if "%~1"=="" goto endparse
if /i "%~1"=="--setup"       ( set "SETUP_ONLY=1" & shift & goto parse )
if /i "%~1"=="--install"     ( set "SETUP_ONLY=1" & shift & goto parse )
if /i "%~1"=="--foreground"  ( set "FOREGROUND=1" & shift & goto parse )
if /i "%~1"=="--reinstall"   ( set "REINSTALL=1" & shift & goto parse )
if /i "%~1"=="--full"        ( set "INSTALL_FULL=1" & shift & goto parse )
if /i "%~1"=="--no-tts"      ( shift & goto parse )
if /i "%~1"=="--help"        goto help
if /i "%~1"=="-h"            goto help
echo Unknown argument: %~1
echo Run "run.bat --help" to see available flags.
exit /b 1

:help
echo KITSUNE // LIVING FLAME HUD - launcher
echo.
echo   run.bat               prepare basic .venv, start assistant
echo   run.bat --setup       only create .venv and install basic dependencies
echo   run.bat --foreground  start with a console window (Vosk logs / errors)
echo   run.bat --reinstall   reinstall basic dependencies
echo   run.bat --full        also install full-mode deps (Whisper/Silero/kokoro)
echo   run.bat --help        this help
echo.
echo   Basic setup also downloads the Vosk model into model\.
echo   Full-mode engines download from the app when you select them.
echo   Use --full only to preseed Whisper/Silero/kokoro offline.
exit /b 0

:endparse

REM --- 1. Find a system Python ------------------------------------------------
REM Проверяем именно версию, а не только наличие: версии в requirements.txt
REM зафиксированы под колёса cp312, и на старом Python pip падает с невнятной
REM ошибкой вместо понятного «нужен Python 3.12».
set "PY_VERSION_TEST=import sys;sys.exit(0 if sys.version_info>=(3,12) else 1)"

set "SYSTEM_PY="
where python >nul 2>nul && python -c "%PY_VERSION_TEST%" >nul 2>nul && set "SYSTEM_PY=python"
if not defined SYSTEM_PY (
    where py >nul 2>nul && py -3 -c "%PY_VERSION_TEST%" >nul 2>nul && set "SYSTEM_PY=py -3"
)
if not defined SYSTEM_PY (
    echo [ERROR] Python 3.12+ not found in PATH.
    echo         Install it from https://www.python.org/downloads/ and enable
    echo         the "Add python.exe to PATH" option.
    exit /b 1
)

REM --- 2. Create the virtual environment --------------------------------------
if not exist "%VENV_PY%" (
    echo [1/3] Creating virtual environment: %VENV_DIR%
    %SYSTEM_PY% -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Failed to create the virtual environment.
        exit /b 1
    )
    set "REINSTALL=1"
)

REM --- 3. Install dependencies if requirements.txt changed -------------------
set "NEED_DEPS=0"
if "%REINSTALL%"=="1" set "NEED_DEPS=1"
if not exist "%LOCK_FILE%" set "NEED_DEPS=1"
if exist "%LOCK_FILE%" (
    fc /b "%REQ_FILE%" "%LOCK_FILE%" >nul 2>nul
    if errorlevel 1 set "NEED_DEPS=1"
)

if "%NEED_DEPS%"=="1" (
    echo [2/3] Installing dependencies, this may take a couple of minutes...
    "%VENV_PY%" -m pip install --upgrade pip
    "%VENV_PY%" -m pip install -r "%REQ_FILE%"
    if errorlevel 1 (
        echo [ERROR] pip install failed. Check your internet connection and retry.
        exit /b 1
    )
    copy /y "%REQ_FILE%" "%LOCK_FILE%" >nul
    echo      Dependencies are ready.
) else (
    echo [2/3] Virtual environment is ready, dependencies are up to date.
)

REM --- 3b. Vosk speech model (required for basic mode) -----------------------
REM Weights are gitignored (~45 MB). Without them recognition cannot start.
echo [2b/3] Checking Vosk model in model\ ...
"%VENV_PY%" "%~dp0basic_setup.py"
if errorlevel 1 (
    echo [ERROR] Vosk model is missing. Download
    echo         https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip
    echo         and unpack it into the model\ folder so that model\conf exists.
    if "%SETUP_ONLY%"=="1" exit /b 1
)

REM --- 3c. Full-mode deps (optional preseed) ---------------------------------
REM Whisper / Silero / kokoro normally download from the app when you pick them
REM after enabling Full mode. --full only preseeds everything offline.
if "%INSTALL_FULL%"=="1" (
    echo [2c/3] Installing full-mode dependencies ^(Whisper / Silero / kokoro^)...
    "%VENV_PY%" "%~dp0full_deps.py" full
    if errorlevel 1 (
        echo [WARN] Full-mode install finished with errors. Basic mode still works.
    ) else (
        echo        Full-mode dependencies are ready.
    )
) else (
    echo [2c/3] Full-mode deps skipped. Pick engines in Full mode to download them.
)

if "%SETUP_ONLY%"=="1" (
    echo [3/3] Setup complete. Start the assistant with: run.bat
    exit /b 0
)

REM --- 4. Start the assistant -------------------------------------------------
if "%FOREGROUND%"=="1" (
    echo [3/3] Starting Kitsune Assistant in this console...
    "%VENV_PY%" "%ENTRYPOINT%"
    exit /b %errorlevel%
)

echo [3/3] Starting Kitsune Assistant in the background...
start "Kitsune Assistant" /b "%VENV_PYW%" "%ENTRYPOINT%"
echo      Icons are in the system tray. Logs: run.bat --foreground
exit /b 0
