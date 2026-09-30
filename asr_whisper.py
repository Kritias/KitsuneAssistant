"""ASR-бэкенд на faster-whisper (CUDA или CPU).

Vosk распознаёт потоково и отдаёт частичные результаты, а whisper работает
сегментами. Поэтому здесь своя логика: копим аудио, находим конец фразы по
тишине (энергетический VAD из `asr_vad`) и распознаём накопленный фрагмент
целиком.

Модуль самодостаточен: `assistant_core` выбирает устройство (GPU/CPU) и
дёргает `feed()` на каждом блоке аудио.
"""

from __future__ import annotations

import gc
import os
import subprocess
import sys
import threading
import time

import numpy as np

from asr_vad import (  # noqa: F401  (константы реэкспортируются для совместимости)
    ABS_ENERGY_FLOOR,
    FRAME_MS,
    HYSTERESIS,
    MAX_NOISE_FLOOR,
    SAMPLERATE,
    SUBFRAME_MS,
    SUBFRAME_SAMPLES,
    TAIL_PAD_MS,
    EnergyVad,
)

# Ниже этой VRAM видеокарта считается слабой, и whisper не включается
DEFAULT_MIN_VRAM_MB = 3000

# Типовые галлюцинации whisper на тишине и шуме — отбрасываем их
HALLUCINATIONS = (
    "продолжение следует",
    "субтитры", "субтитры сделал", "редактор субтитров", "корректор",
    "спасибо за просмотр", "подписывайтесь на канал", "ставьте лайк",
    "thank you for watching", "subscribe",
)


def prepare_cuda_dlls():
    """Прописывает в PATH библиотеки CUDA из pip-колёс nvidia-*.

    CTranslate2 на Windows ищет cublas64_12.dll и cudnn64_9.dll через PATH,
    а колёса nvidia-cublas-cu12 / nvidia-cudnn-cu12 кладут их глубоко в
    site-packages. Без этого шага получаем 'Library cublas64_12.dll is not found'.
    """
    if os.name != "nt":
        return
    if getattr(prepare_cuda_dlls, "_done", False):
        return  # повторный вызов не должен копить одинаковые записи в PATH
    base = os.path.join(sys.prefix, "Lib", "site-packages", "nvidia")
    if not os.path.isdir(base):
        return
    for root, _dirs, files in os.walk(base):
        if not any(f.lower().endswith(".dll") for f in files):
            continue
        try:
            os.add_dll_directory(root)
        except (OSError, AttributeError):
            pass
        os.environ["PATH"] = root + os.pathsep + os.environ.get("PATH", "")
    prepare_cuda_dlls._done = True


def whisper_available():
    """Проверяет наличие faster-whisper. Возвращает (ok, причина)."""
    prepare_cuda_dlls()
    try:
        import ctranslate2  # noqa: F401
        import faster_whisper  # noqa: F401
    except Exception as e:
        return False, f"faster-whisper недоступен: {e}"
    return True, ""


def cuda_info():
    """Информация о CUDA-устройстве или None, если CUDA недоступна."""
    try:
        import ctranslate2
    except Exception:
        return None

    try:
        if ctranslate2.get_cuda_device_count() < 1:
            return None
    except Exception:
        return None

    info = {"name": "CUDA GPU", "vram_mb": 0, "compute_types": []}
    try:
        info["compute_types"] = list(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        pass

    # Имя карты и объём памяти берём у nvidia-smi: он есть вместе с драйвером
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            name, vram = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")[:2]]
            info["name"] = name
            # У виртуальных/гибридных карт память бывает «[N/A]» — это не ошибка,
            # просто объём неизвестен, и проверка порога его проигнорирует.
            info["vram_mb"] = int(float(vram))
        except (ValueError, IndexError):
            pass
        except Exception:
            pass
    return info


def choose_compute_type(cuda, preferred="int8_float16"):
    """Подбирает тип вычислений, поддерживаемый конкретной картой."""
    supported = (cuda or {}).get("compute_types") or []
    for candidate in (preferred, "float16", "int8_float32", "int8", "float32"):
        if not supported or candidate in supported:
            return candidate
    return "float32"


def choose_compute_type_cpu(preferred="int8"):
    """Подбирает тип вычислений для Whisper на процессоре.

    На CPU обычно лучший баланс — ``int8``: быстрее float32 и заметно
    легче по памяти, без заметной потери точности для коротких команд.
    """
    supported = []
    try:
        import ctranslate2
        supported = list(ctranslate2.get_supported_compute_types("cpu"))
    except Exception:
        pass
    for candidate in (preferred, "int8", "int8_float32", "float32"):
        if not supported or candidate in supported:
            return candidate
    return "float32"


def release_memory():
    """Просит GC и CUDA-кэш отдать память после выгрузки модели."""
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


class WhisperStreamRecognizer:
    """Потоковое распознавание через whisper с определением конца фразы.

    Модель грузится в отдельном потоке (`load_async`), чтобы не подвешивать
    интерфейс на время загрузки из кэша HuggingFace.
    """

    def __init__(self, model_name="large-v3-turbo", device="cuda",
                 compute_type="int8_float16", language="ru",
                 silence_ms=1000, energy_factor=2.2, max_utterance_s=12.0,
                 min_speech_ms=200, on_log=None):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.min_speech_ms = int(min_speech_ms)
        self.on_log = on_log

        # Границы фразы считает общий с Vosk модуль: одни и те же настройки
        # тишины и гистерезиса работают на любом железе.
        self.vad = EnergyVad(
            silence_ms=silence_ms,
            energy_factor=energy_factor,
            max_utterance_s=max_utterance_s,
            min_speech_ms=min_speech_ms,
        )

        self.model = None
        self.load_error = None
        self.load_seconds = 0.0
        self._lock = threading.Lock()
        #: Инвалидирует фоновую загрузку при unload / повторном load_async.
        self._load_token = 0

    # ------------------------------------------------------------------ загрузка

    def load_async(self):
        self._load_token += 1
        token = self._load_token
        threading.Thread(target=self.load, args=(token,), daemon=True).start()

    def load(self, token=None):
        """Грузит модель. Исключения запоминаются в `load_error`."""
        if token is None:
            token = self._load_token
        try:
            if self.device == "cuda":
                prepare_cuda_dlls()
            from faster_whisper import WhisperModel

            started = time.time()
            model = WhisperModel(
                self.model_name, device=self.device, compute_type=self.compute_type
            )
            # Прогреваем граф, чтобы первая реальная фраза не ждала инициализации
            model.transcribe(
                np.zeros(SAMPLERATE, dtype=np.float32),
                language=self.language,
                beam_size=1,
            )
            load_seconds = time.time() - started
        except Exception as e:
            if token == self._load_token:
                self.load_error = f"{type(e).__name__}: {e}"
                self.model = None
                self._log(f"Загрузка Whisper не удалась: {self.load_error}")
            return

        if token != self._load_token:
            # Пока грузили, движок уже сменили — новая модель сразу в утиль.
            del model
            release_memory()
            return

        old = self.model
        self.model = model
        self.load_seconds = load_seconds
        self.load_error = None
        if old is not None:
            del old
            release_memory()
        self._log(
            f"Whisper «{self.model_name}» загружен за {self.load_seconds:.1f} с "
            f"({self.device}/{self.compute_type})"
        )

    def unload(self):
        """Снимает модель с устройства и отменяет фоновую загрузку."""
        self._load_token += 1
        with self._lock:
            self.vad.reset()
            model = self.model
            self.model = None
        if model is not None:
            del model
        release_memory()
        self._log(f"Whisper «{self.model_name}» выгружен из памяти")

    @property
    def ready(self):
        return self.model is not None

    def _log(self, message):
        if self.on_log:
            self.on_log(message)
        else:
            print(f"[Whisper] {message}")

    # ------------------------------------------------------------------ состояние

    def reset(self):
        """Забывает незаконченную фразу."""
        with self._lock:
            self.vad.reset()

    # ------------------------------------------------------------------ приём аудио

    def feed(self, pcm_int16_bytes):
        """Принимает блок аудио. Возвращает текст законченной фразы или None."""
        if not self.ready:
            return None

        with self._lock:
            if not self.vad.push(pcm_int16_bytes):
                return None
            utterance, speech_ms = self.vad.take_audio()

        # Слишком короткий всплеск — это щелчок или кашель, а не команда.
        if utterance is None or speech_ms < self.min_speech_ms:
            return None
        return self._transcribe(utterance)

    # ------------------------------------------------------------------ распознавание

    def _transcribe(self, audio):
        try:
            segments, _info = self.model.transcribe(
                audio,
                language=self.language,
                beam_size=1,
                condition_on_previous_text=False,
                vad_filter=False,
                word_timestamps=False,
            )
            parts = []
            for seg in segments:
                # Отсекаем «фантомные» сегменты, которые whisper выдаёт на шуме
                if getattr(seg, "no_speech_prob", 0.0) > 0.6 and getattr(seg, "avg_logprob", 0.0) < -1.0:
                    continue
                text = seg.text.strip()
                if text:
                    parts.append(text)
        except Exception as e:
            self._log(f"Ошибка распознавания: {type(e).__name__}: {e}")
            return None

        text = " ".join(parts).strip()
        return None if self._is_hallucination(text) else text

    @staticmethod
    def _is_hallucination(text):
        if not text:
            return True
        low = text.lower().strip(" .,!?…-—")
        if len(low) < 2:
            return True
        return any(marker in low for marker in HALLUCINATIONS)
