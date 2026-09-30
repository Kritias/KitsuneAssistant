"""Базовая подготовка: модель Vosk для первого запуска.

Веса Vosk не хранятся в git (~45 МБ). Лаунчер и сам ассистент вызывают
``ensure_vosk_model()``, чтобы после клона базовый режим сразу заработал.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = BASE_DIR / "model"

VOSK_ZIP_NAME = "vosk-model-small-ru-0.22.zip"
VOSK_URLS = (
    "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
    # Зеркало SourceForge (тот же upstream alphacephei).
    "https://downloads.sourceforge.net/project/vosk.mirror/vosk-model-small-ru-0.22.zip",
)

#: Ожидаемый размер архива ~45 МБ; меньше — обрыв.
MIN_ZIP_BYTES = 20 * 1024 * 1024


def _log(progress, message: str) -> None:
    if progress:
        progress(message)
        return
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        print(message.encode("utf-8", "backslashreplace").decode("ascii", "replace"), flush=True)


def vosk_model_ready(model_dir: str | Path | None = None) -> bool:
    """Есть ли в ``model/`` каталог с ``conf/`` (как ждёт assistant_core)."""
    root = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
    if not root.is_dir():
        return False
    if (root / "conf").is_dir():
        return True
    for child in root.iterdir():
        if child.is_dir() and (child / "conf").is_dir():
            return True
    return False


def _flatten_extracted_model(root: Path) -> None:
    """Если архив дал ``vosk-model-.../conf``, поднимает содержимое на уровень model/."""
    if (root / "conf").is_dir():
        return
    nested = [p for p in root.iterdir() if p.is_dir() and (p / "conf").is_dir()]
    if len(nested) != 1:
        return
    src = nested[0]
    for item in src.iterdir():
        dest = root / item.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        shutil.move(str(item), str(dest))
    try:
        src.rmdir()
    except OSError:
        pass


def _download_with_curl(url: str, dest: Path, progress=None) -> bool:
    curl = shutil.which("curl")
    if not curl:
        return False
    _log(progress, f"Качаю Vosk через curl: {url}")
    cmd = [
        curl, "-L", "--fail", "--retry", "5", "--retry-delay", "2",
        "--connect-timeout", "30", "-C", "-",
        "-o", str(dest), url,
    ]
    try:
        completed = subprocess.run(cmd, cwd=str(BASE_DIR))
    except Exception as exc:
        _log(progress, f"curl не запустился: {exc}")
        return False
    return completed.returncode == 0 and dest.is_file() and dest.stat().st_size >= MIN_ZIP_BYTES


def _download_with_urllib(url: str, dest: Path, progress=None) -> bool:
    _log(progress, f"Качаю Vosk: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "KitsuneAssistant/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total and done % (2 * 1024 * 1024) < len(chunk):
                    _log(progress, f"  {done // (1024 * 1024)} / {total // (1024 * 1024)} МБ")
    except Exception as exc:
        _log(progress, f"Ошибка загрузки: {exc}")
        return False
    return dest.is_file() and dest.stat().st_size >= MIN_ZIP_BYTES


def _extract_zip(zip_path: Path, model_dir: Path, progress=None) -> bool:
    _log(progress, "Распаковываю модель Vosk...")
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(model_dir)
        _flatten_extracted_model(model_dir)
    except Exception as exc:
        _log(progress, f"Не удалось распаковать: {exc}")
        return False
    return vosk_model_ready(model_dir)


def ensure_vosk_model(model_dir: str | Path | None = None, progress=None) -> bool:
    """Скачивает и распаковывает small-ru модель, если её ещё нет."""
    root = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
    root.mkdir(parents=True, exist_ok=True)
    # Не затираем чужой ReadMe.txt
    readme = root / "ReadMe.txt"

    if vosk_model_ready(root):
        _log(progress, "Модель Vosk уже на месте.")
        return True

    zip_path = root / VOSK_ZIP_NAME
    if zip_path.is_file() and zip_path.stat().st_size < MIN_ZIP_BYTES:
        try:
            zip_path.unlink()
        except OSError:
            pass

    downloaded = False
    if zip_path.is_file() and zip_path.stat().st_size >= MIN_ZIP_BYTES:
        downloaded = True
    else:
        for url in VOSK_URLS:
            if _download_with_curl(url, zip_path, progress) or _download_with_urllib(url, zip_path, progress):
                downloaded = True
                break
            try:
                if zip_path.is_file():
                    zip_path.unlink()
            except OSError:
                pass

    if not downloaded:
        _log(
            progress,
            "Не удалось скачать модель Vosk. Скачай вручную и распакуй в model/: "
            + VOSK_URLS[0],
        )
        return False

    # Распаковка во временный каталог, потом перенос — чтобы битый zip не
    # оставлял полупустой model/.
    with tempfile.TemporaryDirectory(prefix="vosk_unpack_") as tmp:
        tmp_root = Path(tmp) / "model"
        tmp_root.mkdir()
        if readme.is_file():
            shutil.copy2(readme, tmp_root / "ReadMe.txt")
        if not _extract_zip(zip_path, tmp_root, progress):
            return False
        # Переносим файлы модели в целевой model/
        for item in tmp_root.iterdir():
            if item.name == "ReadMe.txt":
                continue
            dest = root / item.name
            if dest.exists():
                if dest.is_dir():
                    shutil.rmtree(dest)
                else:
                    dest.unlink()
            shutil.move(str(item), str(dest))

    try:
        zip_path.unlink()
    except OSError:
        pass

    ok = vosk_model_ready(root)
    if ok:
        _log(progress, "Модель Vosk готова.")
    else:
        _log(progress, "После распаковки модель Vosk не найдена.")
    return ok


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--help" in args or "-h" in args:
        print("Usage: python basic_setup.py [--check]")
        return 0
    if "--check" in args:
        ready = vosk_model_ready()
        print("vosk_model:", "ready" if ready else "missing")
        return 0 if ready else 1
    return 0 if ensure_vosk_model() else 1


if __name__ == "__main__":
    raise SystemExit(main())
