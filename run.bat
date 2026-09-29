@echo off
REM ============================================================================
REM  KITSUNE // LIVING FLAME HUD  -  launcher for native Windows (cmd.exe)
REM ============================================================================
REM  Usage:
REM    run.bat               prepare .venv, install deps, start assistant
REM    run.bat --setup       only create .venv and install dependencies
REM    run.bat --foreground  start with a console window (Vosk logs / errors)
REM    run.bat --reinstall   reinstall dependencies
REM    run.bat --no-tts      skip local speech synthesis (network voice only)
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
set "SKIP_TTS=0"

:parse
if "%~1"=="" goto endparse
if /i "%~1"=="--setup"       ( set "SETUP_ONLY=1" & shift & goto parse )
if /i "%~1"=="--install"     ( set "SETUP_ONLY=1" & shift & goto parse )
if /i "%~1"=="--foreground"  ( set "FOREGROUND=1" & shift & goto parse )
if /i "%~1"=="--reinstall"   ( set "REINSTALL=1" & shift & goto parse )
if /i "%~1"=="--no-tts"      ( set "SKIP_TTS=1" & shift & goto parse )
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
echo   run.bat --no-tts      skip local speech synthesis (network voice only)
echo   run.bat --help        this help
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

REM --- 3c. Local speech synthesis: light path (kokoro, ONNX) -----------------
REM Installed on every machine: it runs on CPU and makes speech work offline.
REM A failure here is NOT fatal - it only means the assistant keeps using
REM edge-tts, then SAPI5, exactly as before local synthesis existed.
set "TTS_REQ_FILE=%~dp0requirements-tts.txt"
set "TTS_LOCK_FILE=%VENV_DIR%\.requirements-tts.lock"

set "TTS_NEED=0"
if not "%SKIP_TTS%"=="1" if exist "%TTS_REQ_FILE%" (
    if "%REINSTALL%"=="1" set "TTS_NEED=1"
    if not exist "%TTS_LOCK_FILE%" set "TTS_NEED=1"
    if exist "%TTS_LOCK_FILE%" (
        fc /b "%TTS_REQ_FILE%" "%TTS_LOCK_FILE%" >nul 2>nul
        if errorlevel 1 set "TTS_NEED=1"
    )
)

if "%SKIP_TTS%"=="1" (
    echo [2c/3] Local speech synthesis skipped by --no-tts.
) else if "!TTS_NEED!"=="1" (
    echo [2c/3] Installing local speech synthesis ^(kokoro, ~0.4 GB^)...
    "%VENV_PY%" -m pip install -r "%TTS_REQ_FILE%"
    if errorlevel 1 (
        echo [WARN] Local synthesis failed to install - speech stays on edge-tts.
    ) else (
        copy /y "%TTS_REQ_FILE%" "%TTS_LOCK_FILE%" >nul
        echo        Local speech synthesis is ready.
    )
) else (
    echo [2c/3] Local speech synthesis is up to date.
)

REM --- 3d. Local speech synthesis: best path (Silero on PyTorch + CUDA) ------
REM Only worth it with an NVIDIA card: torch plus its CUDA runtime is several
REM gigabytes, and the CPU build from PyPI is what makes
REM torch.cuda.is_available() return False even on a healthy GPU. So the check
REM is by capability, not by "is torch installed": a CPU-only torch counts as
REM needing replacement, which is what puts CUDA wheels back in place.
REM
REM The marker file stops this from re-downloading gigabytes on every launch
REM when the install succeeded but the driver turned out to be too old.
set "TTS_GPU_REQ_FILE=%~dp0requirements-tts-gpu.txt"
set "TTS_GPU_LOCK_FILE=%VENV_DIR%\.requirements-tts-gpu.lock"
set "CUDA_MARKER=%VENV_DIR%\.tts-cuda-unavailable"
REM Must match --index-url in requirements-tts-gpu.txt.
set "TORCH_CUDA_INDEX=https://download.pytorch.org/whl/cu126"
set "PYPI_INDEX=https://pypi.org/simple"

if "%REINSTALL%"=="1" del /q "%CUDA_MARKER%" >nul 2>nul

set "CUDA_READY=0"
"%VENV_PY%" -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
if not errorlevel 1 set "CUDA_READY=1"

set "TTS_GPU_NEED=0"
if not "%SKIP_TTS%"=="1" if "%HAS_NVIDIA%"=="1" if exist "%TTS_GPU_REQ_FILE%" (
    if not exist "%TTS_GPU_LOCK_FILE%" set "TTS_GPU_NEED=1"
    if exist "%TTS_GPU_LOCK_FILE%" (
        fc /b "%TTS_GPU_REQ_FILE%" "%TTS_GPU_LOCK_FILE%" >nul 2>nul
        if errorlevel 1 set "TTS_GPU_NEED=1"
    )
    if "%CUDA_READY%"=="0" if not exist "%CUDA_MARKER%" set "TTS_GPU_NEED=1"
)

if "%SKIP_TTS%"=="1" (
    echo [2d/3] Silero skipped by --no-tts.
) else if "!TTS_GPU_NEED!"=="1" (
    set "TTS_GPU_FAILED="
    echo [2d/3] NVIDIA detected - installing Silero on CUDA ^(several GB, slow^)...
    if "!CUDA_READY!"=="0" (
        REM This is the one place that needs --force-reinstall: PyPI ships a CPU
        REM build under the same version number, so without it pip would consider
        REM torch already satisfied. Only torch is forced, so editing
        REM requirements-tts-gpu.txt does not re-download a 2.6 GB wheel or churn
        REM numpy, setuptools and friends along with it.
        "%VENV_PY%" -m pip install --upgrade --force-reinstall --index-url "%TORCH_CUDA_INDEX%" --extra-index-url "%PYPI_INDEX%" torch
        if errorlevel 1 set "TTS_GPU_FAILED=1"
    )
    if not defined TTS_GPU_FAILED (
        "%VENV_PY%" -m pip install -r "%TTS_GPU_REQ_FILE%"
        if errorlevel 1 set "TTS_GPU_FAILED=1"
    )
    if defined TTS_GPU_FAILED (
        echo [WARN] Silero failed to install - kokoro ^(CPU^) will speak instead.
        type nul > "%CUDA_MARKER%"
    ) else (
        copy /y "%TTS_GPU_REQ_FILE%" "%TTS_GPU_LOCK_FILE%" >nul
        "%VENV_PY%" -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul
        if errorlevel 1 (
            echo [WARN] torch is installed but CUDA is unavailable ^(old driver?^).
            echo        Speech will use kokoro on CPU. Update the NVIDIA driver and
            echo        delete "%CUDA_MARKER%" to enable Silero.
            type nul > "%CUDA_MARKER%"
        ) else (
            del /q "%CUDA_MARKER%" >nul 2>nul
            echo        Silero is ready.
        )
    )
) else if "%HAS_NVIDIA%"=="0" (
    echo [2d/3] No NVIDIA GPU - Silero not needed, kokoro speaks on CPU.
) else if "%CUDA_READY%"=="0" (
    echo [2d/3] Silero unavailable on this driver - kokoro speaks on CPU.
) else (
    echo [2d/3] Silero is up to date.
)

REM --- 3e. Kokoro's stress model ----------------------------------------------
REM Kokoro's text frontend fetches its stress model from huggingface.co not at
REM install time and not into tts_models/, but when the engine is first built,
REM and keeps it inside its own package. Without it the engine never comes up
REM and speech silently falls back to edge-tts, which needs the network, so
REM check it here and offer to fetch it now.
if "%SKIP_TTS%"=="1" goto kokoro_check_done

"%VENV_PY%" "%~dp0tts_local.py" kokoro --check
set "KOKORO_STATUS=!errorlevel!"

if "!KOKORO_STATUS!"=="0" (
    echo [2e/3] Kokoro is ready, stress model included.
    goto kokoro_check_done
)
if "!KOKORO_STATUS!"=="11" (
    echo [2e/3] Kokoro packages are missing - offline speech is unavailable.
    echo        Fix it with: run.bat --reinstall
    goto kokoro_check_done
)

echo [2e/3] Kokoro needs its stress model ^(~690 MB^) to speak offline.
echo        Without it speech stays on edge-tts, which needs the network.
set "KOKORO_ANSWER="
set /p "KOKORO_ANSWER=        Download it now? [y/N]: "
if /i not "!KOKORO_ANSWER!"=="y" goto kokoro_check_skip

echo        Downloading, this needs access to huggingface.co...
"%VENV_PY%" "%~dp0tts_local.py" kokoro
if errorlevel 1 (
    echo [WARN] Download failed - speech stays on edge-tts.
) else (
    echo        Done: kokoro will speak locally.
)
goto kokoro_check_done

:kokoro_check_skip
echo        Skipped. Later: python tts_local.py kokoro

:kokoro_check_done

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
