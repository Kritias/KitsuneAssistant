import os  
import sys  
import io  
import gc
import json  
import time  
import queue  
import random
import re  
import threading  
import subprocess  
import webbrowser  
import ctypes  
import tempfile  
import wave  
import asyncio  
from collections import deque
from datetime import datetime  
from pathlib import Path  
  
import pyautogui  
import pyttsx3  
import pyperclip  
import keyboard  
import sounddevice as sd  
import soundfile as sf  
import edge_tts  
from vosk import Model, KaldiRecognizer, SetLogLevel  
from rapidfuzz import fuzz  
import numpy as np  

try:
    import asr_whisper
except Exception as _asr_import_error:  # модуль опционален: без него работает Vosk
    asr_whisper = None
    print(f"[ASR] Модуль asr_whisper недоступен: {_asr_import_error}")

from asr_vad import EnergyVad

try:
    import tts_local
except Exception as _tts_import_error:  # модуль опционален: без него edge-tts и SAPI
    tts_local = None
    print(f"[TTS] Модуль tts_local недоступен: {_tts_import_error}")

try:
    import crypto_rates
except Exception as _rates_import_error:  # модуль опционален: без него нет команд «курс ...»
    crypto_rates = None
    print(f"[Rates] Модуль crypto_rates недоступен: {_rates_import_error}")

try:
    import cbr_rates
except Exception as _cbr_import_error:  # модуль опционален: без него нет «курс валюты»
    cbr_rates = None
    print(f"[CBR] Модуль cbr_rates недоступен: {_cbr_import_error}")

try:
    import weather
except Exception as _weather_import_error:  # модуль опционален: без него нет «погода»
    weather = None
    print(f"[Weather] Модуль weather недоступен: {_weather_import_error}")

try:
    import miniaudio
except Exception as _miniaudio_import_error:  # без него edge-tts ждёт весь mp3
    miniaudio = None
    print(f"[TTS] Потоковое воспроизведение недоступно: {_miniaudio_import_error}")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
COMMANDS_DIR = os.path.join(BASE_DIR, "commands")
MARKER_FILE = os.path.expanduser(r"~\\.sleep_never_marker")  

# =====================================================================
# СЛОТЫ КОМАНД: «включи саус парк серия 312», «курс биткоина»
# =====================================================================

#: Именованная дырка в шаблоне команды, например "включи саус парк серия {number}".
#: Слот всегда стоит в хвосте фразы — так его значение не надо выковыривать
#: из середины, а короткий шаблон без слота не спорит с шаблоном со слотом.
SLOT_TOKEN_RE = re.compile(r"\{(\w+)\}")

#: Размер очереди аудио: 100 мс на блок, то есть минута запаса. Очередь нужна,
#: чтобы пережить долгое распознавание и выполнение сценария (set_volume жмёт
#: клавиши десятки раз) без потери начала следующей фразы.
AUDIO_QUEUE_BLOCKS = 600

#: Сколько микрофон молчит после окончания реплики. Эхо в комнате живёт дольше
#: самой речи, и без этой паузы хвост собственных слов попадает в распознавание.
MIC_ECHO_TAIL_MS = 350

#: Порог похожести шаблона, выше обычного командного: «включи саус парк» не
#: должно ловиться шаблоном «саус парк {number}». Подлинность значения слота
#: проверяется отдельно, поэтому здесь важнее строгость, чем терпимость к ASR.
SLOT_PREFIX_THRESHOLD = 76

#: Слова-цифры и числительные: пользователь может продиктовать код серии
#: цифрами («312»), по одной цифре («три один два») или числом
#: («сто двенадцать»). Английские формы нужны для en.command.
ONE_DIGIT_WORDS = {
    "ноль": "0", "нуль": "0", "zero": "0", "oh": "0",
    "один": "1", "одна": "1", "одно": "1", "one": "1",
    "два": "2", "две": "2", "two": "2",
    "три": "3", "three": "3",
    "четыре": "4", "four": "4",
    "пять": "5", "five": "5",
    "шесть": "6", "six": "6",
    "семь": "7", "seven": "7",
    "восемь": "8", "eight": "8",
    "девять": "9", "nine": "9",
}

#: Числительные целиком — собираются сложением разрядов: «сто двадцать три».
RU_NUMBER_VALUES = {
    "ноль": 0, "нуль": 0, "один": 1, "одна": 1, "одно": 1, "два": 2, "две": 2,
    "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
    "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
    "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19, "двадцать": 20,
    "тридцать": 30, "сорок": 40, "пятьдесят": 50, "шестьдесят": 60,
    "семьдесят": 70, "восемьдесят": 80, "девяносто": 90, "сто": 100,
    "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500,
    "шестьсот": 600, "семьсот": 700, "восемьсот": 800, "девятьсот": 900,
    "тысяча": 1000, "тысячи": 1000, "тысяч": 1000,
}

#: Как номер проговаривается по цифрам — требование к команде серии.
RU_SPOKEN_DIGITS = (
    "ноль", "один", "два", "три", "четыре", "пять", "шесть", "семь",
    "восемь", "девять",
)
EN_SPOKEN_DIGITS = (
    "zero", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine",
)

# ===========================================================================
# РЕЖИМЫ ПРИЛОЖЕНИЯ: базовый (без тяжёлых моделей) и полный
# ===========================================================================
#: Базовый режим: STT — только Vosk, синтез — edge-tts/SAPI. Whisper
#: (включая medium/small на CPU), Silero и kokoro остаются в полном режиме.
MODE_BASIC = "basic"
MODE_FULL = "full"
#: Значения, которые принимает конфиг и принудительно выставляет каждый режим.
# В полном режиме по умолчанию остаёмся на Vosk/edge-tts: Whisper/Silero/kokoro
# качаются только когда пользователь выберет их в настройках.
_BASIC_DEFAULTS = {
    "asr_engine": "vosk",
    "tts_engine": "auto",  # auto в базовом режиме раскрывается в edge-tts
}
_FULL_DEFAULTS = {
    "asr_engine": "vosk",
    "tts_engine": "auto",
}
#: Движок STT → (устройство, модель). None у модели = взять whisper_model из конфига.
_WHISPER_ENGINE_PRESETS = {
    "auto": ("cuda", None),
    "whisper": ("cuda", None),
    "whisper_cpu": ("cpu", "large-v3-turbo"),
    "whisper_medium_cpu": ("cpu", "medium"),
    "whisper_small_cpu": ("cpu", "small"),
}


def apply_mode_defaults(config, full_mode: bool):
    """Приводит поля движков конфига к значениям выбранного режима.

    Переключение режима сбрасывает выбор на vosk + auto-синтез. В полном
    режиме меню открывает Whisper/Silero/kokoro, но пакеты и веса качаются
    только после явного выбора движка в настройках. Прочие настройки
    (словарь, пороги, голосовые ключи) не трогаются.
    """
    config["full_mode"] = bool(full_mode)
    defaults = _FULL_DEFAULTS if full_mode else _BASIC_DEFAULTS
    for key, value in defaults.items():
        config[key] = value
    return config


def canonical_number(digits):
    """Убирает ведущие нули.

    Сайт сериалов отдаёт /episode/101/, а /episode/0101/ и /episode/1/ — 404,
    поэтому «ноль один ноль один» должно превратиться в 101, а не в 101 или 0101.
    """
    stripped = (digits or "").lstrip("0")
    return stripped or "0"


def parse_slot_number(text, tolerate_noise=False):
    """Разбирает продиктованный номер и возвращает строку цифр или None.

    `tolerate_noise` нужен для фраз, пришедших из распознавания: на паузе оно
    вставляет лишние слова. На записи «включи саус парк, серия 312» Vosk сам
    добавил «тире» и выдал «включи саус парк тире триста двенадцать». Команда к
    этому моменту уже опознана по началу фразы, поэтому лишние слова просто
    выбрасываем: иначе номер теряется и срабатывает команда без слота.
    """
    tokens = (text or "").split()
    if not tokens:
        return None

    if tolerate_noise:
        tokens = [t for t in tokens
                  if t.isdigit() or t in ONE_DIGIT_WORDS or t in RU_NUMBER_VALUES]
        if not tokens:
            return None

    # Написано цифрами: «312» или «3 1 2».
    if all(token.isdigit() for token in tokens):
        return canonical_number("".join(tokens))

    # Продиктовано по одной цифре: «три один два», «ноль один ноль один».
    if all(token in ONE_DIGIT_WORDS for token in tokens):
        return canonical_number("".join(ONE_DIGIT_WORDS[token] for token in tokens))

    # Числительное: «сто двенадцать».
    total, current = 0, 0
    for token in tokens:
        value = RU_NUMBER_VALUES.get(token)
        if value is None:
            return None
        if value >= 1000:
            total += (current or 1) * 1000
            current = 0
        else:
            current += value
    return canonical_number(str(total + current))


def spoken_digits(digits, lang="ru"):
    """Проговаривает номер по одной цифре: «312» → «три один два»."""
    words = RU_SPOKEN_DIGITS if lang == "ru" else EN_SPOKEN_DIGITS
    return " ".join(words[int(ch)] for ch in str(digits) if ch.isdigit())


DEFAULT_CONFIG = {  
    "wake_word": "лисичка",  
    "wake_aliases": [  
        "лисичка",  
        "лиса",  
        "лисица",  
        "лисички",  
        "кицунэ",  
        "рыжая",  
        "fox",  
        "kitsune",  
        "foxie"  
    ],  
    "require_wake_word": True,  
    "wake_timeout": 7.0,  
    # auto: сначала локальный синтез (Silero/kokoro), затем edge-tts и SAPI.
    "tts_engine": "auto",  
    "tts_voice": "ru-RU-SvetlanaNeural",
    "tts_rate": 190,
    "tts_pitch": "+10Hz",
    "tts_rate_edge": "+15%",
    "tts_models_dir": "tts_models",
    "silero_speaker": "",
    "kokoro_voice": "sveta",
    # true — лёгкая q8-модель kokoro (138 МБ) вместо fp32 (326 МБ): меньше диск,
    # но на CPU примерно вчетверо медленнее.
    "kokoro_prefer_quantized": False,
    "icon_path": "",  
    "avatar_path": "",  
    "icon_sleep_5min": "",  
    "icon_sleep_never": "",  
    "language": "ru",
    "microphone": "",
    "asr_grammar": True,
    "asr_grammar_extra": [],
    "asr_debug": False,
    "asr_cmd_threshold": 70,
    "asr_chitchat_threshold": 70,
    "asr_chitchat_strong": 75,
    "asr_engine": "vosk",
    # false — базовый режим: STT только Vosk, синтез без локальных моделей.
    # Первый запуск всегда базовый; полный включается кнопкой в «О лисе».
    "full_mode": False,
    "asr_mute_while_speaking": True,
    "whisper_model": "large-v3-turbo",
    "whisper_compute_type": "",
    "whisper_min_vram_mb": 3000,
    # Тишина после речи, после которой фраза считается законченной.
    # 1400 мс терпит паузы в длинных командах («погода в … на завтра»,
    # «курс доллара к рублю»); короче — Vosk/VAD рвут фразу на середине.
    "vad_silence_ms": 1400,
    "vad_energy_factor": 2.2,
    "vad_max_utterance_s": 18,
    # wttr — без ключа; weatherapi — ключ в weatherapi_key. Пустой ключ
    # не мешает: если выбранный источник молчит, берётся второй.
    "weather_provider": "weatherapi",
    "weather_location": "Долгопрудный",
    "weatherapi_key": "",
}  

def get_desktop_dir():
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
        )
        reg_val, _ = winreg.QueryValueEx(key, "Desktop")
        expanded = os.path.expandvars(reg_val)
        if os.path.isdir(expanded):
            return Path(expanded)
    except Exception:
        pass

    candidates = [
        Path.home() / "Desktop",
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "Рабочий стол",
        Path.home() / "OneDrive" / "Рабочий стол"
    ]
    for p in candidates:
        if p.is_dir():
            return p

    fallback = Path.home() / "Desktop"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback

def find_vosk_model_dir(base_folder="model"):  
    if not os.path.isabs(base_folder):
        base_folder = os.path.join(BASE_DIR, base_folder)
    if not os.path.exists(base_folder):  
        return None  
    if os.path.exists(os.path.join(base_folder, "conf")):  
        return base_folder  
    for root, dirs, files in os.walk(base_folder):  
        if "conf" in dirs:  
            return root  
    return None  
  
class _EdgeMp3Feeder(miniaudio.StreamableSource if miniaudio is not None else object):
    """Отдаёт байты mp3 декодеру по мере поступления.

    Декодер просит крупный кусок (часто 64 КБ). Если ждать ровно столько,
    воспроизведение начнётся только когда скачается весь ответ. Поэтому
    отдаём уже пришедшие байты, а пустой ответ — только в конце потока.
    """

    def __init__(self, stop_event):
        self._buf = bytearray()
        self._cv = threading.Condition()
        self._eof = False
        self._stop = stop_event

    def write(self, data):
        if not data:
            return
        with self._cv:
            if self._eof:
                return
            self._buf.extend(data)
            self._cv.notify_all()

    def finish(self):
        with self._cv:
            self._eof = True
            self._cv.notify_all()

    def read(self, num_bytes):
        with self._cv:
            while not self._buf and not self._eof:
                if self._stop.is_set():
                    self._eof = True
                    break
                self._cv.wait(timeout=0.05)
            take = min(num_bytes, len(self._buf))
            if take <= 0:
                return b""
            chunk = bytes(self._buf[:take])
            del self._buf[:take]
            return chunk


class _LivePcmPlayer:
    """Играет float32-кадры по мере поступления, не дожидаясь всей реплики."""

    def __init__(self, owner, sample_rate):
        self.owner = owner
        self.sr = int(sample_rate)
        self.blocks = deque()
        self.lock = threading.Lock()
        self.offset = 0
        self.done = False
        self.samples = 0
        self.finished = threading.Event()
        self._stream = None

    def push(self, samples):
        arr = np.array(samples, dtype=np.float32, copy=True).reshape(-1)
        if arr.size == 0 or self.done:
            return
        with self.lock:
            self.blocks.append(arr)
            self.samples += int(arr.size)
            start = self._stream is None
        if start:
            self._open()

    def _open(self):
        def callback(outdata, frames, time_info, status):
            if self.owner.stop_speech_event.is_set():
                outdata.fill(0)
                self.finished.set()
                raise sd.CallbackStop
            filled, exhausted = self._pull(frames, outdata[:, 0])
            if filled < frames:
                outdata[filled:, 0] = 0.0
            if filled > 0:
                self.owner._calc_spectrum(outdata[:filled, 0], sr=self.sr)
            if exhausted:
                self.finished.set()
                raise sd.CallbackStop

        stream = sd.OutputStream(
            samplerate=self.sr, channels=1, blocksize=1600, callback=callback
        )
        self._stream = stream
        self.owner.active_output_stream = stream
        try:
            stream.start()
        except Exception:
            self._stream = None
            self.owner.active_output_stream = None
            self.finished.set()
            raise

    def _pull(self, frames, out):
        filled = 0
        with self.lock:
            while filled < frames and self.blocks:
                block = self.blocks[0]
                avail = len(block) - self.offset
                take = min(avail, frames - filled)
                out[filled:filled + take] = block[self.offset:self.offset + take]
                self.offset += take
                filled += take
                if self.offset >= len(block):
                    self.blocks.popleft()
                    self.offset = 0
            exhausted = self.done and not self.blocks
        return filled, exhausted

    def finish(self):
        """Доигрывает уже принятые кадры и отпускает устройство."""
        with self.lock:
            self.done = True
        if self._stream is None:
            self.finished.set()
            return
        while not self.finished.is_set():
            if self.owner.stop_speech_event.is_set() or not self._stream.active:
                try:
                    self._stream.abort()
                except Exception:
                    pass
                break
            self.finished.wait(timeout=0.03)
        stream = self._stream
        self._stream = None
        if self.owner.active_output_stream is stream:
            self.owner.active_output_stream = None
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass


def load_language_dict(lang_code="ru"):  
    file_path = os.path.join(BASE_DIR, "lang", f"{lang_code}.lang")
    if not os.path.exists(file_path):  
        file_path = os.path.join(BASE_DIR, "lang", "ru.lang")
    if os.path.exists(file_path):  
        try:  
            with open(file_path, "r", encoding="utf-8") as f:  
                return json.load(f)  
        except Exception as e:  
            print(f"[Language Load Error]: {e}")  
    return {}  
  
class FoxAssistantCore:  
    def __init__(self, update_gui_callback=None, status_callback=None, sleep_change_callback=None, window_action_callback=None):  
        self.update_gui = update_gui_callback  
        self.update_status = status_callback  
        self.sleep_change_callback = sleep_change_callback  
        self.window_action = window_action_callback  
          
        self.config = self._coerce_config_types(self.load_config())  
        self.lang = {}  
        self.all_chitchat_triggers = {}  
          
        self.command_data_ru = {}  
        self.command_data_en = {}  
        self.commands_ru = {}  
        self.commands_en = {}  
        self.commands = {}  
        self.load_all_commands()  
          
        self.reload_language()  
          
        self.is_listening = False  
        # 100 мс на блок, значит 600 блоков — это минута запаса. Раньше стояло 50
        # (5 секунд), и при переполнении аудио молча выбрасывалось: пока whisper
        # распознаёт фразу или set_volume жмёт клавиши, очередь успевала
        # заполниться, и начало следующей фразы терялось.
        self.audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_BLOCKS)  
        self.audio_dropped_blocks = 0  
        self.audio_drop_reported = 0  
        # Пока ассистент говорит, микрофон не слушаем (и ещё чуть-чуть после:
        # комнатное эхо живёт дольше самой реплики). Иначе он распознаёт сам
        # себя и «слышит» команды, которых никто не говорил.
        self.mic_resume_at = 0.0  
        self._unk_warned = False  # чтобы не повторять одну и ту же подсказку
        # Границы фразы для Vosk считаем сами: встроенный endpoint Kaldi
        # завершает utterance уже после ~0.5 с паузы и рвёт длинные команды.
        self.vosk_vad = None
        self.num_bands = 28  
        self.latest_spectrum = [0.0] * self.num_bands  
        self.band_edges = np.logspace(np.log10(90), np.log10(3800), self.num_bands + 1)  
          
        self.is_active_session = False  
        self.last_activation_time = 0.0  
        self.is_speaking = False  
          
        self.stop_speech_event = threading.Event()  
        self.active_output_stream = None  
        self.current_speaking_text = ""  
          
        self.keyboard_locked = False  
        self.kb_hook = None  
         
        self.sapi_voices = {"ru": None, "en": None}  
        self._detect_system_voice()  
          
        self.tts_queue = queue.Queue()  
        threading.Thread(target=self._tts_worker_loop, daemon=True).start()  
         
        # Vosk поднимается только если он и есть выбранный движок. Whisper
        # не должен делить ОЗУ с уже загруженной моделью Kaldi.
        self.model = None
        self.recognizer = None
        self._heavy_parked = False
        self._listen_thread = None
        self._asr_lock = threading.RLock()
        self._init_asr_engine()  
        self._init_tts_engine()  
         
    def load_lang_command_file(self, lang_code):  
        os.makedirs(COMMANDS_DIR, exist_ok=True)  
        file_path = os.path.join(COMMANDS_DIR, f"{lang_code}.command")  
        if os.path.exists(file_path):  
            try:  
                with open(file_path, "r", encoding="utf-8") as f:  
                    data = json.load(f)  
                    if "commands" in data:  
                        return data  
                    return {"commands": data, "responses": {}, "chitchat": {}}  
            except Exception as e:  
                print(f"[Commands Load Error] {file_path}: {e}")  
        return {"commands": {}, "responses": {}, "chitchat": {}}  
         
    def save_lang_command_file(self, lang_code, data):  
        os.makedirs(COMMANDS_DIR, exist_ok=True)  
        file_path = os.path.join(COMMANDS_DIR, f"{lang_code}.command")  
        with open(file_path, "w", encoding="utf-8") as f:  
            json.dump(data, f, ensure_ascii=False, indent=4)  
         
    def load_all_commands(self):  
        self.command_data_ru = self.load_lang_command_file("ru")  
        self.command_data_en = self.load_lang_command_file("en")  
          
        self.commands_ru = self.command_data_ru.get("commands", {})  
        self.commands_en = self.command_data_en.get("commands", {})  
          
        self.commands = {}  
        self.commands.update(self.commands_ru)  
        self.commands.update(self.commands_en)  
          
        self.all_chitchat_triggers = {}  
        for cdata in [self.command_data_ru, self.command_data_en]:  
            chitchat_data = cdata.get("chitchat", {})  
            for intent, data in chitchat_data.items():  
                if intent not in self.all_chitchat_triggers:  
                    self.all_chitchat_triggers[intent] = []  
                self.all_chitchat_triggers[intent].extend(data.get("triggers", []))  
          
        self._report_command_conflicts()  
        self.rebuild_recognizer()
        return self.commands  
         
    def _find_duplicate_phrases(self):
        """Ищет фразы, которые ведут в несколько команд.

        Такие фразы — прямой источник «услышал не ту команду»: срабатывает та
        команда, которая встретилась раньше в файле, а не та, что имел в виду
        человек. Например, «включи музыку» была и у Яндекс Музыки, и у
        play/pause. Возвращает {фраза: [команды]}.
        """
        owners = {}
        for cmd_name, cmd_data in self.commands.items():
            for phrase in [cmd_name] + cmd_data.get("synonyms", []):
                norm = self._normalize_text(phrase)
                if not norm:
                    continue
                owners.setdefault(norm, [])
                if cmd_name not in owners[norm]:
                    owners[norm].append(cmd_name)
        return {phrase: names for phrase, names in owners.items() if len(names) > 1}

    def _report_command_conflicts(self):
        """Предупреждает о конфликтах команд в консоли и в чате HUD."""
        conflicts = self._find_duplicate_phrases()
        if not conflicts:
            return
        lines = [f"«{phrase}» → {', '.join(names)}" for phrase, names in sorted(conflicts.items())]
        print("[Команды] Одинаковые фразы у разных команд:")
        for line in lines:
            print(f"  - {line}")
        self.send_to_gui(
            self.t("ui_skills_tag", "🦊 Навыки"),
            self.t("warn_conflict_header", "Одинаковые фразы у разных команд (сработает первая):")
            + "\n" + "\n".join(f" • {l}" for l in lines),
        )


    # =====================================================================
    # РАСПОЗНАВАНИЕ РЕЧИ: ГРАММАТИКА, НОРМАЛИЗАЦИЯ, ДИАГНОСТИКА
    # =====================================================================

    @staticmethod
    def _normalize_text(text):
        """Приводит фразу к единому виду — и для grammar-режима Vosk, и для
        нечёткого сравнения: нижний регистр, ё→е, без пунктуации."""
        if not isinstance(text, str):
            return ""
        text = text.lower().replace("ё", "е")
        text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
        return re.sub(r"\s+", " ", text).strip()

    def _build_grammar_phrases(self):
        """Собирает словарь активного языка в список фраз для grammar-режима."""
        lang = self.config.get("asr_grammar_lang", self.config.get("language", "ru"))
        data = self.command_data_ru if lang == "ru" else self.command_data_en

        phrases = set()
        for name, cdata in data.get("commands", {}).items():
            phrases.add(name)
            phrases.update(cdata.get("synonyms", []))
        for cdata in data.get("chitchat", {}).values():
            phrases.update(cdata.get("triggers", []))
        phrases.add(self.config.get("wake_word", "лисичка"))
        phrases.update(self.config.get("wake_aliases", []))
        phrases.update(self.config.get("asr_grammar_extra", []) or [])

        cleaned, seen = [], set()
        for phrase in phrases:
            norm = self._normalize_text(phrase)
            if not norm or norm in seen:
                continue
            # Шаблоны со слотами в грамматику не годятся: Vosk — это список
            # готовых фраз, «серия {number}» с любым номером туда не вписать, а
            # нормализация превратила бы {number} в лишнее слово «number».
            if SLOT_TOKEN_RE.search(phrase):
                continue
            # Русская модель не знает латиницы — такие фразы Vosk всё равно
            # отбросит, поэтому не засоряем ими грамматику.
            if lang == "ru" and not re.search(r"[а-я]", norm):
                continue
            seen.add(norm)
            cleaned.append(norm)
        return cleaned

    def rebuild_recognizer(self):
        """Пересоздаёт распознаватель речи.

        В grammar-режиме декодер ограничен словарём команд: на коротких фразах
        это заметно точнее и в разы быстрее, но всё, что вне словаря, будет
        подогнано под ближайшую фразу словаря (либо вернётся как '[unk]').
        """
        if not getattr(self, "model", None):
            return

        with getattr(self, "_asr_lock", threading.RLock()):
            self._rebuild_recognizer_locked()

    def _rebuild_recognizer_locked(self):
        """Собирает KaldiRecognizer. Вызывается уже под ``_asr_lock``."""
        self.asr_mode = "open"
        self.asr_phrase_count = 0
        recognizer = None

        if self.config.get("asr_grammar", True):
            phrases = self._build_grammar_phrases()
            # Страховка: если словарь не стыкуется с моделью (например, английские
            # команды с русской моделью), grammar-режим сделает только хуже.
            if phrases:
                cyrillic = sum(1 for p in phrases if re.search(r"[а-я]", p))
                if cyrillic < len(phrases) * 0.5:
                    print("[Vosk] Словарь не соответствует модели — grammar-режим отключён")
                    phrases = []
            if phrases:
                grammar = json.dumps(phrases + ["[unk]"], ensure_ascii=False)
                try:
                    SetLogLevel(-1)
                    recognizer = KaldiRecognizer(self.model, 16000, grammar)
                    self.asr_mode = "grammar"
                    self.asr_phrase_count = len(phrases)
                except Exception as e:
                    print(f"[Vosk Grammar Error]: {e}")
                finally:
                    SetLogLevel(0)

        if recognizer is None:
            recognizer = KaldiRecognizer(self.model, 16000)

        self._apply_vosk_endpointer(recognizer)
        self.recognizer = recognizer
        self._reset_vosk_vad()
        print(f"[Vosk] Распознавание: {self.asr_mode}, фраз в грамматике: {self.asr_phrase_count}")

    def _vosk_silence_s(self):
        """Секунды тишины до конца фразы — из тех же настроек, что у Whisper."""
        return max(0.5, float(self.config.get("vad_silence_ms", 1400)) / 1000.0)

    def _apply_vosk_endpointer(self, recognizer):
        """Растягивает встроенный endpoint Vosk, если API это умеет.

        Старые сборки vosk метода не имеют — тогда границу держит только
        EnergyVad в `_handle_audio_block`.
        """
        silence_s = self._vosk_silence_s()
        max_s = max(20.0, float(self.config.get("vad_max_utterance_s", 18)))
        delays = getattr(recognizer, "SetEndpointerDelays", None)
        if callable(delays):
            try:
                # t_start_max, t_end, t_max (секунды)
                delays(5.0, silence_s, max_s)
                return
            except TypeError:
                try:
                    delays(silence_s, max_s)
                    return
                except Exception:
                    pass
            except Exception:
                pass
        mode = getattr(recognizer, "SetEndpointerMode", None)
        if callable(mode):
            try:
                # 2 ≈ ×1.5 к min-trailing-silence из model.conf
                mode(2)
            except Exception:
                pass

    def _reset_vosk_vad(self):
        """Сбрасывает или создаёт энергетический VAD для пути Vosk."""
        silence_ms = int(self.config.get("vad_silence_ms", 1400))
        energy_factor = float(self.config.get("vad_energy_factor", 2.2))
        max_utterance_s = float(self.config.get("vad_max_utterance_s", 18))
        vad = getattr(self, "vosk_vad", None)
        if (
            vad is None
            or vad.silence_limit_ms != silence_ms
            or abs(vad.energy_factor - energy_factor) > 1e-6
            or vad.max_utterance_ms != int(max_utterance_s * 1000)
        ):
            self.vosk_vad = EnergyVad(
                silence_ms=silence_ms,
                energy_factor=energy_factor,
                max_utterance_s=max_utterance_s,
            )
        else:
            vad.reset()

    def _log_asr(self, message):
        """Единая точка логирования выбора и работы движка распознавания."""
        print(f"[ASR] {message}")
        try:
            self.send_to_gui(self.t("ui_mic_tag"), message)
        except Exception:
            pass

    def _asr_engine_fingerprint(self):
        """Отпечаток выбранного STT: при совпадении модель заново не грузим."""
        want = str(self.config.get("asr_engine", "auto")).lower().strip()
        if want in ("whisper_gpu", "gpu"):
            want = "whisper"
        if want == "vosk" or not self.config.get("full_mode", False):
            return ("vosk",)
        if want not in _WHISPER_ENGINE_PRESETS:
            return ("vosk",)
        device, model_name = _WHISPER_ENGINE_PRESETS[want]
        if not model_name:
            model_name = self.config.get("whisper_model", "large-v3-turbo")
        return (
            want,
            device,
            str(model_name),
            str(self.config.get("whisper_compute_type") or ""),
            str(self.config.get("language") or "ru"),
        )

    def _unload_whisper(self):
        """Снимает текущий Whisper с CPU/GPU, если он был загружен."""
        old = getattr(self, "whisper", None)
        self.whisper = None
        if old is None:
            return
        try:
            old.unload()
        except Exception as exc:
            print(f"[ASR] Не удалось выгрузить Whisper: {exc}")
            if asr_whisper is not None:
                asr_whisper.release_memory()

    def _init_asr_engine(self):
        """Выбирает движок распознавания и при нехватке железа откатывается на Vosk.

        ``auto`` / ``whisper`` — GPU, ``whisper_cpu`` — turbo на CPU,
        ``whisper_medium_cpu`` / ``whisper_small_cpu`` — medium/small на CPU.
        Все варианты Whisper только в полном режиме; базовый всегда на Vosk.
        При смене варианта прежняя модель сначала выгружается из ОЗУ.
        """
        fingerprint = self._asr_engine_fingerprint()
        same = fingerprint == getattr(self, "_asr_engine_fingerprint", None)
        if same and fingerprint == ("vosk",) and getattr(self, "asr_engine", None) == "vosk":
            if self.model is None:
                self._ensure_vosk_model()
            return
        if (
            same
            and getattr(self, "asr_engine", None) == "whisper"
            and getattr(self, "whisper", None) is not None
            and not getattr(self.whisper, "load_error", None)
        ):
            return

        self._unload_whisper()
        self.asr_engine = "vosk"
        self.whisper_reason = "Vosk (CPU)"
        self._asr_engine_fingerprint = ("vosk",)

        if asr_whisper is None:
            self._log_asr("Модуль whisper недоступен, работаю на Vosk (CPU)")
            self._ensure_vosk_model()
            return

        want = str(self.config.get("asr_engine", "auto")).lower().strip()
        if want in ("whisper_gpu", "gpu"):
            want = "whisper"
        if want == "vosk":
            self._log_asr("Движок задан вручную: Vosk (CPU)")
            self._ensure_vosk_model()
            return

        # Базовый режим: whisper не участвует, как бы ни просил конфиг.
        if not self.config.get("full_mode", False):
            self._log_asr("Базовый режим: распознавание на Vosk (CPU)")
            self._ensure_vosk_model()
            return

        if want not in _WHISPER_ENGINE_PRESETS:
            self._log_asr(f"Неизвестный движок «{want}» — работаю на Vosk (CPU)")
            self._ensure_vosk_model()
            return

        available, reason = asr_whisper.whisper_available()
        if not available:
            self._log_asr(f"{reason} — работаю на Vosk (CPU)")
            self._ensure_vosk_model()
            return

        device, model_name = _WHISPER_ENGINE_PRESETS[want]
        if not model_name:
            model_name = self.config.get("whisper_model", "large-v3-turbo")

        if device == "cuda":
            cuda = asr_whisper.cuda_info()
            if not cuda:
                self._log_asr("CUDA не найдена — работаю на Vosk (CPU)")
                self._ensure_vosk_model()
                return

            min_vram = int(self.config.get("whisper_min_vram_mb", 3000))
            vram = int(cuda.get("vram_mb") or 0)
            if vram and vram < min_vram:
                self._log_asr(
                    f"Видеокарта слабая: {cuda.get('name', 'GPU')} {vram} МБ "
                    f"< {min_vram} МБ — работаю на Vosk (CPU)"
                )
                self._ensure_vosk_model()
                return
            compute = (
                self.config.get("whisper_compute_type")
                or asr_whisper.choose_compute_type(cuda)
            )
            where = f"{cuda.get('name', 'CUDA')} {vram} МБ"
        else:
            preferred = str(self.config.get("whisper_compute_type") or "").strip()
            compute = asr_whisper.choose_compute_type_cpu(preferred or "int8")
            where = "CPU"

        self.whisper = asr_whisper.WhisperStreamRecognizer(
            model_name=model_name,
            device=device,
            compute_type=compute,
            language=self.config.get("language", "ru"),
            silence_ms=self.config.get("vad_silence_ms", 1400),
            energy_factor=self.config.get("vad_energy_factor", 2.2),
            max_utterance_s=self.config.get("vad_max_utterance_s", 18),
            on_log=self._log_asr,
        )
        self.asr_engine = "whisper"
        self._asr_engine_fingerprint = fingerprint
        self.whisper_reason = f"Whisper «{model_name}» на {where} ({compute})"
        self._log_asr(
            f"Распознавание Whisper: {where}, {compute}, "
            f"модель {self.whisper.model_name} — гружу в фоне"
        )
        self.whisper.load_async()
        # Kaldi больше не нужен: две модели сразу — лишние сотни мегабайт.
        self._release_vosk_model()

    def _ensure_vosk_model(self):
        """Грузит модель Vosk, если её ещё нет в процессе."""
        with self._asr_lock:
            if getattr(self, "model", None) is not None and getattr(self, "recognizer", None) is not None:
                return True
            model_path = find_vosk_model_dir("model")
            if not model_path:
                self.send_to_gui("ОШИБКА", "Модель Vosk не найдена в папке 'model'!")
                self.model = None
                self.recognizer = None
                return False
            if self.model is None:
                print(f"[Vosk] Модель загружена: {model_path}")
                self.model = Model(model_path)
            self.rebuild_recognizer()
            return self.recognizer is not None

    def _release_vosk_model(self):
        """Снимает Kaldi с ОЗУ. Веса на диске остаются."""
        with self._asr_lock:
            self.recognizer = None
            model = getattr(self, "model", None)
            self.model = None
            vad = getattr(self, "vosk_vad", None)
            if vad is not None:
                vad.reset()
        if model is not None:
            del model
            gc.collect()

    def _park_heavy_models(self):
        """Сон: Whisper и локальный синтез не висят в ОЗУ до следующего пробуждения.

        Vosk, если он выбран, остаётся: модель маленькая, а пробуждение
        должно быть мгновенным. Библиотеки torch/ctranslate2, однажды
        импортированные, из процесса уже не выгружаются — уходят только веса.
        """
        parked = False
        if getattr(self, "whisper", None) is not None or getattr(self, "asr_engine", "") == "whisper":
            self._unload_whisper()
            self._asr_engine_fingerprint = None
            parked = True
        want_tts = str(self.config.get("tts_engine", "auto")).lower()
        if getattr(self, "_tts_engine_cache", None) or (
            want_tts in ("silero", "kokoro") and getattr(self, "tts_local_name", "")
        ):
            self._tts_setup_token = getattr(self, "_tts_setup_token", 0) + 1
            self._drop_tts_cache()
            self.tts_local_name = ""
            parked = True
        self._heavy_parked = parked

    def _fallback_to_vosk(self, why):
        """Аварийный откат на Vosk, если whisper не смог загрузиться."""
        self._log_asr(f"Whisper не запустился ({why}) — переключаюсь на Vosk (CPU)")
        self._unload_whisper()
        self.asr_engine = "vosk"
        self.whisper_reason = "Vosk (CPU)"
        self._asr_engine_fingerprint = ("vosk",)
        self._ensure_vosk_model()

    def _log_tts(self, message):
        """Единая точка логирования выбора и работы движка синтеза."""
        print(f"[TTS] {message}")
        try:
            self.send_to_gui(self.t("ui_voice_tag"), message)
        except Exception:
            pass

    def tts_models_dir(self):
        """Каталог локальных моделей синтеза из конфига."""
        if tts_local is None:
            return ""
        return tts_local.models_dir(self.config.get("tts_models_dir"))

    def _drop_tts_cache(self):
        """Выгружает все локальные TTS-движки из ОЗУ/VRAM и очищает кэш."""
        cache = getattr(self, "_tts_engine_cache", None) or {}
        self._tts_engine_cache = {}
        self.tts_local_engine = None
        for name, engine in list(cache.items()):
            if engine is None:
                continue
            try:
                if tts_local is not None:
                    tts_local.release_engine(engine)
                else:
                    close = getattr(engine, "close", None)
                    if callable(close):
                        close()
            except Exception as exc:
                print(f"[TTS] Не удалось выгрузить {name}: {exc}")
        if tts_local is not None:
            tts_local.release_memory()

    def _init_tts_engine(self):
        """Перенастраивает локальный синтез речи в фоне.

        Выбор движка требует импорта torch (чтобы понять, жива ли CUDA), а это
        секунды на Windows. Держать из-за этого окно приложения нельзя, поэтому
        сброс состояния делается сразу, а сама проба уходит в отдельный поток:
        пока он работает, озвучка идёт через edge-tts. Прежние Silero/kokoro
        снимаются с памяти до загрузки новых.
        """
        self._tts_setup_token = getattr(self, "_tts_setup_token", 0) + 1
        token = self._tts_setup_token
        self.tts_local_name = ""
        self._drop_tts_cache()
        self._tts_voice_fingerprint = self._local_voice_fingerprint()

        # Токен нужен, чтобы настройки, сохранённые дважды подряд, не гоняли
        # две настройки параллельно: побеждает последняя, ранняя выходит.
        threading.Thread(target=self._select_tts_engine, args=(token,), daemon=True).start()

    def _tts_setup_is_current(self, token):
        return token == self._tts_setup_token

    def _select_tts_engine(self, token):
        """Основной поток: ручной выбор → Silero (torch + CUDA) → kokoro (ONNX).

        Если недоступно ничего, озвучка остаётся сетевой: edge-tts, затем SAPI5.
        В базовом режиме локальный синтез не выбирается и модели не качаются.
        """
        if tts_local is None:
            self._log_tts("Модуль локального синтеза недоступен — работаю через edge-tts")
            return

        if not self.config.get("full_mode", False):
            self.tts_local_name = ""
            self._log_tts("Базовый режим: озвучка через edge-tts")
            return

        name, reason = tts_local.choose_engine(self.config, self.tts_models_dir())
        if not self._tts_setup_is_current(token):
            return

        # Модели, которых ещё нет, догружаем в фоне даже когда говорить прямо
        # сейчас может другой движок. Иначе на машине с CUDA Silero не появился
        # бы никогда: kokoro уже готов, и выбор всегда останавливался бы на нём.
        pending = tts_local.downloadable_engines(self.config, self.tts_models_dir())

        if name:
            self.tts_local_name = name
            self._log_tts(f"Локальный синтез: {name} — гружу модель в фоне")
            threading.Thread(
                target=self._preload_tts_engine, args=(name, token), daemon=True
            ).start()
        elif not pending:
            self._log_tts(f"Локальный синтез недоступен ({reason}) — работаю через edge-tts")
            return

        if pending:
            self._log_tts(f"Догружаю модели в фоне: {', '.join(pending)}")
            threading.Thread(
                target=self._prepare_tts_models, args=(pending, token), daemon=True
            ).start()

    def _prepare_tts_models(self, engines, token):
        """Разовая загрузка моделей. Любой сбой просто оставляет edge-tts."""
        loaded = False
        try:
            if "silero" in engines:
                loaded = tts_local.download_silero(
                    self.tts_models_dir(), progress=self._log_tts
                ) or loaded
            if "kokoro" in engines:
                loaded = tts_local.download_kokoro(
                    self.tts_models_dir(),
                    progress=self._log_tts,
                    prefer_quantized=bool(self.config.get("kokoro_prefer_quantized", False)),
                ) or loaded
        except Exception as exc:
            self._log_tts(f"Загрузка моделей не удалась: {exc}")

        if not self._tts_setup_is_current(token):
            return

        if loaded:
            self._select_tts_engine(token)
        else:
            self._log_tts("Модели загрузить не удалось — озвучка через edge-tts")

    def _preload_tts_engine(self, name, token):
        """Загружает модель в фоне, чтобы не задерживать появление окна."""
        engine = self._local_engine(name, token)
        if not self._tts_setup_is_current(token):
            return
        if engine is None:
            self._log_tts(f"{name} не запустился — перехожу на edge-tts")
            self.tts_local_name = ""
            return
        device = getattr(engine, "device_name", "") or "CPU"
        voice = getattr(engine, "speaker", "") or getattr(engine, "voice", "")
        self._log_tts(f"Локальный синтез готов: {name}, {device}, голос {voice or 'по умолчанию'}")

    def _local_engine(self, name, token=None):
        """Ленивое создание движка с кэшированием, включая неудачные попытки."""
        if tts_local is None:
            return None
        if name not in self._tts_engine_cache:
            engine = tts_local.create_engine(name, self.config, self.tts_models_dir())
            if token is not None and not self._tts_setup_is_current(token):
                # Настройки успели смениться, пока модель поднималась: выгружаем
                # сироту сразу, чтобы она не висела в ОЗУ до сборщика мусора.
                tts_local.release_engine(engine)
                return None
            self._tts_engine_cache[name] = engine
        return self._tts_engine_cache[name]

    def _tts_engine_order(self):
        """Очередь движков: локальный → edge-tts → SAPI5.

        Пока модель грузится в фоне, локальный движок вернёт None, и озвучка
        сразу уйдёт на edge-tts, а после загрузки начнёт работать локально.
        В базовом режиме локальных движков нет вовсе: auto — это сразу
        edge-tts, и модели не скачиваются.
        """
        want = str(self.config.get("tts_engine", "auto")).lower()
        if want == "pyttsx3":
            return ["pyttsx3"]
        if want == "edge-tts":
            return ["edge-tts", "pyttsx3"]

        if not self.config.get("full_mode", False):
            # Базовый режим: silero/kokoro не озвучивают, что бы ни было
            # в конфиге, и не подтягиваются фоновыми загрузками.
            return ["edge-tts", "pyttsx3"]

        # Только выбранный локальный движок. Второй (Silero+kokoro сразу)
        # держал бы в ОЗУ две модели, хотя говорит одна.
        if want in ("silero", "kokoro"):
            order = [want]
        elif self.tts_local_name in ("silero", "kokoro"):
            order = [self.tts_local_name]
        else:
            order = []
        return order + ["edge-tts", "pyttsx3"]

    def _local_voice_fingerprint(self):
        """Настройки, от которых зависит голос локального движка.

        Движок кэшируется вместе с голосом, поэтому смена этих настроек должна
        ронять кэш — иначе переключение Света↔Маша в настройках жило бы до
        перезапуска приложения.
        """
        return (
            str(self.config.get("silero_speaker", "")),
            str(self.config.get("kokoro_voice", "")),
            bool(self.config.get("kokoro_prefer_quantized", False)),
        )

    def _score_candidates(self, text):
        """Возвращает (лучшая команда, её счёт, счёт болталок)."""
        cmd_best, cmd_score = None, 0.0
        cmd_words = len(text.split())
        for cmd_name, cmd_data in self.commands.items():
            for phrase in [cmd_name] + cmd_data.get("synonyms", []):
                phrase_n = self._normalize_text(phrase)
                if not phrase_n:
                    continue
                score = fuzz.ratio(text, phrase_n)
                partial = fuzz.partial_ratio(phrase_n, text) if cmd_words >= len(phrase_n.split()) else 0
                final = max(score, partial)
                if final > cmd_score:
                    cmd_score, cmd_best = final, cmd_name

        chat_score = 0.0
        for triggers in self.all_chitchat_triggers.values():
            for trigger in triggers:
                trigger_n = self._normalize_text(trigger)
                if not trigger_n:
                    continue
                score = fuzz.ratio(text, trigger_n)
                partial = fuzz.partial_ratio(trigger_n, text) if cmd_words >= len(trigger_n.split()) else 0
                chat_score = max(chat_score, score, partial)

        return cmd_best, cmd_score, chat_score

    def _report_asr_debug(self, text):
        """Пишет в чат и консоль сырую гипотезу распознавания и лучшие
        совпадения — чтобы видеть, где именно теряется команда."""
        norm = self._normalize_text(text)
        cmd_best, cmd_score, chat_score = self._score_candidates(norm)
        alias, _ = self._extract_wake_and_command(norm)
        if getattr(self, "asr_engine", "vosk") == "whisper":
            mode = "W"
        elif getattr(self, "asr_mode", "off") == "grammar":
            mode = "G"
        else:
            mode = "O"
        info = (f'ASR[{mode}] "{text}" | оклик={alias or "—"} | '
                f'команда={cmd_best or "—"} {cmd_score:.0f} | болталка={chat_score:.0f}')
        print(f"[Kitsune ASR] {info}")
        self.send_to_gui("🔎 ASR", info)

    def save_command_definition(self, cmd_name, steps, synonyms=None, old_name=None, lang=None):
        if not lang:
            lang = self.config.get("language", "ru")
        target_data = self.command_data_ru if lang == "ru" else self.command_data_en
        target_cmds = target_data.setdefault("commands", {})

        if old_name and old_name in target_cmds and old_name != cmd_name:
            del target_cmds[old_name]

        # Переносим прочие поля прежнего определения: у команд со слотами
        # («включи саус парк серия {number}») есть секция slots, и редактор HUD
        # не должен терять её при сохранении.
        entry = dict(target_cmds.get(cmd_name) or {})
        entry["synonyms"] = synonyms if synonyms is not None else []
        entry["steps"] = steps
        target_cmds[cmd_name] = entry
        self.save_lang_command_file(lang, target_data)
        self.load_all_commands()

    def delete_command(self, cmd_name, lang=None):
        if not lang:
            lang = self.config.get("language", "ru")
        target_data = self.command_data_ru if lang == "ru" else self.command_data_en
        target_cmds = target_data.get("commands", {})
        if cmd_name in target_cmds:
            del target_cmds[cmd_name]
            self.save_lang_command_file(lang, target_data)
            self.load_all_commands()
            return True
        return False
         
    def add_command(self, cmd_name, step, lang=None):  
        if not lang:  
            lang = self.config.get("language", "ru")  
          
        target_data = self.command_data_ru if lang == "ru" else self.command_data_en  
        target_cmds = target_data.setdefault("commands", {})  
          
        if cmd_name not in target_cmds:  
            target_cmds[cmd_name] = {"synonyms": [], "steps": []}  
          
        target_cmds[cmd_name]["steps"].append(step)  
        self.save_lang_command_file(lang, target_data)  
        self.load_all_commands()  
         
    def get_command_response(self, key, default=""):  
        cur_lang = self.config.get("language", "ru")  
        target_data = self.command_data_ru if cur_lang == "ru" else self.command_data_en  
        resp_dict = target_data.get("responses", {})  
        if key in resp_dict:  
            return resp_dict[key]  
          
        other_data = self.command_data_en if cur_lang == "ru" else self.command_data_ru  
        other_resp = other_data.get("responses", {})  
        return other_resp.get(key, default)  
         
    def get_command_response_list(self, key):  
        val = self.get_command_response(key, [])  
        return val if isinstance(val, list) and val else ["OK"]  
         
    def reload_language(self, lang_code=None):  
        if lang_code:  
            self.config["language"] = lang_code  
            cur_voice = self.config.get("tts_voice", "")  
            if lang_code == "en" and cur_voice.startswith("ru-"):  
                self.config["tts_voice"] = "en-US-JennyNeural"  
                self.config["tts_pitch"] = "+32Hz"
                self.config["tts_rate_edge"] = "+12%"
            elif lang_code == "ru" and cur_voice.startswith("en-"):  
                self.config["tts_voice"] = "ru-RU-SvetlanaNeural"  
                self.config["tts_pitch"] = "+10Hz"
                self.config["tts_rate_edge"] = "+15%"
            self.save_config()  
         
        cur_lang = self.config.get("language", "ru")  
        self.lang = load_language_dict(cur_lang)  
        self.load_all_commands()  
        if getattr(self, "whisper", None):  
            self.whisper.language = cur_lang  
         
    def t(self, key, default=""):  
        return self.lang.get(key, default if default else key)  
         
    def _detect_system_voice(self):  
        try:  
            ctypes.windll.ole32.CoInitialize(None)  
            temp_engine = pyttsx3.init()  
            voices = temp_engine.getProperty("voices")  
            self.sapi_voices = {"ru": None, "en": None}  
            for voice in voices:  
                v_name = voice.name.lower()  
                if not self.sapi_voices["ru"] and any(k in v_name for k in ["irina", "elena", "tatiana", "pavel", "russian", "русский", "russia"]):  
                    self.sapi_voices["ru"] = voice.id  
                if not self.sapi_voices["en"] and any(k in v_name for k in ["david", "zira", "mark", "eva", "hazel", "english", "catherine", "george"]):  
                    self.sapi_voices["en"] = voice.id  
            try:
                temp_engine.stop()
            except Exception:
                pass
            del temp_engine  
            ctypes.windll.ole32.CoUninitialize()  
        except Exception as e:  
            print(f"[Voice Detection Error]: {e}")  
         
    def load_config(self):  
        defaults = DEFAULT_CONFIG.copy()
        if os.path.exists(CONFIG_FILE):  
            try:  
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:  
                    data = json.load(f)
            except Exception as e:
                # Битый конфиг (обрыв записи, чужая правка) не должен ронять
                # приложение целиком: работаем на умолчаниях и говорим об этом.
                print(f"[Config] Не читается, использую умолчания: {e}")
                return defaults
            if isinstance(data, dict):
                defaults.update(data)
            return defaults
        return defaults  

    @staticmethod
    def _coerce_config_types(config):
        """Приводит поля конфига к ожидаемым типам.

        config.json открыт для ручной правки, и строка в числовом поле или
        None вместо списка сейчас падает уже в рантайме (float("7"), len(None))
        в местах, далёких от причины. Молча исправляем то, что исправимо.
        """
        def _number(key, default, cast=float):
            try:
                config[key] = cast(config.get(key))
            except (TypeError, ValueError):
                print(f"[Config] {key}: неверное значение {config.get(key)!r}, ставлю {default}")
                config[key] = default

        _number("wake_timeout", 7.0)
        _number("asr_cmd_threshold", 70)
        _number("asr_chitchat_threshold", 70)
        _number("asr_chitchat_strong", 75)
        _number("whisper_min_vram_mb", 3000, int)
        _number("vad_silence_ms", 1400, int)
        _number("vad_max_utterance_s", 18)
        if not isinstance(config.get("asr_grammar_extra"), list):
            config["asr_grammar_extra"] = []
        if not isinstance(config.get("wake_aliases"), list):
            config["wake_aliases"] = []
        config["wake_word"] = str(config.get("wake_word") or "лисичка")
        config["language"] = str(config.get("language") or "ru")
        config["full_mode"] = bool(config.get("full_mode", False))
        provider = str(config.get("weather_provider") or "weatherapi").strip().lower()
        config["weather_provider"] = "wttr" if "wttr" in provider else "weatherapi"
        config["weather_location"] = str(config.get("weather_location") or "Долгопрудный").strip() or "Долгопрудный"
        config["weatherapi_key"] = str(config.get("weatherapi_key") or "").strip()
        if not config["full_mode"]:
            # Базовый режим — рамка: ручная правка конфига (whisper, silero)
            # не должна воскрешать тяжёлые движки. Полный режим, наоборот,
            # сохраняет пользовательский выбор как есть.
            apply_mode_defaults(config, False)
        return config

    def save_config(self):  
        # Запись через временный файл: прерванная прямая запись оставляла бы
        # битый config.json, который при следующем старте уже не прочитать.
        tmp = CONFIG_FILE + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:  
                json.dump(self.config, f, ensure_ascii=False, indent=4)
            os.replace(tmp, CONFIG_FILE)
        except Exception as e:
            print(f"[Config] Не удалось сохранить: {e}")
            try:
                os.remove(tmp)
            except OSError:
                pass
         
    def send_to_gui(self, sender, text):  
        if self.update_gui:  
            self.update_gui(sender, text)  
             
    def set_status(self, text, color="#8A798C"):  
        if self.update_status:  
            self.update_status(text, color)  
             
    def notify_sleep_change(self, is_never: bool):  
        if self.sleep_change_callback:  
            self.sleep_change_callback(is_never)  
             
    def _calc_spectrum(self, samples, sr=16000):  
        if len(samples) < 128:  
            return  
          
        window = np.hanning(len(samples))  
        fft_mag = np.abs(np.fft.rfft(samples * window))  
        freqs = np.fft.rfftfreq(len(samples), d=1.0 / sr)  
         
        bands = []  
        for i in range(self.num_bands):  
            idx = np.where((freqs >= self.band_edges[i]) & (freqs < self.band_edges[i + 1]))[0]  
            if len(idx) > 0:  
                bands.append(float(np.mean(fft_mag[idx])))  
            else:  
                bands.append(0.0)  
         
        arr = np.array(bands)  
        norm = np.clip((np.log10(arr + 1e-4) + 2.7) / 2.7, 0.0, 1.0)  
        self.latest_spectrum = norm.tolist()  
         
    async def _async_generate_edge_tts(self, text, voice, pitch="+0Hz", rate="+10%"):  
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)  
        audio_stream = bytearray()
        agen = communicate.stream()
        try:
            async for chunk in agen:
                if self.stop_speech_event.is_set():
                    return None
                if chunk["type"] == "audio":
                    audio_stream.extend(chunk["data"])
        finally:
            await agen.aclose()
        return bytes(audio_stream)

    async def _feed_edge_mp3(self, text, voice, pitch, rate, feeder):
        communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
        agen = communicate.stream()
        try:
            async for chunk in agen:
                if self.stop_speech_event.is_set():
                    break
                if chunk["type"] == "audio" and chunk.get("data"):
                    feeder.write(chunk["data"])
        finally:
            await agen.aclose()

    def _play_mp3_feeder(self, feeder, sr=24000):
        """Декодирует mp3 из feeder и играет с первого готового кадра."""
        player = _LivePcmPlayer(self, sr)
        gen = miniaudio.stream_any(
            feeder,
            source_format=miniaudio.FileFormat.MP3,
            output_format=miniaudio.SampleFormat.FLOAT32,
            nchannels=1,
            sample_rate=sr,
            frames_to_read=1600,
        )
        try:
            try:
                while player.samples == 0:
                    player.push(next(gen))
            except StopIteration:
                return False
            for samples in gen:
                if self.stop_speech_event.is_set():
                    break
                player.push(samples)
        finally:
            player.finish()
            gen.close()
        return player.samples > 0 and not self.stop_speech_event.is_set()
         
    def _play_audio_array(self, audio_float, sr):  
        cursor = 0  
        total_len = len(audio_float)  
        finished_event = threading.Event()  
         
        def stream_callback(outdata, frames, time_info, status):  
            nonlocal cursor  
            if self.stop_speech_event.is_set() or cursor >= total_len:  
                outdata.fill(0)  
                finished_event.set()  
                raise sd.CallbackStop  
              
            take = min(frames, total_len - cursor)  
            outdata[:take, 0] = audio_float[cursor:cursor + take]  
            if take < frames:  
                outdata[take:, 0] = 0.0  
              
            chunk = audio_float[cursor:cursor + take]  
            cursor += take  
            if len(chunk) > 0:  
                self._calc_spectrum(chunk, sr=sr)  
              
            if cursor >= total_len or self.stop_speech_event.is_set():  
                finished_event.set()  
                raise sd.CallbackStop  
         
        try:  
            with sd.OutputStream(samplerate=sr, channels=1, blocksize=1600, callback=stream_callback) as stream:  
                self.active_output_stream = stream  
                while not finished_event.is_set():  
                    if self.stop_speech_event.is_set():  
                        try:  
                            stream.abort()  
                        except Exception:  
                            pass  
                        break  
                    finished_event.wait(timeout=0.03)  
                self.active_output_stream = None  
        except Exception:  
            self.active_output_stream = None  
             
    def interrupt_speech(self):  
        self.stop_speech_event.set()  
          
        if self.active_output_stream:  
            try:  
                self.active_output_stream.abort()  
            except Exception:  
                pass  
            self.active_output_stream = None  
          
        while not self.tts_queue.empty():  
            try:  
                self.tts_queue.get_nowait()  
                self.tts_queue.task_done()  
            except (queue.Empty, ValueError):  
                break  
          
        self.is_speaking = False  
        self.current_speaking_text = ""  
        self.latest_spectrum = [0.0] * self.num_bands  
          
        while not self.audio_queue.empty():  
            try:  
                self.audio_queue.get_nowait()  
            except queue.Empty:  
                break  

        # Прервали — значит и незаконченная фраза больше не нужна.
        if self.whisper:
            self.whisper.reset()

        with self._asr_lock:
            recognizer = self.recognizer
            if recognizer:  
                try:  
                    recognizer.Result()  
                except Exception:  
                    pass  
         
    def _tts_worker_loop(self):
        while True:
            text = self.tts_queue.get()
            self.stop_speech_event.clear()
            self.is_speaking = True
            self.current_speaking_text = text
            # Микрофон закрывается до первой фонемы, а всё, что успело накопиться,
            # выбрасывается: своя реплика не должна попасть в распознавание.
            self._flush_audio_input()

            pitch_mod = self.config.get("tts_pitch", "+0Hz")
            rate_mod = self.config.get("tts_rate_edge", "+10%")
            played_successfully = False

            for engine_name in self._tts_engine_order():
                if self.stop_speech_event.is_set():
                    break
                try:
                    if engine_name in ("silero", "kokoro"):
                        played_successfully = self._speak_local(engine_name, text)
                    elif engine_name == "edge-tts":
                        played_successfully = self._speak_edge(text, pitch_mod, rate_mod)
                    elif engine_name == "pyttsx3":
                        played_successfully = self._speak_sapi(text)
                except Exception as err:
                    print(f"[TTS {engine_name}]: {err}")
                    played_successfully = False
                if played_successfully:
                    break

            self.latest_spectrum = [0.0] * self.num_bands
            self.current_speaking_text = ""
            time.sleep(0.05)
            self.is_speaking = False
            self._apply_echo_tail()
            self.tts_queue.task_done()

    def _apply_echo_tail(self):
        """Держит микрофон закрытым ещё MIC_ECHO_TAIL_MS после конца реплики.

        Хвост эха считается от КОНЦА реплики, а не от начала: раньше метка
        ставилась до синтеза, и у любой фразы длиннее хвоста микрофон
        открывался ещё звучащим эхом — ассистент слышал собственные слова.
        """
        if self.config.get("asr_mute_while_speaking", True):
            self.mic_resume_at = time.time() + MIC_ECHO_TAIL_MS / 1000.0

    def _speak_local(self, engine_name, text):
        """Локальный синтез (Silero или kokoro).

        Берём только уже загруженный движок: пока модель качается или
        поднимается в фоне, реплику лучше озвучить через edge-tts, чем
        задерживать ответ.
        """
        self._sync_local_voice()
        engine = self._tts_engine_cache.get(engine_name)
        if engine is None:
            return False

        def synthesize(piece):
            audio, sr = engine.synthesize(piece)
            if self.stop_speech_event.is_set() or audio is None or len(audio) == 0:
                return None
            return audio, int(sr)

        # Первая фраза начинает звучать, пока модель считает продолжение.
        return self._speak_synthesized_chunks(
            tts_local.split_for_early_playback(text), synthesize
        )

    def _sync_local_voice(self):
        """Роняет кэш движков, если в настройках сменился голос.

        Менять голос без пересоздания движок не умеет, поэтому при смене
        отпечатка старые Silero/kokoro выгружаются из памяти, а новый
        экземпляр поднимется лениво при следующей реплике.
        """
        if self._local_voice_fingerprint() == getattr(self, "_tts_voice_fingerprint", None):
            return
        self._tts_voice_fingerprint = self._local_voice_fingerprint()
        self._drop_tts_cache()

    def _speak_edge(self, text, pitch_mod, rate_mod):
        """Озвучка через edge-tts. False — нет сети или пришло прерывание.

        Поток играет первый кадр mp3, как только его можно декодировать.
        Если декодера нет, ответ сначала скачивается целиком, как раньше.
        """
        voice_name = self.config.get("tts_voice", "ru-RU-SvetlanaNeural")
        try:
            if miniaudio is not None:
                return self._speak_edge_streaming(text, voice_name, pitch_mod, rate_mod)
            return self._speak_edge_buffered(text, voice_name, pitch_mod, rate_mod)
        except Exception as e:
            print(f"[Edge-TTS Fallback]: {e}")
            self.send_to_gui("⚠️ Sound", self.t("ui_network_fallback"))
            return False

    def _speak_edge_streaming(self, text, voice, pitch_mod, rate_mod):
        feeder = _EdgeMp3Feeder(self.stop_speech_event)
        result = {"ok": False, "error": None}

        def play():
            try:
                result["ok"] = self._play_mp3_feeder(feeder)
            except Exception as exc:
                result["error"] = exc
            finally:
                feeder.finish()

        player = threading.Thread(target=play, daemon=True)
        player.start()
        try:
            asyncio.run(self._feed_edge_mp3(text, voice, pitch_mod, rate_mod, feeder))
        finally:
            feeder.finish()
            player.join()
        if self.stop_speech_event.is_set():
            return False
        if result["error"] is not None and not result["ok"]:
            raise result["error"]
        return result["ok"]

    def _speak_edge_buffered(self, text, voice, pitch_mod, rate_mod):
        audio_bytes = asyncio.run(
            self._async_generate_edge_tts(text, voice, pitch=pitch_mod, rate=rate_mod)
        )
        if self.stop_speech_event.is_set():
            return False
        if audio_bytes and len(audio_bytes) > 100:
            with io.BytesIO(audio_bytes) as bio:
                data, sr = sf.read(bio, dtype="float32")
                if data.ndim > 1:
                    data = data[:, 0]
                self._play_audio_array(data, sr)
                return True
        return False

    def _speak_synthesized_chunks(self, chunks, synthesize):
        """Синтезирует куски по очереди и играет с первого готового.

        ``synthesize(text)`` возвращает ``(float32 mono, sample_rate)`` или
        None. Следующий кусок считается, пока предыдущий уже звучит.
        """
        player = None
        try:
            for chunk in chunks:
                if self.stop_speech_event.is_set():
                    break
                try:
                    produced = synthesize(chunk)
                except Exception:
                    if player is None:
                        raise
                    break
                if not produced:
                    continue
                audio, sr = produced
                audio = np.asarray(audio, dtype=np.float32).reshape(-1)
                if audio.size == 0:
                    continue
                if tts_local is not None:
                    audio = tts_local.normalize_peak(audio)
                if player is None:
                    player = _LivePcmPlayer(self, int(sr))
                else:
                    player.push(np.zeros(max(1, int(player.sr * 0.04)), dtype=np.float32))
                player.push(audio)
        finally:
            if player is not None:
                player.finish()
        if player is None or self.stop_speech_event.is_set():
            return False
        return player.samples > 0

    def _speak_sapi(self, text):
        """Последний фолбэк: системный голос Windows через SAPI5.

        Каждая фраза пишется в wav и сразу ставится в воспроизведение,
        следующая в это время ещё синтезируется.
        """
        chunks = (
            tts_local.split_for_early_playback(text)
            if tts_local is not None
            else [text]
        )

        def synthesize(piece):
            if self.stop_speech_event.is_set():
                return None
            return self._render_sapi_chunk(piece)

        try:
            return self._speak_synthesized_chunks(chunks, synthesize)
        except Exception as err:
            print(f"[SAPI5 Error]: {err}")
            return False

    def _render_sapi_chunk(self, text):
        """Один фрагмент SAPI в float32. None — пусто, таймаут или прерывание."""
        temp_wav = os.path.join(
            tempfile.gettempdir(), f"fox_{os.getpid()}_{time.time_ns()}.wav"
        )
        box = {}

        def _render():
            try:
                ctypes.windll.ole32.CoInitialize(None)
                engine = pyttsx3.init()
                engine.setProperty("rate", self.config.get("tts_rate", 190))
                engine.setProperty("volume", 1.0)
                lang = self.config.get("language", "ru")
                chosen = self.sapi_voices.get(lang) or self.sapi_voices.get("ru") or self.sapi_voices.get("en")
                if chosen:
                    engine.setProperty("voice", chosen)
                engine.save_to_file(text, temp_wav)
                engine.runAndWait()
                engine.stop()
            except Exception as exc:
                box["error"] = exc
            finally:
                try:
                    ctypes.windll.ole32.CoUninitialize()
                except Exception:
                    pass

        worker = threading.Thread(target=_render, daemon=True)
        worker.start()
        worker.join(timeout=10.0)
        if box.get("error"):
            try:
                os.remove(temp_wav)
            except Exception:
                pass
            raise box["error"]
        if worker.is_alive() or self.stop_speech_event.is_set():
            try:
                os.remove(temp_wav)
            except Exception:
                pass
            return None
        if not os.path.exists(temp_wav) or os.path.getsize(temp_wav) <= 44:
            return None
        try:
            with wave.open(temp_wav, "rb") as wf:
                sr = wf.getframerate()
                channels = wf.getnchannels()
                raw = wf.readframes(wf.getnframes())
        finally:
            try:
                os.remove(temp_wav)
            except Exception:
                pass
        arr = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            arr = arr.reshape(-1, channels)[:, 0]
        return arr.astype(np.float32) / 32768.0, sr

    def speak(self, text):  
        speaker_label = self.t("ui_speaker_name", "🦊 Лисичка")  
        self.send_to_gui(speaker_label, text)  
        self.tts_queue.put(text)  
         
    def report_capabilities(self):  
        lang = self.config.get("language", "ru")  
        active_dict = self.commands_en if lang == "en" else self.commands_ru  
        active_cmds = list(active_dict.keys())  
        formatted_list = "\n".join([f" • {cmd}" for cmd in active_cmds])  
          
        header_template = self.get_command_response("skills_header", "Навыки ({count}):\n{list}")  
        header = header_template.format(count=len(active_cmds), list=formatted_list)  
        speech_text = self.get_command_response("skills_speak", "Все навыки выведены в терминал.")  
          
        self.send_to_gui(self.t("ui_skills_tag", "🦊 Навыки"), header)  
        self.speak(speech_text)  
         
    def report_power_mode(self):  
        is_never = os.path.exists(MARKER_FILE)  
        if is_never:  
            msg = self.get_command_response("pwr_status_never", "Сейчас активен режим чуткого дозора! Сон отключен, экран бодрствует, уруру!")  
        else:  
            msg = self.get_command_response("pwr_status_5min", "Сейчас активен режим дрёмы в норке, фырк.")  
        self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), msg)  
        self.speak(msg)  
         
    def _foreground_is_this_process(self):
        """True, если фокус у окна нашего процесса (главное / диалоги)."""
        try:
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            if not hwnd:
                return False
            pid = ctypes.c_ulong(0)
            ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value) == int(os.getpid())
        except Exception:
            return False

    def _cat_lock_hook(self, event):
        """Глобальный suppress-hook кото-режима.

        Блокирует клавиши в чужих приложениях, но пропускает ввод, когда
        фокус у Kitsune — иначе Ctrl+C/V и поля UI «глохнут» из-за WH_KEYBOARD_LL.
        callback должен вернуть True, чтобы событие прошло, False — подавить.
        """
        return self._foreground_is_this_process()

    def lock_keyboard(self):  
        if not self.keyboard_locked:  
            self.kb_hook = keyboard.hook(self._cat_lock_hook, suppress=True)  
            self.keyboard_locked = True  
            msg = self.get_command_response("cat_locked_msg", "Клавиатура заблокирована!")  
            self.send_to_gui(self.t("ui_cat_mode_tag", "🐾 Кото-режим"), msg)  
         
    def unlock_keyboard(self):  
        if self.keyboard_locked:  
            if self.kb_hook:  
                try:  
                    keyboard.unhook(self.kb_hook)  
                except Exception:  
                    keyboard.unhook_all()  
                self.kb_hook = None  
            else:  
                keyboard.unhook_all()  
            self.keyboard_locked = False  
            msg = self.get_command_response("cat_unlocked_msg", "Клавиатура разблокирована.")  
            self.send_to_gui(self.t("ui_cat_mode_tag", "🐾 Кото-режим"), msg)  
         
    def toggle_sleep_mode(self):  
        try:  
            if os.path.exists(MARKER_FILE):  
                subprocess.run("powercfg /change standby-timeout-ac 5", shell=True, check=True)  
                subprocess.run("powercfg /change standby-timeout-dc 5", shell=True, check=True)  
                subprocess.run("powercfg /change monitor-timeout-ac 5", shell=True, check=True)  
                subprocess.run("powercfg /change monitor-timeout-dc 5", shell=True, check=True)  
                os.remove(MARKER_FILE)  
                  
                self.notify_sleep_change(is_never=False)  
                self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), self.get_command_response("pwr_5min_msg"))  
                self.speak(self.get_command_response("pwr_5min_speak"))  
            else:  
                subprocess.run("powercfg /change standby-timeout-ac 0", shell=True, check=True)  
                subprocess.run("powercfg /change standby-timeout-dc 0", shell=True, check=True)  
                subprocess.run("powercfg /change monitor-timeout-ac 30", shell=True, check=True)  
                subprocess.run("powercfg /change monitor-timeout-dc 30", shell=True, check=True)  
                with open(MARKER_FILE, "w") as f:  
                    f.write("")  
                  
                self.notify_sleep_change(is_never=True)  
                self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), self.get_command_response("pwr_never_msg"))  
                self.speak(self.get_command_response("pwr_never_speak"))  
        except Exception as e:  
            self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), str(e))  
         
    def set_sleep_never(self):  
        try:  
            subprocess.run("powercfg /change standby-timeout-ac 0", shell=True, check=True)  
            subprocess.run("powercfg /change standby-timeout-dc 0", shell=True, check=True)  
            subprocess.run("powercfg /change monitor-timeout-ac 30", shell=True, check=True)  
            subprocess.run("powercfg /change monitor-timeout-dc 30", shell=True, check=True)  
            if not os.path.exists(MARKER_FILE):  
                with open(MARKER_FILE, "w") as f:  
                    f.write("")  
            self.notify_sleep_change(is_never=True)  
            self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), self.get_command_response("pwr_never_msg"))  
        except Exception as e:  
            self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), str(e))  
         
    def set_sleep_5min(self):  
        try:  
            subprocess.run("powercfg /change standby-timeout-ac 5", shell=True, check=True)  
            subprocess.run("powercfg /change standby-timeout-dc 5", shell=True, check=True)  
            subprocess.run("powercfg /change monitor-timeout-ac 5", shell=True, check=True)  
            subprocess.run("powercfg /change monitor-timeout-dc 5", shell=True, check=True)  
            if os.path.exists(MARKER_FILE):  
                os.remove(MARKER_FILE)  
            self.notify_sleep_change(is_never=False)  
            self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), self.get_command_response("pwr_5min_msg"))  
        except Exception as e:  
            self.send_to_gui(self.t("ui_pwr_tag", "⚡ Питание"), str(e))  
         
    def _audio_callback(self, indata, frames, time_info, status):  
        # Своя речь — не команда. Пока ассистент говорит (и пока не выветрилось
        # эхо в комнате), микрофон не слушаем: иначе он распознаёт собственную
        # озвучку и «слышит» то, чего вы не говорили.
        if self.config.get("asr_mute_while_speaking", True):
            if self.is_speaking or time.time() < self.mic_resume_at:
                return

        samples = np.frombuffer(indata, dtype=np.int16).astype(np.float32) / 32768.0  
        self._calc_spectrum(samples, sr=16000)  
        try:  
            self.audio_queue.put_nowait(bytes(indata))  
        except queue.Full:  
            # Не молчим об этом: потерянный блок — это потерянное начало фразы.
            self.audio_dropped_blocks += 1  
            if self.audio_dropped_blocks - self.audio_drop_reported >= 100:  
                self.audio_drop_reported = self.audio_dropped_blocks  
                self.send_to_gui(  
                    self.t("ui_mic_tag"),  
                    self.t("warn_audio_dropped", "Пропущено аудио: {count} блоков — распознавание не успевает").format(count=self.audio_dropped_blocks),
                )  
         
    def _flush_audio_input(self):  
        """Сбрасывает накопленное аудио перед репликой ассистента.  

        Одного запрета записи мало: к моменту, когда он заговорит, в очереди уже  
        лежат последние секунды речи, а в VAD whisper — незакрытая фраза. Всё это  
        иначе распознается как продолжение диалога.  
        """  
        if not self.config.get("asr_mute_while_speaking", True):  
            return  
        # Пока is_speaking=True, колбэк микрофона молчит сам; точное время
        # открытия после реплики ставит _tts_worker_loop, когда звук закончился.
        while not self.audio_queue.empty():  
            try:  
                self.audio_queue.get_nowait()  
            except queue.Empty:  
                break  
        if self.whisper:  
            self.whisper.reset()  
        if getattr(self, "vosk_vad", None):
            self.vosk_vad.reset()
        with self._asr_lock:
            recognizer = self.recognizer
            if recognizer:  
                try:  
                    recognizer.Result()  
                except Exception:  
                    pass  
         
    def execute_scenario(self, steps, slots=None):  
        """Выполняет шаги команды, подставляя значения слотов в action value."""
        substitutions = dict(slots or {})
        self.set_status(self.t("ui_status_conjuring"), "#FF8C00")  
        for step in steps:  
            action = step.get("action")  
            val = self._fill_slots(step.get("value", ""), substitutions)  
            try:  
                if action == "speak":  
                    if "|" in val:
                        variants = [v.strip() for v in val.split("|") if v.strip()]
                        if variants:
                            self.speak(random.choice(variants))
                    else:
                        self.speak(val)
                elif action in ["get_power_mode", "check_power_mode"]:  
                    self.report_power_mode()  
                elif action == "show_full_window":  
                    if self.window_action:  
                        self.window_action("full")  
                elif action == "show_mini_window":  
                    if self.window_action:  
                        self.window_action("mini")  
                elif action == "hide_to_tray":  
                    if self.window_action:  
                        self.window_action("tray")  
                elif action == "list_commands":  
                    self.report_capabilities()  
                elif action == "pause":  
                    try:
                        time.sleep(float(val if val else 1))
                    except (ValueError, TypeError):
                        time.sleep(1)  
                elif action == "fullscreen":  
                    pyautogui.press("f11")  
                elif action == "lock_keyboard":  
                    self.lock_keyboard()  
                elif action == "unlock_keyboard":  
                    self.unlock_keyboard()  
                elif action == "toggle_sleep_mode":  
                    self.toggle_sleep_mode()  
                elif action == "sleep_never":  
                    self.set_sleep_never()  
                elif action == "sleep_5min":  
                    self.set_sleep_5min()  
                elif action == "open_url":  
                    webbrowser.open(val)  
                elif action == "run_cmd":  
                    subprocess.Popen(val, shell=True)  
                elif action == "hotkey":  
                    keys = [k.strip().lower() for k in val.split("+")]  
                    pyautogui.hotkey(*keys)  
                elif action == "type_text":  
                    pyperclip.copy(val)  
                    pyautogui.hotkey("ctrl", "v")  
                elif action in ["set_volume", "volume_set"]:  
                    try:  
                        val_clean = str(val).replace("%", "").strip()  
                        val_int = int(val_clean) if val_clean.isdigit() else 20  
                        val_int = max(0, min(100, val_int))  
                        for _ in range(50):  
                            pyautogui.press("volumedown")  
                        for _ in range(round(val_int / 2)):  
                            pyautogui.press("volumeup")  
                    except Exception as e:  
                        print(f"[Set Volume Error]: {e}")  
                elif action == "volume_up":  
                    count = int(val) if str(val).isdigit() else 5  
                    for _ in range(count):  
                        pyautogui.press("volumeup")  
                elif action == "volume_down":  
                    count = int(val) if str(val).isdigit() else 5  
                    for _ in range(count):  
                        pyautogui.press("volumedown")  
                elif action == "volume_mute":  
                    pyautogui.press("volumemute")  
                elif action == "media_play_pause":  
                    pyautogui.press("playpause")  
                elif action == "media_next":  
                    pyautogui.press("nexttrack")  
                elif action == "media_prev":  
                    pyautogui.press("prevtrack")  
                elif action == "screenshot":  
                    desktop = get_desktop_dir()
                    filename = desktop / f"Fox_Shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"  
                    pyautogui.screenshot(str(filename))  
                    msg = self.get_command_response("screenshot_msg", "Снимок экрана сохранен: {filename}").format(filename=filename.name)  
                    self.send_to_gui(self.t("ui_screenshot_tag", "🐾 След"), msg)  
                elif action == "crypto_rate":  
                    self._report_crypto_rate(val)
                elif action == "fiat_rate":
                    self._report_fiat_rate(val)
                elif action == "weather":
                    self._report_weather(val)
                elif action == "lock_pc":  
                    ctypes.windll.user32.LockWorkStation()  
                elif action == "sleep_pc":  
                    os.system("rundll32.exe powrprof.dll,SetSuspendState 0,1,0")  
                  
                time.sleep(0.02)  
            except Exception as e:  
                self.send_to_gui("Error", f"{action}: {str(e)}")  
          
        self.reset_wake_state()  
         
    def reset_wake_state(self):  
        self.is_active_session = False  
        kb_info = self.t("ui_cat_shield_badge") if self.keyboard_locked else ""  
          
        if self.config.get("require_wake_word", True):  
            resting_msg = self.t("ui_status_resting").format(  
                wake_word=self.config.get('wake_word', 'лисичка'),  
                cat_shield=kb_info  
            )  
            self.set_status(resting_msg, "#8A798C")  
        else:  
            free_msg = self.t("ui_status_free_listen").format(cat_shield=kb_info)  
            self.set_status(free_msg, "#FF8C00")  
         
    def _extract_wake_and_command(self, raw_text):  
        raw = self._normalize_text(raw_text)  
        if not raw:  
            return None, ""  
          
        aliases = [self._normalize_text(self.config.get("wake_word", "лисичка"))] + [self._normalize_text(a) for a in self.config.get("wake_aliases", [])]  
        aliases = sorted((a for a in set(aliases) if a), key=len, reverse=True)  
          
        for alias in aliases:  
            if raw.startswith(alias):  
                remainder = raw[len(alias):].strip()  
                return alias, remainder  
              
            words = raw.split()  
            if alias in words:  
                idx = words.index(alias)  
                remainder = " ".join(words[idx + 1:]).strip()  
                return alias, remainder  
              
            if len(raw) >= len(alias):  
                chunk = raw[:len(alias) + 2]  
                if fuzz.ratio(alias, chunk) >= 75:  
                    remainder = raw[len(alias):].strip()  
                    return alias, remainder  
          
        return None, ""  
         
    def _match_chitchat(self, text):  
        best_score = 0  
        matched_intent = None  
        words_count = len(text.split())  
         
        for intent, triggers in self.all_chitchat_triggers.items():  
            for trigger in triggers:  
                trigger_n = self._normalize_text(trigger)
                if not trigger_n:
                    continue
                score = fuzz.ratio(text, trigger_n)  
                partial = fuzz.partial_ratio(trigger_n, text) if words_count >= len(trigger_n.split()) else 0  
                final = max(score, partial)  
                if final > best_score:  
                    best_score = final  
                    matched_intent = intent  
         
        if best_score >= float(self.config.get("asr_chitchat_threshold", 70)) and matched_intent:  
            if matched_intent == "capabilities":  
                return "CAPABILITIES_TRIGGER", best_score  
              
            cur_lang = self.config.get("language", "ru")  
            target_data = self.command_data_ru if cur_lang == "ru" else self.command_data_en  
            active_chitchat = target_data.get("chitchat", {})  
            if matched_intent in active_chitchat:  
                responses = active_chitchat[matched_intent].get("responses", [])  
                if responses:  
                    return random.choice(responses), best_score  
         
        return None, best_score  
         
    # -----------------------------------------------------------------
    # Команды со слотами: «включи саус парк серия 312», «курс биткоина»
    # -----------------------------------------------------------------

    def _parse_slot(self, name, spec, text):
        """Превращает хвост фразы в подстановки для шагов команды.

        Возвращает словарь вроде {"number": "312", "digits": "три один два"}
        или None, если хвост на значение слота не похож.
        """
        kind = (spec or {}).get("type", "number")
        if kind == "number":
            number = parse_slot_number(text, tolerate_noise=True)
            if not number:
                return None
            lang = self.config.get("language", "ru")
            return {"number": number, "digits": spoken_digits(number, lang)}
        if kind == "coin":
            ticker = crypto_rates.resolve_coin(text) if crypto_rates else None
            if not ticker:
                return None
            return {"coin": ticker}
        if kind == "currency":
            # Хвост может нести и дату: «доллара на 15 марта» / «евро на вчера».
            if cbr_rates is None:
                return None
            code, day = cbr_rates.parse_currency_query(text)
            if not code:
                return None
            if day is None:
                # Валюта понятна, дата — нет: передаём маркер, worker скажет отдельно.
                return {"currency": code, "date": "", "date_iso": "bad"}
            return {
                "currency": code,
                "date": day.strftime("%d.%m.%Y"),
                "date_iso": day.isoformat(),
            }
        if kind == "weather":
            text = (text or "").strip()
            if not text:
                return None
            return {"query": text}
        return None

    def _split_slot(self, words, prefix, suffix):
        """Ищет шаблон во фразе и возвращает (хвост, счёт) или (None, 0).

        Шаблон сравнивается скользящим окном в несколько слов, и окно может
        стоять не в самом начале: «включи саус парк 312» должно разобрать как
        шаблон «саус парк» плюс номер. Значение слота — всегда конец фразы,
        поэтому за окном должно остаться хоть одно слово.

        Шаблон бывает и без префикса («{coin} курс», «{coin} price»): тогда
        ищется суффикс, а слот — всё, что перед ним.
        """
        prefix_words = prefix.split()
        suffix_words = suffix.split()
        if not prefix_words and not suffix_words:
            return None, 0

        best = None  # (счёт, индекс конца шаблона)

        if not prefix_words:
            # Только суффикс: окно суффикса идёт по всей фразе, слот — до него.
            size = len(suffix_words)
            for start in range(0, len(words) - size + 1):
                if start == 0:
                    continue  # перед суффиксом должно быть значение слота
                score = fuzz.ratio(" ".join(words[start:start + size]), suffix)
                if best is None or score > best[0]:
                    best = (score, start)
        else:
            base = len(prefix_words)
            for size in (base, base + 1, base - 1):
                if size < 1:
                    continue
                for start in range(0, len(words) - size + 1):
                    if start + size >= len(words):
                        continue  # за шаблоном должно остаться значение слота
                    score = fuzz.ratio(" ".join(words[start:start + size]), prefix)
                    if best is None or score > best[0]:
                        best = (score, start + size)
        if best is None or best[0] < SLOT_PREFIX_THRESHOLD:
            return None, 0

        # В префиксной ветке best[1] — конец шаблона: хвост после него.
        # В суффиксной (best = (score, start)) best[1] — начало суффикса,
        # и хвост — всё, что до него; сам суффикс туда не входит, вычитать
        # его второй раз нельзя.
        tail = words[best[1]:] if prefix_words else words[:best[1]]
        if prefix_words and suffix_words:
            if len(tail) <= len(suffix_words):
                return None, 0
            literal = " ".join(tail[-len(suffix_words):])
            suffix_score = fuzz.ratio(literal, suffix)
            if suffix_score < SLOT_PREFIX_THRESHOLD:
                return None, 0
            tail = tail[:-len(suffix_words)]
            # Итоговый скор учитывает обе литеральные части шаблона.
            best = ((best[0] + suffix_score) / 2, best[1])
        tail_text = " ".join(tail).strip()
        return (tail_text or None), best[0]

    def _match_slot_command(self, command_text):
        """Ищет команду-шаблон со слотом. Возвращает (имя команды, подстановки).

        Общий нечёткий перебор тут не годится: фраза с номером серии не совпадёт
        целиком ни с одной фразой словаря, зато её перетянет короткая команда без
        номера («включи саус парк»). Поэтому шаблоны со слотами разбираются
        отдельно и раньше общего перебора.
        """
        words = (command_text or "").split()
        if not words:
            return None, {}

        best = None  # (счёт, имя команды, подстановки)
        for cmd_name, cmd_data in self.commands.items():
            slots = cmd_data.get("slots") or {}
            if not slots:
                continue
            for phrase in [cmd_name] + cmd_data.get("synonyms", []):
                match = SLOT_TOKEN_RE.search(phrase)
                if not match:
                    continue
                slot_name = match.group(1)
                # Слот ищем в исходной фразе, а нормализуем уже половинки:
                # нормализация съела бы фигурные скобки.
                prefix = self._normalize_text(phrase[: match.start()])
                suffix = self._normalize_text(phrase[match.end():])
                tail, match_score = self._split_slot(words, prefix, suffix)
                if tail is None:
                    continue
                values = self._parse_slot(slot_name, slots.get(slot_name), tail)
                if not values:
                    continue
                # Ранжируем по совпадению с тем окном, где шаблон реально нашёлся,
                # а не с первыми словами фразы: шаблон может стоять в середине.
                if best is None or match_score > best[0]:
                    best = (match_score, cmd_name, values)

        if best is None:
            return None, {}
        return best[1], best[2]

    @staticmethod
    def _fill_slots(value, substitutions):
        """Подставляет слоты в value шага: «.../episode/{number}/» → «.../episode/312/»."""
        if not substitutions or not isinstance(value, str) or "{" not in value:
            return value
        for name, replacement in substitutions.items():
            value = value.replace("{" + name + "}", str(replacement))
        return value

    def _speak_variants(self, text):
        """Озвучивает одну из фраз, разделённых «|», — как это делает шаг speak."""
        variants = [part.strip() for part in str(text).split("|") if part.strip()]
        self.speak(random.choice(variants) if variants else str(text))

    def _report_crypto_rate(self, ticker):
        """Достаёт курс и озвучивает его в фоне.

        Сеть — это секунды, а сценарий выполняется в потоке распознавания (или
        в потоке интерфейса, если команду нажали в HUD), поэтому запрос уходит
        в отдельный поток, а команда не ждёт его ответа.
        """
        if crypto_rates is None:
            self._speak_variants(self.get_command_response(
                "crypto_fail", "Не смогла достать курс, похоже, сеть шалит, фырк."
            ))
            return

        ticker = (ticker or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{2,12}", ticker):
            # Либо слот не разрешился («курс крипты» без названия монеты), либо
            # в редакторе HUD монету написали словами — пробуем понять слово.
            ticker = crypto_rates.resolve_coin(ticker) or ""
        if not ticker:
            self._speak_variants(self.get_command_response(
                "crypto_ask", "Уточни, курс какой монеты смотрим? Биткоин, эфириум, солана?"
            ))
            return

        self.set_status(self.t("ui_status_crypto", "🦊 Смотрю курсы..."), "#FF8C00")
        threading.Thread(target=self._crypto_worker, args=(ticker,), daemon=True).start()

    def _crypto_worker(self, ticker):
        lang = self.config.get("language", "ru")
        try:
            parts = crypto_rates.get_rate(ticker, lang)
        except Exception as e:
            print(f"[Crypto] {ticker}: {e}")
            parts = None

        if not parts:
            message = self.get_command_response(
                "crypto_fail", "Не смогла достать курс, похоже, сеть шалит, фырк."
            )
            self.send_to_gui(self.t("ui_crypto_tag", "📈 Курс"), message)
            self._speak_variants(message)
            return

        if parts.get("has_usd") and parts.get("has_rub"):
            key = "crypto_line"
        elif parts.get("has_rub"):
            key = "crypto_line_rub"
        else:
            key = "crypto_line_usd"

        template = self.get_command_response(
            key, "{name}: {usd} {usd_word}, а в рублях {rub} {rub_word}."
        )
        try:
            line = template.format(**parts)
        except (KeyError, IndexError, ValueError):
            line = f"{parts['name']}: {parts['usd']} {parts['usd_word']}"
        self.send_to_gui(self.t("ui_crypto_tag", "📈 Курс"), line)
        self._speak_variants(line)

    def _report_fiat_rate(self, value):
        """Официальный курс ЦБ на дату. Значение шага: ``USD`` или ``USD|2024-03-15``.

        Дата опциональна: пустая или отсутствующая — сегодня. Маркер ``bad``
        значит, что валюта распознана, а дата в хвосте фразы — нет.
        """
        if cbr_rates is None:
            self._speak_variants(self.get_command_response(
                "fiat_fail", "Не смогла достать курс ЦБ, похоже, сеть шалит, фырк."
            ))
            return

        raw = (value or "").strip()
        code, date_iso = raw, ""
        if "|" in raw:
            code, date_iso = [part.strip() for part in raw.split("|", 1)]

        code = code.upper()
        if not re.fullmatch(r"[A-Z]{3}", code):
            parsed_code, parsed_day = cbr_rates.parse_currency_query(raw)
            code = parsed_code or ""
            if parsed_day is not None:
                date_iso = parsed_day.isoformat()
            elif parsed_code and not date_iso:
                date_iso = "bad"

        if not code:
            self._speak_variants(self.get_command_response(
                "fiat_ask",
                "Уточни валюту: доллар, евро, юань, фунт? Можно и дату: курс доллара на вчера.",
            ))
            return
        if date_iso == "bad":
            self._speak_variants(self.get_command_response(
                "fiat_bad_date",
                "Дату не разобрала, уруру. Скажи «вчера», «15 марта» или «15.03.2024».",
            ))
            return

        self.set_status(self.t("ui_status_fiat", "🦊 Смотрю курс ЦБ..."), "#FF8C00")
        threading.Thread(
            target=self._fiat_worker, args=(code, date_iso), daemon=True
        ).start()

    def _fiat_worker(self, code, date_iso):
        from datetime import date as _date

        lang = self.config.get("language", "ru")
        on_date = None
        if date_iso:
            try:
                on_date = _date.fromisoformat(date_iso)
            except ValueError:
                on_date = None
        try:
            parts = cbr_rates.get_rate(code, on_date=on_date, lang=lang)
        except Exception as e:
            print(f"[CBR] {code} @ {date_iso}: {e}")
            parts = None

        if not parts:
            message = self.get_command_response(
                "fiat_fail", "Не смогла достать курс ЦБ, похоже, сеть шалит, фырк."
            )
            self.send_to_gui(self.t("ui_fiat_tag", "🏦 Курс ЦБ"), message)
            self._speak_variants(message)
            return

        key = "fiat_line_today" if parts.get("is_today") else "fiat_line"
        template = self.get_command_response(
            key, "Официальный курс {name} на {date_spoken}: {rate} {rate_word}."
        )
        try:
            line = template.format(**parts)
        except (KeyError, IndexError, ValueError):
            line = f"{parts['name']} на {parts['date_spoken']}: {parts['rate']} {parts['rate_word']}"
        self.send_to_gui(self.t("ui_fiat_tag", "🏦 Курс ЦБ"), line)
        self._speak_variants(line)

    def _report_weather(self, value):
        """Погода сейчас или на запрошенный срок. Пустое value — сейчас, локация из настроек."""
        if weather is None:
            self._speak_variants(self.get_command_response(
                "weather_fail", "Не смогла достать погоду, похоже, сеть шалит, фырк."
            ))
            return
        query = (value or "").strip()
        if query == "{query}":
            query = ""
        self.set_status(self.t("ui_status_weather", "🦊 Смотрю погоду..."), "#FF8C00")
        threading.Thread(target=self._weather_worker, args=(query,), daemon=True).start()

    def _weather_worker(self, query):
        lang = self.config.get("language", "ru")
        provider = self.config.get("weather_provider", "weatherapi")
        try:
            result = weather.answer(
                query,
                provider=provider,
                default_location=self.config.get("weather_location", "Долгопрудный"),
                api_key=self.config.get("weatherapi_key", ""),
                lang=lang,
            )
        except Exception as exc:
            print(f"[Weather] {exc}")
            result = None

        if not result:
            message = self.get_command_response(
                "weather_fail", "Не смогла достать погоду, похоже, сеть шалит, фырк."
            )
            self.send_to_gui(self.t("ui_weather_tag", "🌤️ Погода"), message)
            self._speak_variants(message)
            return

        template = self.get_command_response(result.get("template") or "weather_fail", "")
        if not template:
            template = self.get_command_response(
                "weather_fail", "Не смогла достать погоду, похоже, сеть шалит, фырк."
            )
        try:
            line = template.format(**(result.get("parts") or {}))
        except (KeyError, IndexError, ValueError):
            line = template
        variants = [part.strip() for part in str(line).split("|") if part.strip()]
        chosen = random.choice(variants) if variants else str(line)
        title = weather.PROVIDER_TITLE.get(result.get("provider"), "")
        tag = self.t("ui_weather_tag", "🌤️ Погода")
        if title:
            tag = f"{tag} · {title}"
        if result.get("fallback"):
            tag = f"{tag} ({self.t('ui_weather_fallback', 'запасной')})"
        self.send_to_gui(tag, chosen)
        self.speak(chosen)

    def execute_command_or_chat(self, command_text, full_phrase):  
        self.send_to_gui(self.t("ui_user_name"), full_phrase)  
        command_text = self._normalize_text(command_text)  
          
        chitchat_reply, chitchat_score = self._match_chitchat(command_text)  
          
        # Шаблоны со слотами идут первыми: иначе «включи саус парк серия 312»
        # уедет в команду «включи саус парк» без номера.
        slot_cmd, slot_values = self._match_slot_command(command_text)
        if slot_cmd:
            self.execute_scenario(self.commands[slot_cmd].get("steps", []), slot_values)
            return
          
        best_cmd, best_cmd_score, _chat = self._score_candidates(command_text)
          
        if chitchat_score >= float(self.config.get("asr_chitchat_strong", 75)) and chitchat_score >= best_cmd_score:  
            if chitchat_reply == "CAPABILITIES_TRIGGER":  
                self.report_capabilities()  
            elif chitchat_reply:  
                self.speak(chitchat_reply)  
                self.reset_wake_state()  
            return  
          
        if best_cmd_score >= float(self.config.get("asr_cmd_threshold", 70)) and best_cmd:  
            self.execute_scenario(self.commands[best_cmd].get("steps", []))  
            return  
          
        if chitchat_score >= float(self.config.get("asr_chitchat_threshold", 70)) and chitchat_reply:  
            if chitchat_reply == "CAPABILITIES_TRIGGER":  
                self.report_capabilities()  
            else:  
                self.speak(chitchat_reply)  
                self.reset_wake_state()  
            return  
          
        unknown_responses = self.get_command_response_list("unknown")  
        self.speak(random.choice(unknown_responses))  
        self.reset_wake_state()  
         
    def process_recognized_text(self, text):  
        raw_text = self._normalize_text(text)  
        if not raw_text:  
            return  

        if self.config.get("asr_debug", False):
            self._report_asr_debug(text)
          
        matched_alias, command_text = self._extract_wake_and_command(raw_text)  
        require_wake = self.config.get("require_wake_word", True)  
        now = time.time()  
        is_session_alive = self.is_active_session and ((now - self.last_activation_time) < self.config.get("wake_timeout", 7.0))  
          
        if require_wake:  
            if matched_alias:  
                self.is_active_session = True  
                self.last_activation_time = now  
                self.set_status(self.t("ui_status_listening"), "#FFD000")  
                  
                if not command_text or len(command_text) < 2:  
                    self.send_to_gui(self.t("ui_user_name"), raw_text)  
                    wake_responses = self.get_command_response_list("wake")  
                    self.speak(random.choice(wake_responses))  
                    return  
                  
                self.execute_command_or_chat(command_text, raw_text)  
                return  
              
            elif is_session_alive:  
                self.last_activation_time = now  
                self.execute_command_or_chat(raw_text, raw_text)  
                return  
              
            else:  
                return  
          
        self.execute_command_or_chat(raw_text, raw_text)  
         
    def listen_loop(self):  
        self.is_listening = True  
        self.reset_wake_state()  
          
        mic_device = self.config.get("microphone", "")
        stream_kwargs = {
            "samplerate": 16000,
            "blocksize": 1600,
            "dtype": "int16",
            "channels": 1,
            "callback": self._audio_callback
        }
        if mic_device:
            stream_kwargs["device"] = mic_device

        try:  
            device_info = sd.query_devices(kind='input' if not mic_device else mic_device)  
            dev_name = device_info.get('name', 'Unknown') if isinstance(device_info, dict) else mic_device  
            con_msg = self.t("ui_mic_connected").format(device=dev_name)  
            self.send_to_gui(self.t("ui_mic_tag"), con_msg)  
        except Exception as e:  
            try:
                device_info = sd.query_devices(kind='input')
                dev_name = device_info.get('name', 'Unknown')
                con_msg = self.t("ui_mic_connected").format(device=dev_name)
                self.send_to_gui(self.t("ui_mic_tag"), con_msg)
                stream_kwargs.pop("device", None)
            except Exception as ex:
                warn_msg = self.t("ui_mic_warning").format(error=ex)  
                self.send_to_gui(self.t("ui_mic_tag"), warn_msg)  
          
        try:  
            with sd.RawInputStream(**stream_kwargs):  
                while self.is_listening:  
                    try:  
                        data = self.audio_queue.get(timeout=0.1)  
                    except queue.Empty:  
                        continue  
                    self._handle_audio_block(data)
                              
        except Exception as e:  
            self.send_to_gui("Audio Error", f"Stream failed: {e}")  
            self.is_listening = False  

    def _handle_audio_block(self, data):
        """Распознаёт один блок аудио и исполняет законченную фразу.

        Вынесено из `listen_loop` отдельным методом, чтобы это можно было
        проверить без микрофона: на вход идёт тот же блок, что даёт sounddevice.
        """
        if self.asr_engine == "whisper" and self.whisper:
            if self.whisper.load_error:
                self._fallback_to_vosk(self.whisper.load_error)
                return
            heard = self.whisper.feed(data)
            if heard:
                self.process_recognized_text(heard)
            return

        if not self.recognizer:
            return

        if self.vosk_vad is None:
            self._reset_vosk_vad()

        # Конец фразы — по нашей тишине, не по раннему endpoint Vosk (~0.5 с).
        # AcceptWaveform всё равно кормим: пока Result() не вызван, декодер
        # продолжает ту же utterance и не теряет «…на завтра в москве».
        phrase_ended = self.vosk_vad.push(data)
        with self._asr_lock:
            recognizer = self.recognizer
            if recognizer is None:
                return
            vosk_endpoint = recognizer.AcceptWaveform(data)
            part_text = ""
            try:
                part_text = json.loads(recognizer.PartialResult()).get("partial", "") or ""
            except Exception:
                part_text = ""
            text = ""
            if phrase_ended:
                speech_ms = self.vosk_vad.speech_ms
                self.vosk_vad.take_audio()  # сброс буфера; байты нужны только Whisper
                min_speech = int(getattr(self.vosk_vad, "min_speech_ms", 200) or 200)
                if speech_ms < min_speech:
                    try:
                        recognizer.Result()
                    except Exception:
                        pass
                else:
                    try:
                        payload = recognizer.Result() if vosk_endpoint else recognizer.FinalResult()
                        text = json.loads(payload).get("text", "")
                    except Exception:
                        text = ""

        if part_text:
            matched_alias, _ = self._extract_wake_and_command(part_text.lower())
            if matched_alias and self.config.get("require_wake_word", True) and not self.is_active_session:
                self.is_active_session = True
                self.last_activation_time = time.time()
                self.set_status(self.t("ui_status_listening"), "#FFD000")

        if text:
            self._warn_about_unknown_tokens(text)
            self.process_recognized_text(text)

    def _warn_about_unknown_tokens(self, text):
        """Объясняет, почему в grammar-режиме не работают команды с номером.

        Словарь Vosk — это список готовых фраз, и числительных в нём нет: на
        записи «серия триста двенадцать» распознаётся как «серия [unk]», а
        значит команда с номером серии в этом режиме не сработает никогда.
        Молча терять такое нельзя — человек должен знать, что делать.
        """
        if "[unk]" not in text or not self.config.get("asr_grammar", True):
            return
        if getattr(self, "_unk_warned", False):
            return
        self._unk_warned = True
        print("[Vosk] [unk] в распознанном тексте: словарь не знает этих слов")
        self.send_to_gui(
            self.t("ui_mic_tag"),
            self.t(
                "warn_unk_tokens",
                "Словарь распознавания не знает этих слов — видно по «[unk]». "
                "Для команд с номером («включи саус парк серия 312») выключи "
                "asr_grammar в config.json.",
            ),
        )

    def start_listening(self):
        if getattr(self, "_heavy_parked", False):
            self._heavy_parked = False
            want_asr = self._asr_engine_fingerprint()
            if want_asr != ("vosk",) and getattr(self, "whisper", None) is None:
                self._init_asr_engine()
            elif want_asr == ("vosk",) and self.model is None:
                self._ensure_vosk_model()
            want_tts = str(self.config.get("tts_engine", "auto")).lower()
            if want_tts in ("silero", "kokoro") and not getattr(self, "_tts_engine_cache", None):
                self._init_tts_engine()
        has_engine = self.model or (self.asr_engine == "whisper" and self.whisper)
        if not self.is_listening and has_engine:
            if getattr(self, "whisper_reason", ""):
                self.send_to_gui(self.t("ui_mic_tag"), f"Распознавание: {self.whisper_reason}")
            self._listen_thread = threading.Thread(target=self.listen_loop, daemon=True)
            self._listen_thread.start()
             
    def stop_listening(self):
        self.is_listening = False
        self.interrupt_speech()
        thread = getattr(self, "_listen_thread", None)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        if getattr(self, "vosk_vad", None):
            self.vosk_vad.reset()
        self._park_heavy_models()
        self.set_status(self.t("ui_status_asleep"), "#8A798C")  
        self.send_to_gui(self.t("ui_mic_tag"), self.t("ui_mic_disconnected"))