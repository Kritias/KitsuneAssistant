"""Догрузка зависимостей полного режима (Whisper / Silero / kokoro).

Базовый запуск ставит только ``requirements.txt`` и модель Vosk
(``basic_setup.py``). Пакеты полного режима ставятся по выбору движка в
настройках после включения полного режима, либо вручную: ``python full_deps.py``.
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

ASR_CPU_PACKAGES = (
    "faster-whisper==1.2.1",
    "ctranslate2==4.8.2",
)

KOKORO_IMPORTS = ("onnxruntime", "huggingface_hub", "ruaccent", "misaki", "phonemizer")


def _python() -> str:
    return sys.executable


def _log(progress, message: str) -> None:
    if progress:
        progress(message)
        return
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("utf-8", "backslashreplace").decode("ascii", "replace"), flush=True)


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


def asr_packages_ready() -> bool:
    return _can_import("faster_whisper") and _can_import("ctranslate2")


def kokoro_packages_ready() -> bool:
    return all(_can_import(name) for name in KOKORO_IMPORTS)


def silero_packages_ready() -> bool:
    return _torch_cuda_ready()


def status() -> dict:
    accent_ready = False
    try:
        import tts_local
        accent_ready = bool(tts_local.kokoro_accent_ready())
    except Exception:
        pass
    return {
        "nvidia": has_nvidia(),
        "asr_packages": asr_packages_ready(),
        "asr_gpu_runtime": _ctranslate2_cuda_ready(),
        "tts_packages": kokoro_packages_ready(),
        "tts_accent": accent_ready,
        "tts_silero_cuda": silero_packages_ready(),
        "ready": asr_packages_ready() and kokoro_packages_ready(),
    }


def is_ready() -> bool:
    """Совместимость: полный набор пакетов уже стоит."""
    return bool(status()["ready"])


def normalize_asr_engine(engine: str) -> str:
    engine = str(engine or "").lower().strip()
    if engine in ("whisper_gpu", "gpu"):
        return "whisper"
    return engine


def whisper_model_for_engine(engine: str, configured: str = "") -> str:
    """Имя модели faster-whisper для выбранного пункта меню. Пусто — это не Whisper."""
    engine = normalize_asr_engine(engine)
    configured = str(configured or "").strip() or "large-v3-turbo"
    if engine == "whisper_cpu":
        return "large-v3-turbo"
    if engine == "whisper_medium_cpu":
        return "medium"
    if engine == "whisper_small_cpu":
        return "small"
    if engine in ("auto", "whisper"):
        return configured
    return ""


def whisper_weights_ready(model_name: str) -> bool:
    """Лежат ли веса в кэше Hugging Face, без скачивания и без загрузки в ОЗУ."""
    model_name = str(model_name or "").strip()
    if not model_name or not asr_packages_ready():
        return False
    try:
        from faster_whisper.utils import download_model
        download_model(model_name, local_files_only=True)
        return True
    except Exception:
        return False


def prefetch_whisper_model(model_name: str, progress=None) -> bool:
    """Скачивает веса Whisper в кэш, не поднимая модель в память процесса."""
    model_name = str(model_name or "").strip()
    if not model_name:
        return True
    if whisper_weights_ready(model_name):
        _log(progress, f"Модель Whisper «{model_name}» уже скачана.")
        return True
    _log(progress, f"Качаю модель Whisper «{model_name}»...")
    try:
        from faster_whisper.utils import download_model
        download_model(model_name, local_files_only=False)
    except Exception as exc:
        _log(progress, f"Модель Whisper не скачалась: {exc}")
        return False
    return whisper_weights_ready(model_name)


def needs_for_asr_engine(engine: str, whisper_model: str | None = None) -> bool:
    """Нужна ли догрузка пакетов или весов Whisper под выбранный STT."""
    engine = normalize_asr_engine(engine)
    if engine in ("", "vosk"):
        return False
    if engine not in (
        "auto", "whisper", "whisper_cpu",
        "whisper_medium_cpu", "whisper_small_cpu",
    ):
        return False
    if not asr_packages_ready():
        return True
    # GPU-движок / auto на NVIDIA — ещё CUDA-библиотеки CT2.
    if engine in ("auto", "whisper") and has_nvidia() and not _ctranslate2_cuda_ready():
        return True
    model_name = whisper_model or whisper_model_for_engine(engine)
    if model_name and not whisper_weights_ready(model_name):
        return True
    return False


def needs_for_tts_engine(engine: str) -> bool:
    """Нужна ли догрузка пакетов локального TTS.

    ``auto`` / ``edge-tts`` ничего не качают: локальные движки ставятся
    только при явном выборе Silero или kokoro.
    """
    engine = str(engine or "").lower().strip()
    if engine == "silero":
        return not silero_packages_ready()
    if engine == "kokoro":
        return not kokoro_packages_ready()
    return False


def install_asr(progress=None, with_gpu: bool | None = None, whisper_model: str | None = None) -> dict:
    """Ставит faster-whisper (+ CUDA runtime при NVIDIA) и веса выбранной модели."""
    installed = False
    if with_gpu is None:
        with_gpu = has_nvidia()

    def step(message: str) -> None:
        _log(progress, message)

    if not asr_packages_ready():
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
        installed = True
    else:
        step("Пакеты Whisper уже на месте.")

    if with_gpu and REQ_GPU.is_file() and not _ctranslate2_cuda_ready():
        step("Ставлю CUDA-библиотеки для Whisper GPU...")
        if _pip(["-r", str(REQ_GPU)], progress):
            _write_lock(REQ_GPU, LOCK_GPU)
            installed = True
        else:
            step("CUDA-библиотеки не встали — Whisper останется на CPU.")
    elif with_gpu and _ctranslate2_cuda_ready():
        step("CUDA-библиотеки Whisper уже на месте.")

    ok = asr_packages_ready()
    if ok and whisper_model:
        if not prefetch_whisper_model(whisper_model, progress):
            return {
                "ok": False,
                "restart_needed": installed,
                "status": status(),
                "error": f"Модель Whisper «{whisper_model}» не скачалась",
            }
        installed = True

    return {
        "ok": ok,
        "restart_needed": installed,
        "status": status(),
        "error": "" if ok else "Пакеты Whisper не установлены",
    }


def install_kokoro(progress=None, download_models: bool = True) -> dict:
    """Ставит kokoro (ONNX) и при необходимости веса."""
    installed = False

    def step(message: str) -> None:
        _log(progress, message)

    if not kokoro_packages_ready():
        if not REQ_TTS.is_file():
            return {
                "ok": False,
                "restart_needed": False,
                "status": status(),
                "error": "Нет requirements-tts.txt",
            }
        step("Ставлю локальный синтез kokoro (~0.4 ГБ)...")
        if not _pip(["-r", str(REQ_TTS)], progress):
            return {
                "ok": False,
                "restart_needed": False,
                "status": status(),
                "error": "Не удалось установить kokoro",
            }
        _write_lock(REQ_TTS, LOCK_TTS)
        installed = True
    else:
        step("Пакеты kokoro уже на месте.")

    if download_models:
        try:
            import tts_local
        except Exception as exc:
            step(f"tts_local недоступен: {exc}")
        else:
            if not tts_local.kokoro_available()[0] or not tts_local.kokoro_accent_ready():
                step("Качаю модели kokoro...")
                if tts_local.download_kokoro(progress=lambda m: step(str(m))):
                    installed = True
                else:
                    step("Модели kokoro не скачались — пока edge-tts.")

    ok = kokoro_packages_ready()
    return {
        "ok": ok,
        "restart_needed": installed,
        "status": status(),
        "error": "" if ok else "Пакеты kokoro не установлены",
    }


def install_silero(progress=None, download_models: bool = True) -> dict:
    """Ставит Silero + torch CUDA (только при NVIDIA)."""
    installed = False

    def step(message: str) -> None:
        _log(progress, message)

    if not has_nvidia():
        return {
            "ok": False,
            "restart_needed": False,
            "status": status(),
            "error": "Silero на CUDA нужен NVIDIA GPU",
        }
    if CUDA_MARKER.is_file() and not _torch_cuda_ready():
        return {
            "ok": False,
            "restart_needed": False,
            "status": status(),
            "error": "Silero ранее недоступен на этом драйвере",
        }
    if not REQ_TTS_GPU.is_file():
        return {
            "ok": False,
            "restart_needed": False,
            "status": status(),
            "error": "Нет requirements-tts-gpu.txt",
        }

    if not silero_packages_ready():
        step("Ставлю Silero на CUDA (несколько ГБ)...")
        failed = False
        if not _can_import("torch") or not _torch_cuda_ready():
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
            try:
                CUDA_MARKER.write_text("", encoding="utf-8")
            except OSError:
                pass
            return {
                "ok": False,
                "restart_needed": False,
                "status": status(),
                "error": "Не удалось установить Silero/torch",
            }
        _write_lock(REQ_TTS_GPU, LOCK_TTS_GPU)
        installed = True
        if not _torch_cuda_ready():
            try:
                CUDA_MARKER.write_text("", encoding="utf-8")
            except OSError:
                pass
            return {
                "ok": False,
                "restart_needed": True,
                "status": status(),
                "error": "torch установлен, но CUDA недоступна",
            }
    else:
        step("Silero уже готов.")

    if download_models:
        try:
            import tts_local
        except Exception as exc:
            step(f"tts_local недоступен: {exc}")
        else:
            if not tts_local.silero_available()[0]:
                step("Качаю модель Silero...")
                if tts_local.download_silero(progress=lambda m: step(str(m))):
                    installed = True

    ok = silero_packages_ready()
    return {
        "ok": ok,
        "restart_needed": installed or ok,
        "status": status(),
        "error": "" if ok else "Silero не установлен",
    }


def install_component(name: str, progress=None, download_models: bool = True,
                      whisper_model: str | None = None) -> dict:
    """Ставит один компонент: ``asr`` / ``kokoro`` / ``silero`` / ``full``."""
    name = str(name or "").lower().strip()
    if name == "asr":
        return install_asr(progress=progress, whisper_model=whisper_model)
    if name == "kokoro":
        return install_kokoro(progress=progress, download_models=download_models)
    if name == "silero":
        return install_silero(progress=progress, download_models=download_models)
    if name == "full":
        return install_full(progress=progress, download_models=download_models)
    return {
        "ok": False,
        "restart_needed": False,
        "status": status(),
        "error": f"Неизвестный компонент: {name}",
    }


def install_full(progress=None, download_models: bool = True) -> dict:
    """Ставит все компоненты полного режима (для ``--full`` / офлайн-пресета)."""
    installed = False
    for name in ("asr", "kokoro", "silero"):
        # Silero без NVIDIA — ожидаемый отказ, не валим всю установку.
        result = install_component(name, progress=progress, download_models=download_models)
        installed = installed or bool(result.get("restart_needed"))
        if name != "silero" and not result.get("ok"):
            return result
    final = status()
    ok = bool(final["ready"])
    if ok:
        _log(progress, "Полный набор пакетов готов.")
    return {
        "ok": ok,
        "restart_needed": True if ok else installed,
        "status": final,
        "error": "" if ok else "Пакеты полного режима установлены не полностью",
    }


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--status" in args:
        info = status()
        for key, value in info.items():
            print(f"{key}: {value}")
        return 0 if info["ready"] else 1
    if "--help" in args or "-h" in args:
        print("Usage: python full_deps.py [--status] [--no-models] [asr|kokoro|silero|full]")
        return 0
    download_models = "--no-models" not in args
    positional = [a for a in args if not a.startswith("--")]
    component = positional[0] if positional else "full"
    result = install_component(component, download_models=download_models)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
