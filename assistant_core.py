import os  
import sys  
import io  
import json  
import time  
import queue  
import random  
import threading  
import subprocess  
import webbrowser  
import ctypes  
import tempfile  
import wave  
import asyncio  
from datetime import datetime  
from pathlib import Path  
  
import pyautogui  
import pyttsx3  
import pyperclip  
import keyboard  
import sounddevice as sd  
import soundfile as sf  
import edge_tts  
from vosk import Model, KaldiRecognizer  
from rapidfuzz import fuzz  
import numpy as np  

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
COMMANDS_DIR = os.path.join(BASE_DIR, "commands")
MARKER_FILE = os.path.expanduser(r"~\\.sleep_never_marker")  
  
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
    "tts_engine": "edge-tts",  
    "tts_voice": "ru-RU-SvetlanaNeural",  
    "tts_rate": 190,
    "tts_pitch": "+35Hz",
    "tts_rate_edge": "+12%",
    "icon_path": "",  
    "avatar_path": "",  
    "icon_sleep_5min": "",  
    "icon_sleep_never": "",  
    "language": "ru",
    "microphone": ""
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
          
        self.config = self.load_config()  
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
        self.audio_queue = queue.Queue(maxsize=50)  
          
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
         
        model_path = find_vosk_model_dir("model")  
        if not model_path:  
            self.send_to_gui("ОШИБКА", "Модель Vosk не найдена в папке 'model'!")  
            self.model = None  
            self.recognizer = None  
        else:  
            print(f"[Vosk] Модель загружена: {model_path}")  
            self.model = Model(model_path)  
            self.recognizer = KaldiRecognizer(self.model, 16000)  
         
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
                  
        return self.commands  
         
    def save_command_definition(self, cmd_name, steps, synonyms=None, old_name=None, lang=None):
        if not lang:
            lang = self.config.get("language", "ru")
        target_data = self.command_data_ru if lang == "ru" else self.command_data_en
        target_cmds = target_data.setdefault("commands", {})

        if old_name and old_name in target_cmds and old_name != cmd_name:
            del target_cmds[old_name]

        target_cmds[cmd_name] = {
            "synonyms": synonyms if synonyms is not None else [],
            "steps": steps
        }
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
                self.config["tts_pitch"] = "+35Hz"
                self.config["tts_rate_edge"] = "+12%"
            self.save_config()  
         
        cur_lang = self.config.get("language", "ru")  
        self.lang = load_language_dict(cur_lang)  
        self.load_all_commands()  
         
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
            del temp_engine  
            ctypes.windll.ole32.CoUninitialize()  
        except Exception as e:  
            print(f"[Voice Detection Error]: {e}")  
         
    def load_config(self):  
        if os.path.exists(CONFIG_FILE):  
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:  
                data = json.load(f)  
                merged = DEFAULT_CONFIG.copy()  
                merged.update(data)  
                return merged  
        return DEFAULT_CONFIG.copy()  
         
    def save_config(self):  
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:  
            json.dump(self.config, f, ensure_ascii=False, indent=4)  
         
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
        async for chunk in communicate.stream():  
            if self.stop_speech_event.is_set():  
                return None  
            if chunk["type"] == "audio":  
                audio_stream.extend(chunk["data"])  
        return bytes(audio_stream)  
         
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
          
        if self.recognizer:  
            try:  
                self.recognizer.Result()  
            except Exception:  
                pass  
         
    def _tts_worker_loop(self):  
        while True:  
            text = self.tts_queue.get()  
            self.stop_speech_event.clear()  
            self.is_speaking = True  
            self.current_speaking_text = text  
             
            played_successfully = False  
            tts_engine_type = self.config.get("tts_engine", "edge-tts")  
            voice_name = self.config.get("tts_voice", "ru-RU-SvetlanaNeural")  
            pitch_mod = self.config.get("tts_pitch", "+0Hz")
            rate_mod = self.config.get("tts_rate_edge", "+10%")
             
            if self.stop_speech_event.is_set():  
                self.is_speaking = False  
                self.tts_queue.task_done()  
                continue  
             
            if tts_engine_type == "edge-tts":  
                try:  
                    audio_bytes = asyncio.run(self._async_generate_edge_tts(text, voice_name, pitch=pitch_mod, rate=rate_mod))  
                    if self.stop_speech_event.is_set():  
                        self.is_speaking = False  
                        self.tts_queue.task_done()  
                        continue  
                      
                    if audio_bytes and len(audio_bytes) > 100:  
                        with io.BytesIO(audio_bytes) as bio:  
                            data, sr = sf.read(bio, dtype="float32")  
                            if data.ndim > 1:  
                                data = data[:, 0]  
                            self._play_audio_array(data, sr)  
                            played_successfully = True  
                except Exception as e:  
                    print(f"[Edge-TTS Fallback]: {e}")  
                    self.send_to_gui("⚠️ Sound", self.t("ui_network_fallback"))  
             
            if not played_successfully and not self.stop_speech_event.is_set():  
                temp_wav = os.path.join(tempfile.gettempdir(), f"fox_{os.getpid()}_{int(time.time()*1000)}.wav")  
                try:  
                    def _render_sapi():  
                        try:  
                            ctypes.windll.ole32.CoInitialize(None)  
                            engine = pyttsx3.init()  
                            engine.setProperty("rate", self.config.get("tts_rate", 190))  
                            engine.setProperty("volume", 1.0)  
                              
                            lang = self.config.get("language", "ru")  
                            chosen_sapi = self.sapi_voices.get(lang) or self.sapi_voices.get("ru") or self.sapi_voices.get("en")  
                            if chosen_sapi:  
                                engine.setProperty("voice", chosen_sapi)  
                              
                            engine.save_to_file(text, temp_wav)  
                            engine.runAndWait()  
                            engine.stop()  
                        finally:  
                            try:  
                                ctypes.windll.ole32.CoUninitialize()  
                            except Exception:  
                                pass  
                     
                    t = threading.Thread(target=_render_sapi, daemon=True)  
                    t.start()  
                    t.join(timeout=10.0)
  
                    if not self.stop_speech_event.is_set() and os.path.exists(temp_wav) and os.path.getsize(temp_wav) > 44:  
                        with wave.open(temp_wav, "rb") as wf:  
                            sr = wf.getframerate()  
                            ch = wf.getnchannels()  
                            raw = wf.readframes(wf.getnframes())  
                          
                        arr = np.frombuffer(raw, dtype=np.int16)  
                        if ch > 1:  
                            arr = arr.reshape(-1, ch)[:, 0]  
                        data = arr.astype(np.float32) / 32768.0  
                        self._play_audio_array(data, sr)  
                        try:  
                            os.remove(temp_wav)  
                        except Exception:  
                            pass  
                except Exception as err:  
                    print(f"[SAPI5 Error]: {err}")  
             
            self.latest_spectrum = [0.0] * self.num_bands  
            self.current_speaking_text = ""  
            time.sleep(0.05)  
            self.is_speaking = False  
            self.tts_queue.task_done()  
         
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
         
    def lock_keyboard(self):  
        if not self.keyboard_locked:  
            self.kb_hook = keyboard.hook(lambda e: None, suppress=True)  
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
        samples = np.frombuffer(indata, dtype=np.int16).astype(np.float32) / 32768.0  
        if not self.is_speaking:  
            self._calc_spectrum(samples, sr=16000)  
        try:  
            self.audio_queue.put_nowait(bytes(indata))  
        except queue.Full:  
            pass  
         
    def execute_scenario(self, steps):  
        self.set_status(self.t("ui_status_conjuring"), "#FF8C00")  
        for step in steps:  
            action = step.get("action")  
            val = step.get("value", "")  
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
        raw = raw_text.lower().strip()  
        if not raw:  
            return None, ""  
          
        aliases = [self.config.get("wake_word", "лисичка").lower()] + [a.lower() for a in self.config.get("wake_aliases", [])]  
        aliases = sorted(list(set(aliases)), key=len, reverse=True)  
          
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
                score = fuzz.ratio(text, trigger)  
                partial = fuzz.partial_ratio(trigger, text) if words_count >= len(trigger.split()) else 0  
                final = max(score, partial)  
                if final > best_score:  
                    best_score = final  
                    matched_intent = intent  
         
        if best_score >= 70 and matched_intent:  
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
         
    def execute_command_or_chat(self, command_text, full_phrase):  
        self.send_to_gui(self.t("ui_user_name"), full_phrase)  
          
        chitchat_reply, chitchat_score = self._match_chitchat(command_text)  
          
        best_cmd_score = 0  
        best_cmd = None  
        cmd_words_count = len(command_text.split())  
          
        for cmd_name, cmd_data in self.commands.items():  
            phrases = [cmd_name] + cmd_data.get("synonyms", [])  
            for phrase in phrases:  
                phrase_words_count = len(phrase.split())  
                score = fuzz.ratio(command_text, phrase)  
                if cmd_words_count >= phrase_words_count:  
                    partial = fuzz.partial_ratio(phrase, command_text)  
                else:  
                    partial = 0  
                  
                final_score = max(score, partial)  
                if final_score > best_cmd_score:  
                    best_cmd_score = final_score  
                    best_cmd = cmd_name  
          
        if chitchat_score >= 75 and chitchat_score >= best_cmd_score:  
            if chitchat_reply == "CAPABILITIES_TRIGGER":  
                self.report_capabilities()  
            elif chitchat_reply:  
                self.speak(chitchat_reply)  
                self.reset_wake_state()  
            return  
          
        if best_cmd_score >= 70 and best_cmd:  
            self.execute_scenario(self.commands[best_cmd].get("steps", []))  
            return  
          
        if chitchat_score >= 70 and chitchat_reply:  
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
        raw_text = text.lower().strip()  
        if not raw_text:  
            return  
          
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

                    if not self.recognizer:  
                        continue  
                      
                    if self.recognizer.AcceptWaveform(data):  
                        result = json.loads(self.recognizer.Result())  
                        text = result.get("text", "")  
                        if text:  
                            self.process_recognized_text(text)  
                    else:  
                        part_json = json.loads(self.recognizer.PartialResult())  
                        part_text = part_json.get("partial", "").lower()  
                        if part_text:  
                            matched_alias, _ = self._extract_wake_and_command(part_text)  
                            if matched_alias and self.config.get("require_wake_word", True) and not self.is_active_session:  
                                self.is_active_session = True  
                                self.last_activation_time = time.time()  
                                self.set_status(self.t("ui_status_listening"), "#FFD000")  
                              
        except Exception as e:  
            self.send_to_gui("Audio Error", f"Stream failed: {e}")  
            self.is_listening = False  
             
    def start_listening(self):  
        if not self.is_listening and self.model:  
            threading.Thread(target=self.listen_loop, daemon=True).start()  
             
    def stop_listening(self):  
        self.is_listening = False  
        self.interrupt_speech()  
        self.set_status(self.t("ui_status_asleep"), "#8A798C")  
        self.send_to_gui(self.t("ui_mic_tag"), self.t("ui_mic_disconnected"))