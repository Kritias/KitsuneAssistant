"""Базовая подготовка: модель Vosk для первого запуска.

Веса Vosk не хранятся в git (~45 МБ). Лаунчер и сам ассистент вызывают
``ensure_vosk_model()``, чтобы после клона базовый режим сразу заработал.

Если сайт модели отвечает 403 / требует прокси / сеть недоступна —
загрузка сразу останавливается и показывается инструкция, как скачать
архив вручную и куда его распаковать.

Загрузка только через urllib с жёстким таймаутом в Python: системный
``curl.exe`` на Windows часто зависает на DNS/прокси и игнорирует
``--max-time``, из‑за чего run.bat «висит» без сообщения.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL_DIR = BASE_DIR / "model"

VOSK_ZIP_NAME = "vosk-model-small-ru-0.22.zip"
VOSK_MODEL_FOLDER = "vosk-model-small-ru-0.22"
VOSK_URLS = (
    "https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip",
    # Зеркало SourceForge (тот же upstream alphacephei).
    "https://downloads.sourceforge.net/project/vosk.mirror/vosk-model-small-ru-0.22.zip",
)

#: Ожидаемый размер архива ~45 МБ; меньше — обрыв или HTML-страница ошибки.
MIN_ZIP_BYTES = 20 * 1024 * 1024

#: HTTP-коды, при которых повторять бессмысленно (прокси / запрет / auth).
FATAL_HTTP_CODES = frozenset({401, 403, 407, 451})

#: Быстрая проверка «сайт вообще отвечает».
PROBE_TIMEOUT_S = 8
#: Лимит на полное скачивание одного зеркала. Если за это время архив
#: не пришёл (типично при прокси/DPI) — сразу ручная инструкция, без «висения».
TRANSFER_TIMEOUT_S = 20


class DownloadFatalError(Exception):
    """Сеть/прокси явно отказали — дальше качать с других зеркал бесполезно."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _log(progress, message: str) -> None:
    if progress:
        progress(message)
        return
    stream = sys.stdout
    try:
        print(message, flush=True)
        return
    except UnicodeEncodeError:
        pass
    # Windows cp1252/cp866 ломается на кириллице в pipe — пишем replace в байты.
    text = message + "\n"
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        if hasattr(stream, "buffer"):
            stream.buffer.write(text.encode(encoding, errors="replace"))
            stream.buffer.flush()
        else:
            stream.write(text.encode(encoding, errors="replace").decode(encoding, errors="replace"))
            stream.flush()
    except Exception:
        try:
            sys.stderr.buffer.write(text.encode("ascii", errors="replace"))
            sys.stderr.buffer.flush()
        except Exception:
            pass


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


def manual_install_instructions(model_dir: str | Path | None = None) -> str:
    """Текст для консоли и чата: откуда скачать zip и куда распаковать."""
    root = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
    root_abs = root.resolve()
    urls = "\n".join(f"  {url}" for url in VOSK_URLS)
    return (
        "Автозагрузка модели Vosk недоступна (часто 403, прокси или сайт не отвечает).\n"
        "Скачай архив вручную в браузере (через свой прокси, если нужно):\n"
        f"{urls}\n"
        f"Распакуй содержимое в папку:\n"
        f"  {root_abs}\n"
        "Нужно, чтобы появился каталог:\n"
        f"  {root_abs / 'conf'}\n"
        f"Если архив дал вложенную папку «{VOSK_MODEL_FOLDER}», перенеси всё из неё\n"
        "прямо в model\\ (чтобы model\\conf существовал), затем перезапусти run.bat."
    )


def _emit_manual_instructions(progress, model_dir: str | Path | None = None) -> None:
    for line in manual_install_instructions(model_dir).splitlines():
        _log(progress, line)


def _looks_like_proxy_error(text: str) -> bool:
    lowered = (text or "").lower()
    markers = (
        "403",
        "401",
        "407",
        "proxy",
        "прокси",
        "forbidden",
        "access denied",
        "tunnel connection failed",
        "cannot connect to proxy",
        "authentication required",
        "timed out",
        "timeout",
        "name or service not known",
        "getaddrinfo failed",
        "could not resolve",
    )
    return any(marker in lowered for marker in markers)


def _cleanup_partial(dest: Path) -> None:
    try:
        if dest.is_file():
            dest.unlink()
    except OSError:
        pass


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


def _raise_from_http(code: int, reason: str = "") -> None:
    detail = f"HTTP {code}" + (f" ({reason})" if reason else "")
    if code in FATAL_HTTP_CODES:
        raise DownloadFatalError(f"Сервер отказал в загрузке: {detail}", status=code)
    raise DownloadFatalError(detail, status=code)


def _probe_url(url: str, progress=None) -> None:
    """Короткий запрос: при 403/таймауте сразу Abort, без минутного ожидания."""
    _log(progress, f"Проверяю доступность ({PROBE_TIMEOUT_S} с): {url}")
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": "KitsuneAssistant/1.0",
            "Range": "bytes=0-1023",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT_S) as resp:
            status = getattr(resp, "status", None) or resp.getcode()
            if status in FATAL_HTTP_CODES:
                _raise_from_http(int(status))
            # 200 / 206 — сайт отвечает, можно качать целиком.
            resp.read(1024)
    except DownloadFatalError:
        raise
    except urllib.error.HTTPError as exc:
        # 416 = Range не принят, но хост жив — для полной загрузки это ок.
        if exc.code == 416:
            return
        if exc.code in FATAL_HTTP_CODES:
            _raise_from_http(exc.code, str(exc.reason))
        _log(progress, f"Проба HTTP {exc.code}: {exc.reason}")
        raise DownloadFatalError(
            f"Сервер недоступен для автозагрузки: HTTP {exc.code}",
            status=exc.code,
        ) from exc
    except Exception as exc:
        reason = str(getattr(exc, "reason", exc))
        raise DownloadFatalError(
            f"Сеть не отвечает за {PROBE_TIMEOUT_S} с: {reason}",
            status=None,
        ) from exc


def _download_with_urllib(url: str, dest: Path, progress=None) -> bool:
    _log(progress, f"Качаю Vosk ({TRANSFER_TIMEOUT_S} с макс.): {url}")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "KitsuneAssistant/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TRANSFER_TIMEOUT_S) as resp, open(
            dest, "wb"
        ) as out:
            status = getattr(resp, "status", None) or resp.getcode()
            if status in FATAL_HTTP_CODES:
                _raise_from_http(int(status))
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while True:
                chunk = resp.read(256 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                done += len(chunk)
                if total and done % (2 * 1024 * 1024) < len(chunk):
                    _log(
                        progress,
                        f"  {done // (1024 * 1024)} / {total // (1024 * 1024)} МБ",
                    )
    except DownloadFatalError:
        _cleanup_partial(dest)
        raise
    except urllib.error.HTTPError as exc:
        _cleanup_partial(dest)
        if exc.code in FATAL_HTTP_CODES:
            _raise_from_http(exc.code, str(exc.reason))
        _log(progress, f"Ошибка загрузки HTTP {exc.code}: {exc.reason}")
        return False
    except Exception as exc:
        _cleanup_partial(dest)
        reason = str(getattr(exc, "reason", exc))
        if _looks_like_proxy_error(reason):
            raise DownloadFatalError(
                f"Сеть/прокси блокирует загрузку: {reason}", status=403
            ) from exc
        _log(progress, f"Ошибка загрузки: {reason}")
        return False

    if not dest.is_file() or dest.stat().st_size < MIN_ZIP_BYTES:
        size = dest.stat().st_size if dest.is_file() else 0
        _cleanup_partial(dest)
        _log(progress, f"Файл слишком маленький ({size} байт) — похоже на страницу ошибки.")
        raise DownloadFatalError("Получен неполный или ошибочный архив Vosk", status=403)
    return True


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
    """Скачивает и распаковывает small-ru модель, если её ещё нет.

    При 403/прокси/таймауте не крутит зеркала по кругу: останавливается и
    печатает инструкцию по ручной установке.
    """
    root = Path(model_dir) if model_dir else DEFAULT_MODEL_DIR
    root.mkdir(parents=True, exist_ok=True)
    readme = root / "ReadMe.txt"

    if vosk_model_ready(root):
        _log(progress, "Модель Vosk уже на месте.")
        return True

    zip_path = root / VOSK_ZIP_NAME
    if zip_path.is_file() and zip_path.stat().st_size < MIN_ZIP_BYTES:
        _cleanup_partial(zip_path)

    downloaded = False
    if zip_path.is_file() and zip_path.stat().st_size >= MIN_ZIP_BYTES:
        downloaded = True
    else:
        last_error = None
        for url in VOSK_URLS:
            try:
                _probe_url(url, progress)
                if _download_with_urllib(url, zip_path, progress):
                    downloaded = True
                    break
            except DownloadFatalError as exc:
                last_error = exc
                _log(progress, f"Зеркало недоступно: {exc}")
                _cleanup_partial(zip_path)
                # Пробуем следующее зеркало; если все отвалятся — инструкция ниже.
                continue
            _cleanup_partial(zip_path)

        if not downloaded:
            if last_error is not None:
                _log(progress, f"Автозагрузка остановлена: {last_error}")
            _emit_manual_instructions(progress, root)
            return False

    if not downloaded:
        _log(progress, "Не удалось скачать модель Vosk ни с одного зеркала.")
        _emit_manual_instructions(progress, root)
        return False

    with tempfile.TemporaryDirectory(prefix="vosk_unpack_") as tmp:
        tmp_root = Path(tmp) / "model"
        tmp_root.mkdir()
        if readme.is_file():
            shutil.copy2(readme, tmp_root / "ReadMe.txt")
        if not _extract_zip(zip_path, tmp_root, progress):
            _log(progress, "Архив скачан, но распаковка не удалась.")
            _emit_manual_instructions(progress, root)
            return False
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
        _emit_manual_instructions(progress, root)
    return ok


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--help" in args or "-h" in args:
        print("Usage: python basic_setup.py [--check | --manual]")
        return 0
    if "--manual" in args:
        print(manual_install_instructions())
        return 1
    if "--check" in args:
        ready = vosk_model_ready()
        print("vosk_model:", "ready" if ready else "missing")
        return 0 if ready else 1
    return 0 if ensure_vosk_model() else 1


if __name__ == "__main__":
    raise SystemExit(main())
