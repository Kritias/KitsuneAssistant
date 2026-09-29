@echo off
REM ============================================================================
REM  KITSUNE // LIVING FLAME HUD  -  launcher for native Windows (cmd.exe)
REM ============================================================================
REM  Usage:
REM    run.bat               prepare .venv, install deps, start assistant
REM    run.bat --setup       only create .venv and install dependencies
REM    run.bat --foreground  start with a console window (Vosk logs / errors)
REM    run.bat --reinstall   reinstall dependencies
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

:parse
if "%~1"=="" goto endparse
if /i "%~1"=="--setup"       ( set "SETUP_ONLY=1" & shift & goto parse )
if /i "%~1"=="--install"     ( set "SETUP_ONLY=1" & shift & goto parse )
if /i "%~1"=="--foreground"  ( set "FOREGROUND=1" & shift & goto parse )
if /i "%~1"=="--reinstall"   ( set "REINSTALL=1" & shift & goto parse )
if /i "%~1"=="--help"        goto help
if /i "%~1"=="-h"            goto help
echo Unknown argument: %~1
echo Run "run.bat --help" to see available flags.
exit /b 1

:help
echo KITSUNE // LIVING FLAME HUD - launcher
echo.
echo   run.bat               prepare .venv, install deps, start assistant
echo   run.bat --setup       only create .venv and install dependencies
echo   run.bat --foreground  start with a console window (Vosk logs / errors)
echo   run.bat --reinstall   reinstall dependencies
echo   run.bat --help        this help
exit /b 0

:endparse

REM --- 1. Find a system Python ------------------------------------------------
set "SYSTEM_PY="
where python >nul 2>nul && set "SYSTEM_PY=python"
if not defined SYSTEM_PY (
    where py >nul 2>nul && set "SYSTEM_PY=py -3"
)
if not defined SYSTEM_PY (
    echo [ERROR] Python 3.9+ not found in PATH.
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

REM --- 3b. Optional GPU dependencies for on-GPU speech recognition -------------
REM Installed only when an NVIDIA GPU is present. Without them the assistant
REM runs on Vosk (CPU), as config.json -> asr_engine = auto allows.
set "GPU_REQ_FILE=%~dp0requirements-gpu.txt"
set "GPU_LOCK_FILE=%VENV_DIR%\.requirements-gpu.lock"

set "HAS_NVIDIA=0"
where nvidia-smi >nul 2>nul && set "HAS_NVIDIA=1"
if exist "%SystemRoot%\System32\nvidia-smi.exe" set "HAS_NVIDIA=1"
if exist "%ProgramFiles%\NVIDIA Corporation\NVSMI\nvidia-smi.exe" set "HAS_NVIDIA=1"

if "%HAS_NVIDIA%"=="1" if exist "%GPU_REQ_FILE%" (
    set "GPU_NEED=0"
    if "%REINSTALL%"=="1" set "GPU_NEED=1"
    if not exist "%GPU_LOCK_FILE%" set "GPU_NEED=1"
    if exist "%GPU_LOCK_FILE%" (
        fc /b "%GPU_REQ_FILE%" "%GPU_LOCK_FILE%" >nul 2>nul
        if errorlevel 1 set "GPU_NEED=1"
    )
    if "!GPU_NEED!"=="1" (
        echo [2b/3] NVIDIA detected - installing GPU recognition deps ^(~0.5 GB^)...
        "%VENV_PY%" -m pip install -r "%GPU_REQ_FILE%"
        if errorlevel 1 (
            echo [WARN] GPU dependencies failed to install - will run on Vosk ^(CPU^).
        ) else (
            copy /y "%GPU_REQ_FILE%" "%GPU_LOCK_FILE%" >nul
            echo        GPU recognition is ready.
        )
    ) else (
        echo [2b/3] GPU recognition dependencies are up to date.
    )
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
