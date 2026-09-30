"""Догрузка зависимостей полного режима (Whisper / Silero / kokoro).

Базовый запуск ставит только ``requirements.txt`` (Vosk + edge-tts). Пакеты
полного режима тяжёлые (гигабайты) и ставятся позже — когда пользователь
включает полный режим в настройках, либо вручную: ``python full_deps.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
VENV_DIR = BASE_DIR / ".venv"

REQ_ASR = BASE_DIR / "requirements-asr.txt"
REQ_GPU = BASE_DIR / "requirements-gpu.txt"
REQ_TTS = BASE_DIR / "requirements-tts.txt"
REQ_TTS_GPU = BASE_DIR / "requirements-tts-gpu.txt"

LOCK_ASR = VENV_DIR / ".requirements-asr.lock"
LOCK_GPU = VENV_DIR / ".requirements-gpu.lock"
LOCK_TTS = VENV_DIR / ".requirements-tts.lock"
LOCK_TTS_GPU = VENV_DIR / ".requirements-tts-gpu.lock"
CUDA_MARKER = VENV_DIR / ".tts-cuda-unavailable"

TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu126"
PYPI_INDEX = "https://pypi.org/simple"

#: Минимальный ASR без NVIDIA-библиотек — Whisper на CPU (medium/small/turbo).
ASR_CPU_PACKAGES = (
    "faster-whisper==1.2.1",
    "ctranslate2==4.8.2",
)


def _python() -> str:
    return sys.executable


def _log(progress, message: str) -> None:
    if progress:
        progress(message)
    else:
        print(message)


def has_nvidia() -> bool:
    if shutil.which("nvidia-smi"):
        return True
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = (
        Path(system_root) / "System32" / "nvidia-smi.exe",
        Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        / "NVIDIA Corporation"
        / "NVSMI"
        / "nvidia-smi.exe",
    )
    return any(path.is_file() for path in candidates)


def _can_import(module_name: str) -> bool:
    try:
        __import__(module_name)
        return True
    except Exception:
        return False


def _torch_cuda_ready() -> bool:
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _ctranslate2_cuda_ready() -> bool:
    try:
        import ctranslate2
        return int(ctranslate2.get_cuda_device_count()) >= 1
    except Exception:
        return False


def _lock_matches(req: Path, lock: Path) -> bool:
    if not req.is_file() or not lock.is_file():
        return False
    try:
        return req.read_bytes() == lock.read_bytes()
    except OSError:
        return False


def _write_lock(req: Path, lock: Path) -> None:
    lock.parent.mkdir(parents=True, exist_ok=True)
    if req.is_file():
        shutil.copyfile(req, lock)
    else:
        lock.write_text("\n".join(ASR_CPU_PACKAGES) + "\n", encoding="utf-8")


def _pip(args: list[str], progress=None) -> bool:
    cmd = [_python(), "-m", "pip", "install", *args]
    _log(progress, "  $ " + " ".join(cmd))
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        _log(progress, f"  pip не запустился: {exc}")
        return False
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()
        for line in tail[-8:]:
            _log(progress, f"  {line}")
        return False
    return True


def status() -> dict:
    """Снимок готовности полного режима для UI и CLI."""
    asr_packages = _can_import("faster_whisper") and _can_import("ctranslate2")
    tts_packages = all(
        _can_import(name)
        for name in ("onnxruntime", "huggingface_hub", "ruaccent", "misaki", "phonemizer")
    )
    accent_ready = False
    try:
        import tts_local
        accent_ready = bool(tts_local.kokoro_accent_ready())
    except Exception:
        pass

    nvidia = has_nvidia()
    return {
        "nvidia": nvidia,
        "asr_packages": asr_packages,
        "asr_gpu_runtime": _ctranslate2_cuda_ready(),
        "tts_packages": tts_packages,
        "tts_accent": accent_ready,
        "tts_silero_cuda": _torch_cuda_ready(),
        "ready": asr_packages and tts_packages,
    }


def is_ready() -> bool:
    """Достаточно ли пакетов, чтобы включить полный режим без pip."""
    return bool(status()["ready"])


def install_full(progress=None, download_models: bool = True) -> dict:
    """Ставит пакеты полного режима и при необходимости модели.

    Возвращает ``{ok, restart_needed, status, error}``.
    """
    installed_anything = False
    error = ""

    def step(message: str) -> None:
        _log(progress, message)

    # --- ASR: Whisper на CPU всегда; CUDA-библиотеки — только при NVIDIA ----
    if not (_can_import("faster_whisper") and _can_import("ctranslate2")):
        step("Ставлю Whisper (faster-whisper)...")
        if REQ_ASR.is_file():
            ok = _pip(["-r", str(REQ_ASR)], progress)
            if ok:
                _write_lock(REQ_ASR, LOCK_ASR)
        else:
            ok = _pip(list(ASR_CPU_PACKAGES), progress)
            if ok:
                LOCK_ASR.parent.mkdir(parents=True, exist_ok=True)
                LOCK_ASR.write_text("\n".join(ASR_CPU_PACKAGES) + "\n", encoding="utf-8")
        if not ok:
            return {
                "ok": False,
                "restart_needed": False,
                "status": status(),
                "error": "Не удалось установить пакеты Whisper",
            }
        installed_anything = True
    else:
        step("Пакеты Whisper уже на месте.")

    if has_nvidia() and REQ_GPU.is_file():
        need_gpu = not _lock_matches(REQ_GPU, LOCK_GPU)
        if need_gpu:
            step("Найдена NVIDIA — ставлю CUDA-библиотеки для Whisper GPU...")
            if _pip(["-r", str(REQ_GPU)], progress):
                _write_lock(REQ_GPU, LOCK_GPU)
                installed_anything = True
            else:
                step("CUDA-библиотеки Whisper не встали — останется Whisper на CPU.")
    elif not has_nvidia():
        step("NVIDIA не найдена — Whisper будет работать на CPU.")

    # --- TTS: kokoro (CPU) ---------------------------------------------------
    if REQ_TTS.is_file() and (
        not _lock_matches(REQ_TTS, LOCK_TTS)
        or not all(_can_import(n) for n in ("onnxruntime", "ruaccent"))
    ):
        step("Ставлю локальный синтез kokoro (~0.4 ГБ)...")
        if _pip(["-r", str(REQ_TTS)], progress):
            _write_lock(REQ_TTS, LOCK_TTS)
            installed_anything = True
        else:
            error = "Не удалось установить kokoro"
            return {
                "ok": False,
                "restart_needed": installed_anything,
                "status": status(),
                "error": error,
            }
    else:
        step("Пакеты kokoro уже на месте.")

    # --- TTS: Silero + torch CUDA -------------------------------------------
    if has_nvidia() and REQ_TTS_GPU.is_file() and not CUDA_MARKER.is_file():
        cuda_ready = _torch_cuda_ready()
        need_silero = not _lock_matches(REQ_TTS_GPU, LOCK_TTS_GPU) or not cuda_ready
        if need_silero:
            step("Ставлю Silero на CUDA (несколько ГБ, долго)...")
            failed = False
            if not cuda_ready:
                failed = not _pip(
                    [
                        "--upgrade",
                        "--force-reinstall",
                        "--index-url",
                        TORCH_CUDA_INDEX,
                        "--extra-index-url",
                        PYPI_INDEX,
                        "torch",
                    ],
                    progress,
                )
            if not failed:
                failed = not _pip(["-r", str(REQ_TTS_GPU)], progress)
            if failed:
                step("Silero не встал — в полном режиме останется kokoro.")
                try:
                    CUDA_MARKER.write_text("", encoding="utf-8")
                except OSError:
                    pass
            else:
                _write_lock(REQ_TTS_GPU, LOCK_TTS_GPU)
                installed_anything = True
                if not _torch_cuda_ready():
                    step("torch есть, но CUDA недоступна (старый драйвер?) — говорит kokoro.")
                    try:
                        CUDA_MARKER.write_text("", encoding="utf-8")
                    except OSError:
                        pass
                else:
                    step("Silero готов.")
        else:
            step("Silero уже готов.")
    elif has_nvidia() and CUDA_MARKER.is_file():
        step("Silero ранее недоступен на этом драйвере — пропускаю.")
    else:
        step("Silero на CUDA не нужен без NVIDIA.")

    # --- Модели (веса) -------------------------------------------------------
    if download_models:
        try:
            import tts_local
        except Exception as exc:
            step(f"Модуль tts_local недоступен для скачивания моделей: {exc}")
        else:
            if not tts_local.kokoro_accent_ready() or not tts_local.kokoro_available()[0]:
                step("Качаю модели kokoro (нужен доступ к huggingface.co)...")
                if tts_local.download_kokoro(progress=lambda m: step(str(m))):
                    installed_anything = True
                else:
                    step("Модели kokoro не скачались — озвучка пока через edge-tts.")
            else:
                step("Модели kokoro на месте.")

            if _torch_cuda_ready() and not tts_local.silero_available()[0]:
                step("Качаю модель Silero...")
                if tts_local.download_silero(progress=lambda m: step(str(m))):
                    installed_anything = True

    final = status()
    ok = bool(final["ready"])
    if not ok and not error:
        error = "Пакеты полного режима установлены не полностью"
    if ok:
        step("Полный режим готов к запуску.")
    return {
        "ok": ok,
        "restart_needed": True if ok else installed_anything,
        "status": final,
        "error": error,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--status" in args:
        info = status()
        for key, value in info.items():
            print(f"{key}: {value}")
        return 0 if info["ready"] else 1
    if "--help" in args or "-h" in args:
        print("Usage: python full_deps.py [--status] [--no-models]")
        return 0
    result = install_full(download_models="--no-models" not in args)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
