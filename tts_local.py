"""Локальный синтез речи для Kitsune Assistant.

Два движка, оба работают без интернета после разовой загрузки моделей:

* ``silero``    — PyTorch-модель Silero TTS, лучшее качество русского языка;
* ``kokoro``    — ONNX-экспорт kokoro-ru, лёгкий и не требует torch.

Модуль намеренно устроен так, чтобы **никогда не ронять приложение**.
Любая ошибка — отсутствие пакета, невозможность скачать или распаковать
модель, сбой разбора — приводит лишь к тому, что движок считается
недоступным. Вызывающий код (`assistant_core`) в этом случае просто идёт
дальше по очереди движков: локальный → edge-tts → pyttsx3 (SAPI5).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import warnings

# ---------------------------------------------------------------------------
# Пути и адреса моделей
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

#: Каталог для скачанных моделей. В git не попадает (десятки и сотни мегабайт).
DEFAULT_MODELS_DIR = os.path.join(BASE_DIR, "tts_models")

SILERO_URL = "https://models.silero.ai/models/tts/ru/v5_ru.pt"
SILERO_FILE = "silero_v5_ru.pt"

#: Запасной список голосов, если модель не сообщает свои.
SILERO_FALLBACK_SPEAKERS = ["baya", "kseniya", "xenia", "aidar", "eugene"]

#: Голос по умолчанию. Модель отдаёт список, начиная с мужского aidar, а
#: ассистент говорит женским голосом — и в UI пункт Silero тоже на baya.
SILERO_DEFAULT_SPEAKER = "baya"

KOKORO_REPO = "zaakirio/kokoro-ru"

#: Голос → (чекпоинт ONNX-модели, файл стилевого пака).
#: Света и Маша делят одну модель и различаются только паками.
KOKORO_VOICES = {
    "sveta": ("base", "voices/sveta.bin"),
    "masha": ("base", "voices/masha.bin"),
    "dima": ("dima", "voices/dima.bin"),
}
KOKORO_DEFAULT_VOICE = "sveta"

#: Файл модели по чекпоинту и признаку квантования.
#: fp32 заметно быстрее q8 на CPU (замер в этом проекте: RTF 0.29 против 1.39),
#: поэтому он вариант по умолчанию; q8 оставлен для слабых машин и малого диска.
KOKORO_MODELS = {
    ("base", False): "onnx/model.onnx",
    ("base", True): "onnx/model_quantized.onnx",
    ("dima", False): "onnx/model_dima.onnx",
    ("dima", True): "onnx/model_dima_quantized.onnx",
}

#: Общие ассеты: фронтенд G2P, словари и все стилевые паки.
KOKORO_SHARED_PATTERNS = [
    "ru_g2p.py",
    "config.json",
    "kokoro-config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "espeak-data/**",
    "voices/sveta.bin",
    "voices/masha.bin",
    "voices/dima.bin",
]

#: Размер модели омографов для текстового фронтенда. Должен совпадать с тем,
#: что запрашивает ru_g2p.py, иначе при первом синтезе докачается вторая модель.
KOKORO_OMOGRAPH_SIZE = "turbo3.1"

#: Пакеты, без которых путь kokoro не заработает. espeakng-loader даёт
#: бинарник eSpeak, который нужен misaki и phonemizer.
KOKORO_PACKAGES = (
    ("onnxruntime", "onnxruntime"),
    ("ruaccent", "ruaccent"),
    ("phonemizer", "phonemizer"),
    ("misaki", "misaki"),
    ("espeakng_loader", "espeakng-loader"),
)


def _log(message: str) -> None:
    print(f"[TTS] {message}")


def _prefer_quantized(config: dict) -> bool:
    return bool(config.get("kokoro_prefer_quantized", False))


def kokoro_model_rel(voice: str, prefer_quantized: bool = False) -> str:
    """Относительный путь ONNX-модели для голоса."""
    checkpoint = KOKORO_VOICES.get(voice, KOKORO_VOICES[KOKORO_DEFAULT_VOICE])[0]
    return KOKORO_MODELS[(checkpoint, bool(prefer_quantized))]


def _kokoro_root(directory: str | None = None) -> str:
    return os.path.join(models_dir(directory), "kokoro")


def ruaccent_root() -> str | None:
    """Каталог пакета ruaccent или None, если пакета нет."""
    import importlib.util

    try:
        spec = importlib.util.find_spec("ruaccent")
    except Exception:
        return None
    if spec is None or not spec.origin:
        return None
    return os.path.dirname(spec.origin)


def kokoro_accent_paths() -> list[str]:
    """Что ruaccent докачивает с HuggingFace при построении движка.

    Текстовый фронтенд kokoro (ruaccent) идёт в HuggingFace за моделью ударений
    и складывает её **внутрь своего пакета**, а не в наш каталог моделей. Это
    отдельная загрузка: `snapshot_download` репозитория kokoro-ru её не делает.
    Без неё движок не поднимается вообще, поэтому её надо и проверять, и
    догружать заранее.
    """
    root = ruaccent_root()
    if not root:
        return []
    return [
        os.path.join(root, "dictionary"),
        os.path.join(root, "nn", "nn_omograph", KOKORO_OMOGRAPH_SIZE),
        os.path.join(root, "koziev"),
    ]


def kokoro_accent_ready() -> bool:
    """Скачана ли модель ударений. Проверка без сети и без импорта ruaccent."""
    paths = kokoro_accent_paths()
    return bool(paths) and all(os.path.exists(path) for path in paths)


def prepare_kokoro_accent(
    directory: str | None = None, progress=None
) -> tuple[bool, str]:
    """Догружает модель ударений для фронтенда kokoro. Исключений не бросает.

    Загрузка разовая, и раньше её не делал никто: она случалась при построении
    движка, то есть при первой фразе. Если сети нет, движок просто не
    поднимался, и это выглядело как «kokoro не работает» без объяснения.
    """
    if kokoro_accent_ready():
        return True, ""
    if not _can_import("ruaccent"):
        return False, "нет пакета ruaccent"

    root = _kokoro_root(directory)
    if progress:
        progress("Догружаю модель ударений ruaccent (один раз, нужен huggingface.co)")
    try:
        if root not in sys.path:
            sys.path.insert(0, root)
        from ru_g2p import RuG2P

        # Достаточно собрать фронтенд: он сам скачает всё, чего ему не хватает.
        RuG2P(
            espeak_data=os.path.join(root, "espeak-data"),
            vocab_path=os.path.join(root, "kokoro-config.json"),
            omograph_model_size=KOKORO_OMOGRAPH_SIZE,
        )
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"

    if kokoro_accent_ready():
        return True, ""
    return False, "файлы не появились после загрузки"


def _patch_g2p_encoding(root: str) -> None:
    """Чинит чтение файлов в ru_g2p.py для Windows.

    Оригинальный ``ru_g2p.py`` читает JSON через ``Path.read_text()`` без
    указания кодировки. На русской Windows это cp1252/cp1251, и загрузка
    падает с ``UnicodeDecodeError``. Патч идемпотентный и только добавляет
    явный ``encoding="utf-8"``.
    """
    path = os.path.join(root, "ru_g2p.py")
    if not os.path.isfile(path):
        return
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        if ".read_text()" not in source:
            return
        patched = source.replace(".read_text()", '.read_text(encoding="utf-8")')
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(patched)
        _log("ru_g2p.py: добавлена явная кодировка UTF-8 для Windows")
    except Exception as exc:  # не критично: спасает режим PYTHONUTF8=1
        _log(f"Не удалось пропатчить ru_g2p.py: {exc}")


def models_dir(override: str | None = None) -> str:
    """Каталог моделей: из конфига, иначе значение по умолчанию."""
    path = (override or DEFAULT_MODELS_DIR).strip() or DEFAULT_MODELS_DIR
    if not os.path.isabs(path):
        path = os.path.join(BASE_DIR, path)
    return path


# ---------------------------------------------------------------------------
# Проверка доступности
# ---------------------------------------------------------------------------

def _can_import(module_name: str) -> bool:
    """Проверяет импортируемость пакета, не оставляя мусора в sys.modules."""
    import importlib.util

    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


#: Кэш сведений о PyTorch: импорт torch стоит секунды.
_TORCH_INFO: dict | None = None


def torch_info() -> dict:
    """Сведения о PyTorch. Никогда не бросает исключений.

    Результат кэшируется: импорт torch занимает секунды, а вызывается это
    из горячего пути выбора движка.
    """
    global _TORCH_INFO

    if _TORCH_INFO is not None:
        return _TORCH_INFO

    info = {"available": False, "cuda": False, "device": "cpu", "name": ""}
    if not _can_import("torch"):
        _TORCH_INFO = info
        return info
    try:
        import torch

        info["available"] = True
        if torch.cuda.is_available():
            info["cuda"] = True
            info["device"] = "cuda"
            try:
                info["name"] = torch.cuda.get_device_name(0)
            except Exception:
                info["name"] = "CUDA"
    except Exception as exc:  # битая или несовместимая сборка torch
        _log(f"PyTorch не загрузился: {exc}")
        info["available"] = False
    _TORCH_INFO = info
    return info


def silero_model_path(directory: str | None = None) -> str:
    return os.path.join(models_dir(directory), SILERO_FILE)


def silero_available(directory: str | None = None) -> tuple[bool, str]:
    """Готов ли движок Silero: есть torch и скачанная модель."""
    if not _can_import("torch"):
        return False, "PyTorch не установлен"
    if not os.path.isfile(silero_model_path(directory)):
        return False, "модель Silero не скачана"
    return True, ""


def kokoro_available(
    directory: str | None = None, prefer_quantized: bool = False
) -> tuple[bool, str]:
    """Готов ли движок kokoro: есть пакеты и скачанные ассеты модели."""
    for package, human in KOKORO_PACKAGES:
        if not _can_import(package):
            return False, f"нет пакета {human}"

    root = _kokoro_root(directory)
    needed = [
        os.path.join(root, "ru_g2p.py"),
        os.path.join(root, "kokoro-config.json"),
        os.path.join(root, "espeak-data", "ru_dict"),
        os.path.join(root, kokoro_model_rel(KOKORO_DEFAULT_VOICE, prefer_quantized)),
        os.path.join(root, "voices", KOKORO_DEFAULT_VOICE + ".bin"),
    ]
    for path in needed:
        if not os.path.isfile(path):
            return False, f"нет файла {os.path.relpath(path, root)}"
    # Модель ударений лежит в пакете ruaccent и качается отдельно: без неё
    # движок не построится, значит и «готов» говорить рано.
    if not kokoro_accent_ready():
        return False, "модель ударений не скачана (нужен доступ к huggingface.co)"
    return True, ""


def downloadable_engines(config: dict, directory: str | None = None) -> list[str]:
    """Движки, которые есть смысл догрузить: пакеты стоят, а модели нет.

    Нужна, чтобы приложение подтянуло модели в фоне и при этом не считало это
    обязательным шагом, роняющим запуск при сбое сети.

    Учитывает железо: в режиме ``auto`` модель Silero тянется только при живой
    CUDA, иначе на CPU выгоднее лёгкий kokoro — не стоит занимать сотни
    мегабайт ради заведомо худшего варианта. Явный выбор ``silero`` уважается
    всегда, даже без CUDA.
    """
    requested = str(config.get("tts_engine", "auto")).lower()
    if requested in ("edge-tts", "pyttsx3"):
        # Сетевой движок выбран осознанно — локальные модели не нужны.
        return []

    engines = []

    if _can_import("torch") and not os.path.isfile(silero_model_path(directory)):
        if requested == "silero" or torch_info()["cuda"]:
            engines.append("silero")

    if all(_can_import(pkg) for pkg, _ in KOKORO_PACKAGES):
        model = os.path.join(
            _kokoro_root(directory),
            kokoro_model_rel(KOKORO_DEFAULT_VOICE, _prefer_quantized(config)),
        )
        # Модель ударений качается отдельно от самой модели синтеза, поэтому
        # «файл на месте» ещё не значит «готово».
        if not os.path.isfile(model) or not kokoro_accent_ready():
            engines.append("kokoro")
    return engines


def choose_engine(config: dict, directory: str | None = None) -> tuple[str | None, str]:
    """Выбирает локальный движок. Возвращает (имя или None, пояснение).

    Порядок решения: явный выбор пользователя → Silero (нужны torch и CUDA)
    → kokoro (ONNX, идёт на любом CPU) → ничего, то есть работа через
    edge-tts и pyttsx3 как раньше.
    """
    requested = str(config.get("tts_engine", "auto")).lower()
    quantized = _prefer_quantized(config)

    if requested == "silero":
        ok, reason = silero_available(directory)
        return ("silero", "") if ok else (None, reason)
    if requested == "kokoro":
        ok, reason = kokoro_available(directory, quantized)
        return ("kokoro", "") if ok else (None, reason)
    if requested in ("edge-tts", "pyttsx3"):
        return None, "выбран сетевой движок"

    # auto: Silero раскрывается только на CUDA, на CPU быстрее kokoro.
    silero_ok, silero_reason = silero_available(directory)
    use_silero = silero_ok and torch_info()["cuda"]

    kokoro_ok, kokoro_reason = kokoro_available(directory, quantized)
    if kokoro_ok and not use_silero:
        return "kokoro", ""
    if use_silero:
        return "silero", ""

    # Остался единственный локальный вариант — Silero без CUDA.
    if silero_ok:
        return "silero", ""

    return None, kokoro_reason or silero_reason or "локальные движки недоступны"


# ---------------------------------------------------------------------------
# Загрузка моделей
# ---------------------------------------------------------------------------

def download_silero(directory: str | None = None, progress=None) -> bool:
    """Скачивает модель Silero. Возвращает True при успехе, исключений не бросает."""
    target = silero_model_path(directory)
    if os.path.isfile(target) and os.path.getsize(target) > 1_000_000:
        return True
    if not _can_import("torch"):
        _log("Загрузка Silero невозможна: нет PyTorch")
        return False

    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".part"
    try:
        import torch

        if progress:
            progress("Скачиваю модель Silero (один раз)")
        torch.hub.download_url_to_file(SILERO_URL, tmp, progress=False)
        os.replace(tmp, target)
        _log(f"Модель Silero загружена: {target}")
        return True
    except Exception as exc:
        _log(f"Не удалось скачать модель Silero: {exc}")
        for path in (tmp, target):
            try:
                if os.path.exists(path) and os.path.getsize(path) < 1_000_000:
                    os.remove(path)
            except Exception:
                pass
        return False


def download_kokoro(
    directory: str | None = None, progress=None, prefer_quantized: bool = False
) -> bool:
    """Скачивает ассеты kokoro-ru. Исключений не бросает.

    Кроме репозитория модели догружает модель ударений для текстового
    фронтенда: без неё движок не построится, а раньше эта загрузка случалась
    неожиданно при первой фразе и молча падала без сети.
    """
    root = _kokoro_root(directory)
    model_file = kokoro_model_rel(KOKORO_DEFAULT_VOICE, prefer_quantized)

    if os.path.isfile(os.path.join(root, model_file)):
        _patch_g2p_encoding(root)
    else:
        if not _can_import("huggingface_hub"):
            _log("Загрузка kokoro невозможна: нет пакета huggingface_hub")
            return False

        try:
            from huggingface_hub import snapshot_download

            if progress:
                size = "138 МБ" if prefer_quantized else "326 МБ"
                progress(f"Скачиваю модель kokoro (один раз, {size})")
            os.makedirs(root, exist_ok=True)
            snapshot_download(
                repo_id=KOKORO_REPO,
                local_dir=root,
                allow_patterns=KOKORO_SHARED_PATTERNS + [model_file],
            )
            _patch_g2p_encoding(root)
            _log(f"Модель kokoro загружена: {root}")
        except Exception as exc:
            _log(f"Не удалось скачать модель kokoro: {exc}")
            return False

    accent_ok, accent_reason = prepare_kokoro_accent(directory, progress)
    if not accent_ok:
        _log(f"Модель ударений для kokoro не загрузилась: {accent_reason}")
        return False
    return True


def ensure_kokoro_model(
    directory: str | None, voice: str, prefer_quantized: bool = False
) -> str:
    """Путь к ONNX-модели голоса, при необходимости догружает её.

    Нужна для голосов, которые не тянулись при первой загрузке (например dima).
    """
    root = _kokoro_root(directory)
    model_rel = kokoro_model_rel(voice, prefer_quantized)
    path = os.path.join(root, model_rel)
    if os.path.isfile(path):
        return path

    from huggingface_hub import hf_hub_download

    _log(f"Догружаю модель голоса {voice}: {model_rel}")
    return hf_hub_download(repo_id=KOKORO_REPO, filename=model_rel, local_dir=root)


# ---------------------------------------------------------------------------
# Движки
# ---------------------------------------------------------------------------

class SileroEngine:
    """Синтез через Silero TTS. Требует PyTorch."""

    name = "silero"

    def __init__(self, directory: str | None = None, speaker: str = ""):
        import torch

        info = torch_info()
        model_path = silero_model_path(directory)

        self._torch = torch
        self._device = info["device"] if info["available"] else "cpu"
        self.device_name = info.get("name") or "CPU"

        with warnings.catch_warnings():
            # torch.package ругается на TypedStorage внутри собственной распаковки.
            # Предупреждение не про этот код, но пугает в консоли и, попадая в
            # stderr, выглядит как ошибка запуска.
            warnings.filterwarnings("ignore", message=".*TypedStorage is deprecated.*")
            model = torch.package.PackageImporter(model_path).load_pickle(
                "tts_models", "model"
            )
        model.to(self._device)
        self._model = model

        available = list(getattr(model, "speakers", []) or [])
        self.speakers = available or list(SILERO_FALLBACK_SPEAKERS)
        if speaker in self.speakers:
            self.speaker = speaker
        elif SILERO_DEFAULT_SPEAKER in self.speakers:
            self.speaker = SILERO_DEFAULT_SPEAKER
        else:
            self.speaker = self.speakers[0]
        self.sample_rate = 48000

    def synthesize(self, text: str):
        """Возвращает (numpy float32 mono в [-1, 1], частота дискретизации)."""
        with self._torch.no_grad():
            audio = self._model.apply_tts(
                text=text, speaker=self.speaker, sample_rate=self.sample_rate
            )
        return audio.detach().cpu().numpy().astype("float32"), self.sample_rate


class KokoroEngine:
    """Синтез через kokoro-ru (ONNX). PyTorch не нужен."""

    name = "kokoro"

    def __init__(
        self,
        directory: str | None = None,
        voice: str = KOKORO_DEFAULT_VOICE,
        prefer_quantized: bool = False,
    ):
        import numpy as np
        import onnxruntime as ort

        root = _kokoro_root(directory)
        voice = voice if voice in KOKORO_VOICES else KOKORO_DEFAULT_VOICE
        voice_rel = KOKORO_VOICES[voice][1]

        _patch_g2p_encoding(root)
        model_path = ensure_kokoro_model(directory, voice, prefer_quantized)

        self.voice = voice
        self.sample_rate = 24000
        self._np = np

        # ru_g2p ищет ассеты рядом с собой, поэтому добавляем его каталог в путь.
        if root not in sys.path:
            sys.path.insert(0, root)
        from ru_g2p import RuG2P  # noqa: WPS433 — импорт по месту, модуль опционален

        self._g2p = RuG2P(
            espeak_data=os.path.join(root, "espeak-data"),
            vocab_path=os.path.join(root, "kokoro-config.json"),
            omograph_model_size=KOKORO_OMOGRAPH_SIZE,
        )
        with open(os.path.join(root, "config.json"), encoding="utf-8") as handle:
            self._vocab = json.load(handle)["vocab"]
        self._sess = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        self._style = np.fromfile(
            os.path.join(root, voice_rel), dtype="float32"
        ).reshape(510, 256)

    def synthesize(self, text: str):
        """Возвращает (numpy float32 mono в [-1, 1], частота дискретизации)."""
        np = self._np
        ipa, _oov = self._g2p(text)
        ids = [self._vocab[c] for c in ipa if c in self._vocab]
        if not ids:
            return np.zeros(0, dtype="float32"), self.sample_rate

        row = min(len(ipa) - 1, self._style.shape[0] - 1)
        audio = self._sess.run(
            None,
            {
                "input_ids": np.array([[0, *ids, 0]], dtype=np.int64),
                "style": self._style[row][None],
                "speed": np.ones(1, dtype=np.float32),
            },
        )[0]
        return np.asarray(audio, dtype="float32").reshape(-1), self.sample_rate


def create_engine(engine_name: str, config: dict, directory: str | None = None):
    """Создаёт движок по имени. При любой ошибке возвращает None."""
    try:
        if engine_name == "silero":
            return SileroEngine(directory, speaker=config.get("silero_speaker", ""))
        if engine_name == "kokoro":
            return KokoroEngine(
                directory,
                voice=config.get("kokoro_voice", ""),
                prefer_quantized=_prefer_quantized(config),
            )
    except Exception as exc:
        _log(f"Движок {engine_name} не запустился: {exc}")
        return None
    return None


def normalize_peak(audio, target: float = 0.9, max_gain: float = 8.0):
    """Выравнивает громкость движков по пику сигнала.

    kokoro отдаёт звук с пиком около 0.2, Silero — около 1.0. Без выравнивания
    смена движка меняла бы громкость в разы, а спектр в HUD — масштаб. Рост
    ограничен ``max_gain``, чтобы тишина или шум не раздувались в щелчок.
    """
    if audio is None or len(audio) == 0:
        return audio
    try:
        import numpy as np
    except Exception:
        return audio

    peak = float(np.abs(audio).max())
    if peak <= 1e-4:  # тишина: усиливать нечего
        return audio

    gain = min(target / peak, max_gain)
    if abs(gain - 1.0) < 0.01:  # уже в норме — не трогаем дискретизацию
        return audio
    return (np.asarray(audio, dtype="float32") * gain).astype("float32", copy=False)


def warmup(engine_name: str, config: dict, directory: str | None = None) -> str:
    """Пробует поднять движок и произносит короткую фразу.

    Возвращает человекочитаемый итог для лога. Нужна только для диагностики.
    """
    engine = create_engine(engine_name, config, directory)
    if engine is None:
        return f"{engine_name}: не запустился"
    try:
        started = time.time()
        samples, rate = engine.synthesize("Проверка синтеза речи.")
        seconds = len(samples) / float(rate or 1)
        return (
            f"{engine_name}: ок, {seconds:.2f} с речи за "
            f"{time.time() - started:.2f} с"
        )
    except Exception as exc:
        return f"{engine_name}: ошибка синтеза ({exc})"


def _download_all_cli() -> int:
    """Ручная предзагрузка моделей: python tts_local.py [silero|kokoro|all] [--q8]."""
    args = [a.lower() for a in sys.argv[1:]]
    quantized = "--q8" in args
    positional = [a for a in args if not a.startswith("--")]
    what = positional[0] if positional else "all"

    ok = True
    if what in ("silero", "all"):
        ok = download_silero(progress=_log) and ok
    if what in ("kokoro", "all"):
        ok = download_kokoro(progress=_log, prefer_quantized=quantized) and ok

    for name in ("silero", "kokoro"):
        available, reason = (
            silero_available()
            if name == "silero"
            else kokoro_available(prefer_quantized=quantized)
        )
        _log(f"{name}: {'готов' if available else 'недоступен — ' + reason}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(_download_all_cli())
