import os
import sys
import math
import time
import json
import random
import threading
import subprocess
import tkinter as tk
import customtkinter as ctk
from PIL import Image, ImageDraw, ImageTk
import pystray
from pystray import MenuItem as item
import numpy as np

# --- Версии компонентов ---
APP_VERSION = "0.1"
VOSK_MODEL_VERSION = "vosk-model-small-ru-0.22"
WHISPER_VERSION = "faster-whisper-1.2.1"

# --- Принудительный UTF-8 для консоли ---------------------------------------
if os.name == "nt" and sys.stdout is not None:
    os.system("chcp 65001 >nul 2>&1")

for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

from assistant_core import (
    FoxAssistantCore,
    MARKER_FILE,
    MODE_BASIC,
    MODE_FULL,
    apply_mode_defaults,
    load_language_dict,
)
try:
    import tts_local
except Exception:  # модуль опционален: базовому режиму он не нужен
    tts_local = None
try:
    import full_deps
except Exception:  # без модуля движки полного режима не догрузятся из UI
    full_deps = None
try:
    import basic_setup
except Exception:  # без модуля лаунчер всё равно качает Vosk отдельно
    basic_setup = None

# --- Киберпанк-палитра KITSUNE ---
HUD_THEME = {
    "chassis_dark": "#05070A",
    "chassis_panel": "#0B0F16",
    "panel_card": "#111722",
    "panel_inner": "#080B10",
    "panel_border": "#1D2636",
    "panel_border_light": "#364154",
    "panel_border_glow": "#FF8C00",
    "panel_cyan_glow": "#00F0FF",
    
    # Неоновые языки пламени
    "flame_core": "#FFFFFF",
    "flame_hot": "#FFF0A5",
    "flame_gold": "#FFB703",
    "flame_amber": "#FB8500",
    "flame_crimson": "#D90429",
    "flame_glow": "#FF4500",
    "flame_smoke": "#450A14",
    
    "hud_cyan": "#00F0FF",
    "hud_cyan_dim": "#0E3846",
    "hud_green": "#00FF9D",
    "spirit_shield": "#C084FC",
    
    "text_bright": "#F8FAFC",
    "text_dim": "#7D8FA9",
    "text_dark": "#182232"
}

NUM_BANDS = 28

# Общий цикл реактора для базового и полного режима. Раньше слушающее окно
# перерисовывалось каждые 22 мс (~45 кадров/с), тишина — каждые 50 мс.
HUD_FRAME_MS = 80
HUD_IDLE_FRAME_MS = 160

LANG_OPTIONS = {
    "🇷🇺 Русский (RU)": "ru",
    "🇬🇧 English (EN)": "en"
}

ctk.set_appearance_mode("Dark")

def resource_path(relative_path):
    base_path = os.path.dirname(os.path.abspath(__file__))
    res_path = os.path.join(base_path, "resources", relative_path)
    if os.path.exists(res_path):
        return res_path
    return os.path.join(base_path, relative_path)

def create_default_fox_icon():
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.polygon([(8, 20), (56, 20), (32, 60)], fill="#FB8500")
    draw.polygon([(8, 20), (20, 4), (28, 20)], fill="#FFB703")
    draw.polygon([(56, 20), (44, 4), (36, 20)], fill="#FFB703")
    draw.ellipse([(28, 48), (36, 56)], fill="#05070A")
    return img

def create_fallback_sun_icon():
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([(18, 18), (46, 46)], fill="#FFB703")
    return img

def create_fallback_moon_icon():
    img = Image.new("RGBA", (64, 64), color=(0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([(14, 14), (50, 50)], fill="#00F2FE")
    draw.ellipse([(24, 10), (56, 46)], fill=(0, 0, 0, 0))
    return img

class FoxAssistantApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        
        self.protocol("WM_DELETE_WINDOW", self.hide_to_tray)
        self.configure(fg_color=HUD_THEME["chassis_dark"])
        
        self.core = FoxAssistantCore(
            update_gui_callback=self.update_chat,
            status_callback=self.update_status_badge,
            sleep_change_callback=self.on_sleep_mode_changed,
            window_action_callback=self.handle_window_action
        )

        self.cur_lang = self.core.config.get("language", "ru")
        self.lang = load_language_dict(self.cur_lang)
        self.title(self.t("app_title"))
        
        self.current_window_mode = "mini"
        self.current_screen = "chat"
        
        self.flame_time = 0.0
        self.rot_ring_inner = 0.0
        self.rot_ring_outer = 0.0
        self.scanline_y = 0.0
        self.smooth_heat = 0.0
        self.smooth_spectrum = [0.0] * NUM_BANDS

        self.current_editing_cmd = None
        self.step_rows = []

        self.load_sleep_icons()
        self.current_pil_icon = self.load_application_icon()
        self.apply_window_icon()
        self.load_fox_avatar()

        self.setup_actions_and_voices()

        self.tray_icon = None
        self.sleep_tray = None
        self.init_main_system_tray()
        self.init_sleep_system_tray()

        self.setup_ui()
        self.set_window_mode("mini")
        self._install_clipboard_hotkeys()
        
        self.animate_hud()
        self.after(100, self._ensure_basic_vosk_model)
        self.after(500, self.auto_start_listening)

    def t(self, key, default=""):
        return self.lang.get(key, default if default else key)

    def _install_clipboard_hotkeys(self):
        """Ctrl+C/V/X/A при русской (и любой не-EN) раскладке.

        Tk биндит только keysym Control-c/v/...; на RU те же физ. клавиши
        дают Cyrillic_* и буфер обмена молчит. keycode на Windows стабилен.
        """
        def _widget_state(widget):
            try:
                return str(widget.cget("state"))
            except Exception:
                return "normal"

        def _copy_selection(widget):
            try:
                selected = widget.selection_get()
            except Exception:
                return False
            try:
                widget.clipboard_clear()
                widget.clipboard_append(selected)
                return True
            except Exception:
                return False

        def _handler(event):
            if not (event.state & 0x4):
                return
            widget = event.widget
            if widget is None:
                return
            keycode = event.keycode
            keysym = (event.keysym or "").lower()
            state = _widget_state(widget)
            try:
                # 67=C, 86=V, 88=X, 65=A (Win VK); не дублируем EN-раскладку
                if keycode == 67:
                    if state == "disabled":
                        _copy_selection(widget)
                        return "break"
                    if keysym != "c":
                        widget.event_generate("<<Copy>>")
                        return "break"
                if keycode == 86:
                    if state == "disabled":
                        return "break"
                    if keysym != "v":
                        widget.event_generate("<<Paste>>")
                        return "break"
                if keycode == 88:
                    if state == "disabled":
                        return "break"
                    if keysym != "x":
                        widget.event_generate("<<Cut>>")
                        return "break"
                if keycode == 65 and keysym != "a":
                    try:
                        widget.tag_add("sel", "1.0", "end-1c")
                        widget.mark_set("insert", "1.0")
                        return "break"
                    except Exception:
                        pass
                    try:
                        widget.select_range(0, "end")
                        widget.icursor("end")
                        return "break"
                    except Exception:
                        pass
            except Exception:
                pass

        self.bind_all("<KeyPress>", _handler, add="+")

    def _make_textbox_selectable(self, textbox):
        """Выделение и Ctrl+C в read-only CTkTextbox (state=disabled)."""
        def _focus(_event=None):
            try:
                textbox.focus_set()
            except Exception:
                pass

        def _copy(_event=None):
            try:
                selected = textbox.selection_get()
                textbox.clipboard_clear()
                textbox.clipboard_append(selected)
            except Exception:
                pass
            return "break"

        # disabled Text не берёт фокус сам — без этого нет sel/Ctrl+C
        textbox.bind("<Button-1>", _focus)
        textbox.bind("<Control-c>", _copy)
        textbox.bind("<<Copy>>", _copy)
        # Вставка/вырезание в логе не нужны
        textbox.bind("<<Paste>>", lambda e: "break")
        textbox.bind("<<Cut>>", lambda e: "break")

    def get_available_input_devices(self):
        """Список устройств ввода: [(индекс, название), ...].

        PortAudio показывает одно и то же устройство в каждом Host API
        (MME, DirectSound, WASAPI, WDM-KS) — без фильтра у одной веб-камеры
        оказывается 3–4 копии. Оставляем WASAPI: он родной для Windows, у
        остальных API у устройств другие индексы и латентности, и выбрать
        их в config можно только случайно. Псевдоустройства (Sound Mapper,
        «первичный драйвер») и служебные записи с мусорными именами
        («@System32/...») отбрасываются по чёрному списку.
        """
        devices = []
        seen_names = set()
        blacklist = [
            "первичный драйвер",
            "primary sound capture",
            "переназначение",
            "mapper",
            "@system32",
            "input ()",
        ]
        try:
            import sounddevice as sd
            devs = sd.query_devices()
            hostapis = sd.query_hostapis()

            wasapi_id = None
            for h_idx, h in enumerate(hostapis):
                if "WASAPI" in h.get("name", ""):
                    wasapi_id = h_idx
                    break

            for idx, dev in enumerate(devs):
                if dev["max_input_channels"] <= 0:
                    continue
                # Без WASAPI (старый PortAudio, не-Windows) берём все входы.
                if wasapi_id is not None and dev["hostapi"] != wasapi_id:
                    continue

                name = dev["name"].strip()
                # WDM-KS отдаёт имена вида «@System32\drivers\bthhfenum.sys,
                # #2;%1 Hands-Free…» — нечитаемые, для выбора не годятся.
                if not name or name.startswith("@") or name == "()":
                    continue

                name_lower = name.lower()
                if any(bad in name_lower for bad in blacklist):
                    continue

                # Страховка: два Host API с совпадающими именами не должны
                # задвоить устройство в списке.
                if name in seen_names:
                    continue
                seen_names.add(name)
                devices.append((idx, name))
        except Exception:
            pass
        return devices

    def _full_mode(self):
        """Полный режим: whisper, Silero, kokoro и их настройки доступны."""
        return bool(self.core.config.get("full_mode", False))

    def setup_actions_and_voices(self):
        act_map = self.lang.get("actions", {})
        self.actions_dict = {
            act_map.get("get_power_mode", "get_power_mode"): "get_power_mode",
            act_map.get("show_full_window", "show_full_window"): "show_full_window",
            act_map.get("show_mini_window", "show_mini_window"): "show_mini_window",
            act_map.get("hide_to_tray", "hide_to_tray"): "hide_to_tray",
            act_map.get("list_commands", "list_commands"): "list_commands",
            act_map.get("fullscreen", "fullscreen"): "fullscreen",
            act_map.get("lock_keyboard", "lock_keyboard"): "lock_keyboard",
            act_map.get("unlock_keyboard", "unlock_keyboard"): "unlock_keyboard",
            act_map.get("toggle_sleep_mode", "toggle_sleep_mode"): "toggle_sleep_mode",
            act_map.get("sleep_never", "sleep_never"): "sleep_never",
            act_map.get("sleep_5min", "sleep_5min"): "sleep_5min",
            act_map.get("speak", "speak"): "speak",
            act_map.get("open_url", "open_url"): "open_url",
            act_map.get("hotkey", "hotkey"): "hotkey",
            act_map.get("type_text", "type_text"): "type_text",
            act_map.get("run_cmd", "run_cmd"): "run_cmd",
            act_map.get("screenshot", "screenshot"): "screenshot",
            act_map.get("crypto_rate", "crypto_rate"): "crypto_rate",
            act_map.get("fiat_rate", "fiat_rate"): "fiat_rate",
            act_map.get("weather", "weather"): "weather",
            act_map.get("set_volume", "set_volume"): "set_volume",
            act_map.get("pause", "pause"): "pause",
            act_map.get("volume_up", "volume_up"): "volume_up",
            act_map.get("volume_down", "volume_down"): "volume_down",
            act_map.get("volume_mute", "volume_mute"): "volume_mute",
            act_map.get("media_play_pause", "media_play_pause"): "media_play_pause",
            act_map.get("media_next", "media_next"): "media_next",
            act_map.get("media_prev", "media_prev"): "media_prev",
            act_map.get("lock_pc", "lock_pc"): "lock_pc",
            act_map.get("sleep_pc", "sleep_pc"): "sleep_pc"
        }
        self.rev_actions_dict = {v: k for k, v in self.actions_dict.items()}

        if self.cur_lang == "en":
            self.voice_options = {
                self.t("voice_en_kitsune"): ("edge-tts", "en-US-JennyNeural", "+32Hz", "+12%"),
                self.t("voice_en_aria"): ("edge-tts", "en-US-AriaNeural", "+0Hz", "+2%"),
                self.t("voice_en_ana"): ("edge-tts", "en-US-AnaNeural", "+0Hz", "+0%"),
                self.t("voice_sapi"): ("pyttsx3", "", "+0Hz", "+0%")
            }
        else:
            self.voice_options = {
                self.t("voice_ru_auto"): ("auto", "", "+0Hz", "+0%"),
                self.t("voice_ru_kitsune"): ("edge-tts", "ru-RU-SvetlanaNeural", "+10Hz", "+15%"),
                self.t("voice_ru_kawaii"): ("edge-tts", "ru-RU-SvetlanaNeural", "+75Hz", "+35%"),
                self.t("voice_ru_svetlana"): ("edge-tts", "ru-RU-SvetlanaNeural", "-10Hz", "-5%"),
                self.t("voice_sapi"): ("pyttsx3", "", "+0Hz", "+0%")
            }
        if self._full_mode():
            # Локальные движки видны только в полном режиме: в базовом они
            # всё равно не озвучивают, а пункты без движка сбивают с толку.
            # Пересобираем список: авто → локальные → edge-голоса → SAPI.
            rebuilt = {self.t("voice_ru_auto"): ("auto", "", "+0Hz", "+0%")}
            if self.cur_lang == "en":
                rebuilt[self.t("voice_ru_silero")] = ("silero", "baya", "+0Hz", "+0%")
                rebuilt[self.t("voice_ru_kokoro")] = ("kokoro", "sveta", "+0Hz", "+0%")
            else:
                rebuilt[self.t("voice_ru_silero")] = ("silero", "baya", "+0Hz", "+0%")
                rebuilt[self.t("voice_ru_kokoro")] = ("kokoro", "sveta", "+0Hz", "+0%")
            rebuilt.update({
                k: v for k, v in self.voice_options.items() if v[0] != "auto"
            })
            self.voice_options = rebuilt

    def _resolve_asset_path(self, config_key, default_resource_file):
        cfg_val = self.core.config.get(config_key, "").strip()
        if cfg_val and os.path.exists(cfg_val):
            return cfg_val
        res_p = resource_path(cfg_val if cfg_val else default_resource_file)
        if os.path.exists(res_p):
            return res_p
        return None

    def load_fox_avatar(self):
        avatar_path = self._resolve_asset_path("avatar_path", "fox_avatar.png")
        self.fox_avatar_tk = None
        self.fox_avatar_dim_tk = None
        if avatar_path and os.path.exists(avatar_path):
            try:
                pil_img = Image.open(avatar_path).convert("RGBA")
                target_size = (185, 185)
                pil_resized = pil_img.resize(target_size, Image.Resampling.LANCZOS)
                self.fox_avatar_tk = ImageTk.PhotoImage(pil_resized)

                dim_img = pil_resized.copy()
                r, g, b, a = dim_img.split()
                r = r.point(lambda p: int(p * 0.25))
                g = g.point(lambda p: int(p * 0.25))
                b = b.point(lambda p: int(p * 0.25))
                a = a.point(lambda p: int(p * 0.40))
                dim_img = Image.merge("RGBA", (r, g, b, a))
                self.fox_avatar_dim_tk = ImageTk.PhotoImage(dim_img)
            except Exception as e:
                print(f"[Avatar Load Error]: {e}")

    def auto_start_listening(self):
        ready = self.core.model or (
            getattr(self.core, "asr_engine", "") == "whisper" and self.core.whisper
        )
        if not self.core.is_listening and ready:
            self.toggle_listen()

    def load_sleep_icons(self):
        path_5min = self._resolve_asset_path("icon_sleep_5min", "icon_5min.png")
        path_never = self._resolve_asset_path("icon_sleep_never", "icon_never.png")
        self.icon_5min = Image.open(path_5min) if path_5min else create_fallback_moon_icon()
        self.icon_never = Image.open(path_never) if path_never else create_fallback_sun_icon()

    def load_application_icon(self):
        icon_path = self._resolve_asset_path("icon_path", "icon.png")
        if icon_path and os.path.exists(icon_path):
            try:
                return Image.open(icon_path)
            except Exception:
                pass
        return create_default_fox_icon()

    def apply_window_icon(self):
        try:
            self._tk_icon = ImageTk.PhotoImage(self.current_pil_icon)
            self.iconphoto(False, self._tk_icon)
        except Exception:
            pass

    def init_main_system_tray(self):
        menu = pystray.Menu(
            item(self.t("tray_full"), lambda: self.handle_window_action("full"), default=True),
            item(self.t("tray_mini"), lambda: self.handle_window_action("mini")),
            item(self.t("tray_mic"), self.tray_toggle_listen),
            pystray.Menu.SEPARATOR,
            item(self.t("tray_quit"), self.quit_application)
        )
        self.tray_icon = pystray.Icon("kitsune_main_icon", self.current_pil_icon, self.t("tray_app_name"), menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def init_sleep_system_tray(self):
        is_never = os.path.exists(MARKER_FILE)
        current_img = self.icon_never if is_never else self.icon_5min
        tooltip = self.t("tray_pwr_tooltip_never") if is_never else self.t("tray_pwr_tooltip_5min")

        sleep_menu = pystray.Menu(
            item(self.t("tray_toggle_sleep"), self.tray_toggle_sleep_action, default=True),
            item(self.t("tray_sleep_never"), lambda: self.core.set_sleep_never()),
            item(self.t("tray_sleep_5min"), lambda: self.core.set_sleep_5min())
        )
        self.sleep_tray = pystray.Icon("sleep_switcher_tray", current_img, tooltip, sleep_menu)
        threading.Thread(target=self.sleep_tray.run, daemon=True).start()

    def tray_toggle_sleep_action(self, icon=None, item=None):
        self.core.toggle_sleep_mode()

    def on_sleep_mode_changed(self, is_never: bool):
        if self.sleep_tray:
            self.sleep_tray.icon = self.icon_never if is_never else self.icon_5min
            self.sleep_tray.title = self.t("tray_pwr_tooltip_never") if is_never else self.t("tray_pwr_tooltip_5min")

    def handle_window_action(self, mode):
        self.after(0, lambda: self.set_window_mode(mode))

    def toggle_window_mode_on_click(self, event=None):
        if self.current_window_mode == "full":
            self.set_window_mode("mini")
        else:
            self.set_window_mode("full")

    def set_window_mode(self, mode):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()

        if mode == "mini":
            self.current_window_mode = "mini"
            if self.state() == "zoomed":
                self.state("normal")
            self.minsize(200, 200)

            self.sidebar.pack_forget()
            self.frame_editor.pack_forget()
            self.frame_settings.pack_forget()
            self.status_card.pack_forget()
            self.chat_card.pack_forget()
            self.container.pack_forget()

            self.container.pack(fill="both", expand=True, padx=2, pady=2)
            self.frame_chat.pack(fill="both", expand=True)
            self.reactor_frame.pack_forget()
            self.reactor_frame.pack(fill="both", expand=True)

            self.update_idletasks()
            w, h = 330, 360
            x = max(0, sw - w - 24)
            y = max(0, sh - h - 68)

            self.geometry(f"{w}x{h}+{x}+{y}")
            self.attributes("-topmost", True)
            self.deiconify()
            self.lift()
            self.update_idletasks()
            self.after(25, lambda: self.geometry(f"{w}x{h}+{x}+{y}"))

        elif mode == "full":
            self.current_window_mode = "full"
            self.attributes("-topmost", False)
            if self.state() == "zoomed":
                self.state("normal")

            self.container.pack_forget()
            self.status_card.pack_forget()
            self.reactor_frame.pack_forget()
            self.chat_card.pack_forget()

            self.sidebar.pack(side="left", fill="y", padx=(14, 0), pady=14)
            self.container.pack(side="right", fill="both", expand=True, padx=14, pady=14)

            self.status_card.pack(fill="x", pady=(0, 10))
            self.reactor_frame.configure(height=360)
            self.reactor_frame.pack(fill="x", pady=(0, 10))
            self.chat_card.pack(fill="both", expand=True)

            self.show_chat()
            self.update_idletasks()

            w, h = 1180, 850
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            
            self.minsize(980, 700)
            self.geometry(f"{w}x{h}+{x}+{y}")
            self.deiconify()
            self.lift()
            self.focus_force()
            self.update_idletasks()
            self.after(25, lambda: self.geometry(f"{w}x{h}+{x}+{y}"))

        elif mode == "tray":
            self.current_window_mode = "tray"
            self.withdraw()
            if self.tray_icon:
                self.tray_icon.notify(self.t("tray_notif_hidden"), self.t("tray_app_name"))

    def hide_to_tray(self):
        self.set_window_mode("tray")

    def show_from_tray(self, icon=None, item=None):
        self.handle_window_action("full")

    def tray_toggle_listen(self, icon=None, item=None):
        self.after(0, self.toggle_listen)

    def quit_application(self, icon=None, item=None):
        if hasattr(self, 'core'):
            if self.core.is_listening:
                self.core.stop_listening()
            if self.core.keyboard_locked:
                self.core.unlock_keyboard()
        if self.tray_icon:
            self.tray_icon.stop()
        if self.sleep_tray:
            self.sleep_tray.stop()
        self.after(50, self._clean_shutdown)

    def _clean_shutdown(self):
        try:
            self.destroy()
        except Exception:
            pass
        os._exit(0)

    def _asr_engine_options(self):
        """Доступные движки распознавания для списка настроек.

        Базовому режиму whisper не показывается: он там всё равно не
        поднимется. В полном режиме рядом с авто/Vosk — GPU, turbo CPU,
        medium CPU и small CPU.
        """
        options = {
            self.t("asr_engine_auto"): "auto",
            self.t("asr_engine_vosk"): "vosk",
        }
        if self._full_mode():
            options[self.t("asr_engine_whisper")] = "whisper"
            options[self.t("asr_engine_whisper_cpu")] = "whisper_cpu"
            options[self.t("asr_engine_whisper_medium_cpu")] = "whisper_medium_cpu"
            options[self.t("asr_engine_whisper_small_cpu")] = "whisper_small_cpu"
        return options

    def _set_engine_combo(self, engine_value):
        wanted = str(engine_value).lower().strip()
        if wanted in ("whisper_gpu", "gpu"):
            wanted = "whisper"
        for label, value in self.asr_engine_values.items():
            if value == wanted:
                self.combo_asr_engine.set(label)
                return
        self.combo_asr_engine.set(list(self.asr_engine_values.keys())[0])

    def open_about_window(self):
        about_win = ctk.CTkToplevel(self)
        about_win.title(self.t("about_window_title"))
        about_win.geometry("460x560")
        about_win.configure(fg_color=HUD_THEME["chassis_dark"])
        about_win.resizable(False, False)
        about_win.transient(self)
        about_win.grab_set()
        about_win.focus_force()

        card = ctk.CTkFrame(
            about_win, fg_color=HUD_THEME["panel_card"],
            border_color=HUD_THEME["panel_border"], border_width=1.5,
            corner_radius=12
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        # Кнопки сразу под заголовком — иначе длинное описание выталкивает
        # «Включить фулл» за край окна, и кажется, что кнопка «не работает».
        lbl_ver = ctk.CTkLabel(
            card, text=f"🦊 KITSUNE // {self.t('about_header_text')} {APP_VERSION}",
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color="#FFB703"
        )
        lbl_ver.pack(pady=(16, 10))

        btn_row = ctk.CTkFrame(card, fg_color="transparent")
        btn_row.pack(fill="x", padx=20, pady=(0, 12))
        btn_row.grid_columnconfigure(0, weight=1)
        btn_row.grid_columnconfigure(1, weight=1)

        if self._full_mode():
            mode_btn_text = self.t("about_disable_full_btn")
            mode_btn_colors = {
                "fg_color": "#26334A",
                "hover_color": "#36475F",
                "text_color": HUD_THEME["text_bright"],
            }
        else:
            mode_btn_text = self.t("about_enable_full_btn")
            mode_btn_colors = {
                "fg_color": "#00F0FF",
                "hover_color": "#00B8C4",
                "text_color": HUD_THEME["chassis_dark"],
            }

        btn_mode = ctk.CTkButton(
            btn_row,
            text=mode_btn_text,
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            height=36, corner_radius=6,
            command=lambda: self.toggle_full_mode(about_win),
            **mode_btn_colors
        )
        btn_mode.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        btn_close = ctk.CTkButton(
            btn_row,
            text=self.t("about_close_btn"),
            fg_color="#FB8500", hover_color="#D94400",
            text_color=HUD_THEME["chassis_dark"],
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            height=36, corner_radius=6,
            command=about_win.destroy
        )
        btn_close.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        avatar_lbl = ctk.CTkLabel(card, text="")
        avatar_lbl.pack(pady=(4, 8))
        avatar_path = self._resolve_asset_path("avatar_path", "fox_avatar.png")
        if avatar_path and os.path.exists(avatar_path):
            try:
                pil_img = Image.open(avatar_path).convert("RGBA")
                self.about_avatar_ctk = ctk.CTkImage(
                    light_image=pil_img,
                    dark_image=pil_img,
                    size=(140, 140)
                )
                avatar_lbl.configure(image=self.about_avatar_ctk)
            except Exception:
                avatar_lbl.configure(text="🦊", font=("Segoe UI Emoji", 48))
        else:
            avatar_lbl.configure(text="🦊", font=("Segoe UI Emoji", 48))

        desc_text = self.t("about_desc_text").format(
            vosk_ver=VOSK_MODEL_VERSION,
            whisper_ver=WHISPER_VERSION
        )
        desc_box = ctk.CTkTextbox(
            card,
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color=HUD_THEME["panel_inner"],
            text_color=HUD_THEME["text_bright"],
            border_width=0,
            corner_radius=8,
            wrap="word",
            activate_scrollbars=True,
        )
        desc_box.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        desc_box.insert("1.0", desc_text)
        desc_box.configure(state="disabled")
        self._make_textbox_selectable(desc_box)

    def toggle_full_mode(self, window=None):
        """Включает/выключает полный режим.

        Сам переход пакеты не качает: Whisper/Silero/kokoro догружаются
        позже, когда их выбирают в настройках. Базовый режим остаётся на
        Vosk + edge-tts.
        """
        self._apply_mode_switch(not self._full_mode(), window)

    def _apply_mode_switch(self, new_full, window=None):
        """Переключает режим и обновляет UI/движки."""
        try:
            apply_mode_defaults(self.core.config, new_full)
            self.core.save_config()

            # Сначала закрываем модалку и обновляем меню — тяжёлую пересборку
            # ASR делаем следом, чтобы клик не «замирал» на recognizer.
            if window is not None:
                try:
                    window.grab_release()
                except Exception:
                    pass
                try:
                    window.destroy()
                except Exception:
                    pass

            self.setup_actions_and_voices()
            self._refresh_mode_dependent_ui()
            label = (
                self.t("mode_enabled_full_msg")
                if new_full
                else self.t("mode_disabled_full_msg")
            )
            self.update_chat("System", label)
            self.after(0, self._finish_mode_switch_engines)
        except Exception as exc:
            self.update_chat("System", f"Не удалось переключить режим: {exc}")
            print(f"[Mode] switch failed: {exc}")

    def _finish_mode_switch_engines(self):
        """Догружает/сбрасывает движки после переключения режима."""
        try:
            self.core._init_asr_engine()
            self.core._init_tts_engine()
            self.core.rebuild_recognizer()
            self.core.reset_wake_state()
        except Exception as exc:
            self.update_chat("System", f"Режим сменён, но движки не пересобрались: {exc}")
            print(f"[Mode] engine refresh failed: {exc}")

    def _ensure_basic_vosk_model(self):
        """Если после клона нет весов Vosk — качает их в фоне и поднимает распознавание."""
        if basic_setup is None:
            return
        if basic_setup.vosk_model_ready():
            return
        if getattr(self, "_vosk_fetch_busy", False):
            return
        self._vosk_fetch_busy = True
        self.update_chat("System", self.t("vosk_model_downloading"))

        def worker():
            ok = False
            unexpected = None
            try:
                ok = basic_setup.ensure_vosk_model(
                    progress=lambda m: self.after(0, lambda msg=m: self.update_chat("System", msg))
                )
            except Exception as exc:
                unexpected = exc

            def finish():
                self._vosk_fetch_busy = False
                if ok:
                    self.core.model = None
                    # Пересоздаём recognizer: при старте модели не было.
                    from assistant_core import find_vosk_model_dir
                    from vosk import Model
                    path = find_vosk_model_dir("model")
                    if path:
                        try:
                            self.core.model = Model(path)
                            self.core.rebuild_recognizer()
                            self.update_chat("System", self.t("vosk_model_ready"))
                        except Exception as exc:
                            self.update_chat("System", f"{self.t('vosk_model_failed')}: {exc}")
                            self._show_vosk_manual_help()
                    else:
                        self._show_vosk_manual_help()
                    return
                # При обычном отказе ensure_vosk_model уже написал инструкцию
                # через progress. Дублируем только если упали исключением.
                if unexpected is not None:
                    self.update_chat(
                        "System",
                        f"{self.t('vosk_model_failed')}: {unexpected}",
                    )
                    self._show_vosk_manual_help()

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _show_vosk_manual_help(self):
        """Пишет в чат, откуда скачать Vosk вручную, если автозагрузка убита 403/прокси."""
        self.update_chat("System", self.t("vosk_model_failed"))
        if basic_setup is None:
            return
        try:
            text = basic_setup.manual_install_instructions()
        except Exception:
            return
        for line in text.splitlines():
            if line.strip():
                self.update_chat("System", line)

    def _start_component_install(self, components, pending_config=None):
        """Окно прогресса: качает выбранные движки и один раз перезапускает.

        ``components`` — список пар ``(имя, модель whisper или None)``.
        Если за один Save выбраны и распознавание, и синтез, ставятся оба,
        потом один перезапуск.
        """
        if isinstance(components, str):
            components = [(components, None)]
        components = [(str(name), model) for name, model in components if name]
        if full_deps is None:
            self.update_chat("System", self.t("full_deps_module_missing"))
            return
        if getattr(self, "_full_deps_busy", False) or not components:
            return
        self._full_deps_busy = True
        if pending_config:
            self.core.config.update(pending_config)
            self.core.save_config()
        label = ", ".join(name for name, _model in components)

        win = ctk.CTkToplevel(self)
        win.title(self.t("full_deps_window_title"))
        win.geometry("520x360")
        win.configure(fg_color=HUD_THEME["chassis_dark"])
        win.resizable(False, False)
        win.grab_set()

        card = ctk.CTkFrame(
            win, fg_color=HUD_THEME["panel_card"],
            border_color=HUD_THEME["panel_border"], border_width=1.5,
            corner_radius=12,
        )
        card.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            card, text=self.t("full_deps_window_title"),
            font=ctk.CTkFont(family="Consolas", size=14, weight="bold"),
            text_color="#FFB703",
        ).pack(anchor="w", padx=16, pady=(16, 6))

        ctk.CTkLabel(
            card, text=self.t("full_deps_window_hint"),
            font=ctk.CTkFont(family="Consolas", size=11),
            text_color=HUD_THEME["text_dim"],
            justify="left", wraplength=460,
        ).pack(anchor="w", padx=16, pady=(0, 10))

        log = ctk.CTkTextbox(
            card, font=ctk.CTkFont(family="Consolas", size=11),
            fg_color=HUD_THEME["panel_inner"],
            text_color=HUD_THEME["text_bright"],
            border_width=0, corner_radius=8, height=180,
        )
        log.pack(fill="both", expand=True, padx=16, pady=(0, 12))
        log.insert("end", self.t("full_deps_starting") + f" [{label}]\n")
        log.configure(state="disabled")
        self._make_textbox_selectable(log)

        def append_line(message):
            try:
                log.configure(state="normal")
                log.insert("end", str(message) + "\n")
                log.see("end")
                log.configure(state="disabled")
            except Exception:
                pass

        def worker():
            def progress(message):
                self.after(0, lambda m=message: append_line(m))

            try:
                result = {"ok": True, "restart_needed": False, "error": ""}
                for name, whisper_model in components:
                    one = full_deps.install_component(
                        name, progress=progress, whisper_model=whisper_model,
                    )
                    result["restart_needed"] = result["restart_needed"] or bool(one.get("restart_needed"))
                    if not one.get("ok"):
                        result = one
                        break
            except Exception as exc:
                result = {"ok": False, "restart_needed": False, "error": str(exc)}

            def finish():
                self._full_deps_busy = False
                if result.get("ok"):
                    append_line(self.t("full_deps_restarting"))
                    self.after(800, self._restart_app)
                else:
                    err = result.get("error") or self.t("full_deps_failed")
                    append_line(err)
                    self.update_chat("System", self.t("full_deps_failed"))
                    try:
                        ctk.CTkButton(
                            card, text=self.t("about_close_btn"),
                            fg_color="#FB8500", hover_color="#D94400",
                            text_color=HUD_THEME["chassis_dark"],
                            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
                            command=win.destroy,
                        ).pack(pady=(0, 12))
                    except Exception:
                        pass

            self.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def _restart_app(self):
        """Перезапускает процесс, чтобы новые пакеты корректно импортировались."""
        try:
            script = os.path.abspath(sys.argv[0] or "main.py")
            args = [sys.executable, script, *sys.argv[1:]]
            subprocess.Popen(args, cwd=os.path.dirname(script) or os.getcwd())
        except Exception as exc:
            self.update_chat("System", f"{self.t('full_deps_restart_failed')}: {exc}")
            return
        self.after(50, self._clean_shutdown)

    def _refresh_mode_dependent_ui(self):
        """Обновляет экран настроек под текущий режим без пересоздания окна.

        Проще всего пересобрать экран заново: он статичен между режимами, а
        точечное скрытие/показ каждого виджета легко рассинхронизировать.
        """
        if hasattr(self, "frame_settings"):
            was_visible = self.frame_settings.winfo_ismapped()
            self.frame_settings.destroy()
            self.build_settings_screen()
            if was_visible:
                self.show_settings()
            else:
                self.frame_settings.pack_forget()

    def retranslate_ui(self):
        self.title(self.t("app_title"))
        self.lbl_logo_title.configure(text=self.t("app_brand"))
        self.lbl_logo_subtitle.configure(text=self.t("app_subtitle").format(version=APP_VERSION))

        self.nav_btns["chat"].configure(text=self.t("nav_chat"))
        self.nav_btns["editor"].configure(text=self.t("nav_editor"))
        self.nav_btns["settings"].configure(text=self.t("nav_settings"))

        if not self.core.is_listening:
            self.btn_listen.configure(text=self.t("btn_wake"))
        else:
            self.btn_listen.configure(text=self.t("btn_sleep"))

        self.status_lbl.configure(text=self.t("status_idle"))

        self.lbl_editor_title.configure(text=self.t("editor_title"))
        self.btn_load_packs.configure(text=self.t("editor_btn_load_packs"))
        self.btn_new_cmd.configure(text=self.t("editor_btn_new"))
        self.entry_cmd_search.configure(placeholder_text=self.t("editor_search_ph"))

        self.lbl_editor_phrase.configure(text=self.t("editor_lbl_phrase"))
        self.entry_cmd_name.configure(placeholder_text=self.t("editor_ph_phrase"))
        self.lbl_editor_synonyms.configure(text=self.t("editor_lbl_synonyms"))
        self.entry_synonyms.configure(placeholder_text=self.t("editor_ph_synonyms"))

        self.lbl_steps_header.configure(text=self.t("editor_steps_header"))
        self.btn_add_step.configure(text=self.t("editor_btn_add_step"))

        self.btn_save_cmd.configure(text=self.t("editor_btn_save_cmd"))
        self.btn_delete_cmd.configure(text=self.t("editor_btn_delete_cmd"))
        self.btn_clear_cmd.configure(text=self.t("editor_btn_clear"))

        self.setup_actions_and_voices()
        
        act_vals = list(self.actions_dict.keys())
        for row in self.step_rows:
            curr_act_key = row["action_key"]
            row["combo"].configure(values=act_vals)
            loc_name = self.rev_actions_dict.get(curr_act_key, act_vals[0])
            row["combo"].set(loc_name)

        self.lbl_settings_title.configure(text=self.t("settings_title"))
        self.check_wake.configure(text=self.t("settings_chk_wake"))
        self.lbl_settings_wake.configure(text=self.t("settings_lbl_wake"))
        self.lbl_settings_lang.configure(text=self.t("settings_lbl_lang"))
        self.lbl_settings_mic.configure(text=self.t("settings_lbl_mic"))
        self.lbl_settings_voice.configure(text=self.t("settings_lbl_voice"))
        self.lbl_settings_timeout.configure(text=self.t("settings_lbl_timeout"))
        self.check_grammar.configure(text=self.t("settings_chk_grammar"))
        self.check_asr_debug.configure(text=self.t("settings_chk_asr_debug"))
        self.lbl_settings_asr_engine.configure(text=self.t("settings_lbl_asr_engine"))
        if hasattr(self, "lbl_settings_weather"):
            self.lbl_settings_weather.configure(text=self.t("settings_lbl_weather"))
            self.lbl_settings_weather_place.configure(text=self.t("settings_lbl_weather_place"))
            self.lbl_settings_weather_key.configure(text=self.t("settings_lbl_weather_key"))
        
        current_engine = self.asr_engine_values.get(self.combo_asr_engine.get(), "auto")
        self.asr_engine_values = self._asr_engine_options()
        self.combo_asr_engine.configure(values=list(self.asr_engine_values.keys()))
        self._set_engine_combo(current_engine)
        self.btn_save_settings.configure(text=self.t("settings_btn_save"))

        self.combo_voice.configure(values=list(self.voice_options.keys()))

        input_devices = self.get_available_input_devices()
        mic_display_values = [self.t("mic_default")] + [d[1] for d in input_devices]
        self.combo_mic.configure(values=mic_display_values)
        cur_mic = self.core.config.get("microphone", "")
        if not cur_mic or cur_mic not in mic_display_values:
            self.combo_mic.set(mic_display_values[0])
        else:
            self.combo_mic.set(cur_mic)

        self.combo_voice.set(self._voice_display_for_config())

        # Скрытие/показ голосов локальных движков зависит от режима.
        if self._full_mode():
            self.lbl_settings_silero.pack(anchor="w", padx=22, pady=(5, 2))
            self.combo_silero.pack(anchor="w", padx=22, pady=(0, 16))
            self.lbl_settings_kokoro.pack(anchor="w", padx=22, pady=(5, 2))
            self.combo_kokoro.pack(anchor="w", padx=22, pady=(0, 16))
        else:
            self.lbl_settings_silero.pack_forget()
            self.combo_silero.pack_forget()
            self.lbl_settings_kokoro.pack_forget()
            self.combo_kokoro.pack_forget()

        self.refresh_editor_command_list()

    def _voice_display_for_config(self):
        """Подпись в списке голосов, соответствующая текущему конфигу.

        Локальные движки хранят голос в своих ключах (silero_speaker,
        kokoro_voice), поэтому сверять их с tts_voice нельзя — иначе после
        сохранения настроек выбор «съезжал» на первый пункт списка.
        """
        engine = str(self.core.config.get("tts_engine", "auto")).lower()
        per_engine = {
            "silero": self.core.config.get("silero_speaker", ""),
            "kokoro": self.core.config.get("kokoro_voice", ""),
        }

        for label, values in self.voice_options.items():
            option_engine, voice_name = values[0], values[1]
            if option_engine != engine:
                continue
            if engine in ("auto", "pyttsx3", "silero", "kokoro"):
                # Голос у этих движков один в рамках пункта списка либо берётся
                # из отдельного ключа; дополнительное сравнение не нужно.
                if engine not in per_engine or per_engine[engine] == voice_name:
                    return label
                continue
            if voice_name == self.core.config.get("tts_voice", ""):
                return label

        return list(self.voice_options.keys())[0]

    def on_language_selected(self, selected_label):
        new_lang = LANG_OPTIONS.get(selected_label, "ru")
        if new_lang != self.cur_lang:
            self.cur_lang = new_lang
            self.core.config["language"] = new_lang
            
            if new_lang == "en":
                self.core.config["tts_voice"] = "en-US-JennyNeural"
                self.core.config["tts_pitch"] = "+32Hz"
                self.core.config["tts_rate_edge"] = "+12%"
            else:
                self.core.config["tts_voice"] = "ru-RU-SvetlanaNeural"
                self.core.config["tts_pitch"] = "+10Hz"
                self.core.config["tts_rate_edge"] = "+15%"
            self.core.config["tts_engine"] = "edge-tts"
            
            self.core.save_config()
            self.core.reload_language(new_lang)
            self.lang = load_language_dict(self.cur_lang)
            self.retranslate_ui()
            self.update_chat("System", self.t("settings_lang_switched"))

    def setup_ui(self):
        self.sidebar = ctk.CTkFrame(
            self, width=274, corner_radius=12,
            fg_color=HUD_THEME["chassis_panel"],
            border_color=HUD_THEME["panel_border"],
            border_width=2
        )
        self.sidebar.pack(side="left", fill="y", padx=(14, 0), pady=14)
        self.sidebar.pack_propagate(False)

        logo_card = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        logo_card.pack(fill="x", padx=16, pady=(22, 16))

        self.lbl_logo_title = ctk.CTkLabel(
            logo_card, text=self.t("app_brand"), 
            font=ctk.CTkFont(family="Consolas", size=22, weight="bold"),
            text_color="#FFB703", anchor="w"
        )
        self.lbl_logo_title.pack(anchor="w")

        self.lbl_logo_subtitle = ctk.CTkButton(
            logo_card, text=self.t("app_subtitle").format(version=APP_VERSION), 
            font=ctk.CTkFont(family="Consolas", size=8, weight="bold"),
            fg_color="transparent",
            hover_color=HUD_THEME["chassis_panel"],
            text_color=HUD_THEME["text_dim"],
            anchor="w",
            height=20,
            command=self.open_about_window
        )
        self.lbl_logo_subtitle.pack(anchor="w", pady=(2, 0))

        self.nav_btns = {}
        self.nav_btns["chat"] = self._create_nav_button(self.t("nav_chat"), self.show_chat)
        self.nav_btns["chat"].pack(pady=4, padx=12, fill="x")

        self.nav_btns["editor"] = self._create_nav_button(self.t("nav_editor"), self.show_editor)
        self.nav_btns["editor"].pack(pady=4, padx=12, fill="x")

        self.nav_btns["settings"] = self._create_nav_button(self.t("nav_settings"), self.show_settings)
        self.nav_btns["settings"].pack(pady=4, padx=12, fill="x")

        self.telemetry_card = ctk.CTkFrame(
            self.sidebar, fg_color=HUD_THEME["panel_inner"],
            corner_radius=10, border_color=HUD_THEME["panel_border"], border_width=1.5
        )
        self.telemetry_card.pack(fill="x", padx=12, pady=12, side="bottom")

        self.canvas_telemetry = tk.Canvas(
            self.telemetry_card, height=114, bg=HUD_THEME["panel_inner"],
            highlightthickness=0, bd=0
        )
        self.canvas_telemetry.pack(fill="both", expand=True, padx=2, pady=2)

        self.btn_listen = ctk.CTkButton(
            self.sidebar, text=self.t("btn_sleep"), 
            fg_color="#180C11",
            hover_color="#2A121A",
            border_color="#FB8500",
            border_width=1.5,
            text_color=HUD_THEME["text_bright"], 
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            height=40, corner_radius=8,
            command=self.toggle_listen
        )
        self.btn_listen.pack(pady=(4, 10), padx=12, fill="x", side="bottom")

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(side="right", fill="both", expand=True, padx=14, pady=14)
        
        self.build_chat_screen()
        self.build_editor_screen()
        self.build_settings_screen()

    def _create_nav_button(self, text, command):
        return ctk.CTkButton(
            self.sidebar, text=text,
            fg_color="transparent", 
            text_color=HUD_THEME["text_dim"],
            hover_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            anchor="w", height=38, corner_radius=6,
            command=command
        )

    def _highlight_active_nav(self, active_key):
        self.current_screen = active_key
        for key, btn in self.nav_btns.items():
            if key == active_key:
                btn.configure(
                    fg_color=HUD_THEME["panel_card"],
                    text_color="#FFB703",
                    border_color="#FB8500",
                    border_width=1.5
                )
            else:
                btn.configure(
                    fg_color="transparent",
                    text_color=HUD_THEME["text_dim"],
                    border_width=0
                )

    def build_chat_screen(self):
        self.frame_chat = ctk.CTkFrame(self.container, fg_color="transparent")
        
        self.status_card = ctk.CTkFrame(
            self.frame_chat, height=44, 
            fg_color=HUD_THEME["panel_card"],
            corner_radius=8, border_color=HUD_THEME["panel_border"], border_width=1.5
        )
        self.status_card.pack(fill="x", pady=(0, 10))
        self.status_card.pack_propagate(False)
        
        self.status_lbl = ctk.CTkLabel(
            self.status_card, text=self.t("status_idle"), 
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=HUD_THEME["text_dim"]
        )
        self.status_lbl.pack(side="left", padx=16)

        self.hud_clock_lbl = ctk.CTkLabel(
            self.status_card, text=f"{self.t('clock_prefix', 'ЛИСЬЕ_ВРЕМЯ')}: --:--:--",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color="#FFB703"
        )
        self.hud_clock_lbl.pack(side="right", padx=16)

        self.reactor_frame = ctk.CTkFrame(
            self.frame_chat, height=360,
            fg_color=HUD_THEME["panel_inner"],
            corner_radius=12, border_color=HUD_THEME["panel_border_light"], border_width=2
        )
        self.reactor_frame.pack(fill="x", pady=(0, 10))
        self.reactor_frame.pack_propagate(False)

        self.canvas_reactor = tk.Canvas(
            self.reactor_frame, bg=HUD_THEME["panel_inner"],
            highlightthickness=0, bd=0
        )
        self.canvas_reactor.pack(fill="both", expand=True)

        ev_double = '<' + 'Double-Button-1' + '>'
        self.canvas_reactor.bind(ev_double, self.toggle_window_mode_on_click)

        self.chat_card = ctk.CTkFrame(
            self.frame_chat, 
            fg_color=HUD_THEME["panel_inner"],
            corner_radius=10, border_color=HUD_THEME["panel_border"], border_width=1.5
        )
        self.chat_card.pack(fill="both", expand=True)

        self.chat_box = ctk.CTkTextbox(
            self.chat_card, 
            font=ctk.CTkFont(family="Consolas", size=12),
            fg_color="transparent",
            text_color=HUD_THEME["text_bright"],
            border_width=0, corner_radius=10
        )
        self.chat_box.pack(fill="both", expand=True, padx=10, pady=10)
        self.chat_box.insert("end", self.t("log_init_1") + self.t("log_init_2") + self.t("log_init_3"))
        self.chat_box.configure(state="disabled")
        self._make_textbox_selectable(self.chat_box)

    def animate_hud(self):
        w = self.canvas_reactor.winfo_width()
        h = self.canvas_reactor.winfo_height()
        listening = bool(self.core.is_listening)
        frame_ms = HUD_FRAME_MS if listening else HUD_IDLE_FRAME_MS
        # Коэффициенты сглаживания подобраны под старый интервал кадра.
        # Степень сохраняет ту же скорость реакции при более редкой перерисовке.
        ref_ms = 22.0 if listening else 50.0
        motion = frame_ms / ref_ms
        heat_keep = 0.84 ** motion
        spec_keep = 0.76 ** motion

        target_spectrum = self.core.latest_spectrum
        is_speaking = getattr(self.core, 'is_speaking', False)
        is_cat_locked = getattr(self.core, 'keyboard_locked', False)
        total_flux = float(np.sum(target_spectrum))

        raw_heat = total_flux * 3.82 if listening else 0.0
        self.smooth_heat = self.smooth_heat * heat_keep + raw_heat * (1.0 - heat_keep)
        for i in range(NUM_BANDS):
            v_t = target_spectrum[i] if listening else 0.0
            self.smooth_spectrum[i] = self.smooth_spectrum[i] * spec_keep + v_t * (1.0 - spec_keep)

        cw = self.canvas_telemetry.winfo_width()
        ch = self.canvas_telemetry.winfo_height()
        if cw > 30 and ch > 30:
            self.canvas_telemetry.delete("all")
            
            for gy in range(0, ch, 14):
                self.canvas_telemetry.create_line(0, gy, cw, gy, fill="#0B1017", width=1)
            
            self.canvas_telemetry.create_text(
                10, 13, text=f"// {self.t('telemetry_title', 'СТАТУС СИСТЕМЫ')}",
                font=("Consolas", 8, "bold"), fill=HUD_THEME["text_dim"], anchor="w"
            )
            self.canvas_telemetry.create_text(
                cw - 10, 13, text=self.t("telemetry_online", "● АКТИВНО"),
                font=("Consolas", 8, "bold"), fill=HUD_THEME["hud_green"], anchor="e"
            )

            is_never = os.path.exists(MARKER_FILE)
            energy_prefix = self.t("telemetry_energy_label", "ЭНЕРГИЯ:")
            status_fox = self.t("telemetry_fox_full_power") if is_never else self.t("telemetry_fox_wants_sleep")
            pwr_text = f"{energy_prefix} {status_fox}"
            
            bat_color = HUD_THEME["hud_green"] if is_never else "#FFB703"
            
            self.canvas_telemetry.create_text(
                10, 38, text=pwr_text,
                font=("Consolas", 9, "bold"), fill=bat_color, anchor="w"
            )

            bw, bh = 34, 12
            bx1 = cw - 13
            bx0 = bx1 - bw
            by0, by1 = 32, 44
            self.canvas_telemetry.create_rectangle(bx0, by0, bx1, by1, outline=bat_color, width=1)
            self.canvas_telemetry.create_rectangle(bx1, by0 + 3, bx1 + 3, by1 - 3, fill=bat_color, outline="")
            
            seg_w, seg_gap = 8, 2
            active_segments = 3 if is_never else 1
            for i in range(3):
                sx0 = bx0 + 3 + i * (seg_w + seg_gap)
                fill_col = bat_color if i < active_segments else ""
                self.canvas_telemetry.create_rectangle(sx0, by0 + 2, sx0 + seg_w, by1 - 2, fill=fill_col, outline=bat_color if fill_col == "" else "")

            self.canvas_telemetry.create_text(
                10, 64, text="👂 СЛУХ:",
                font=("Consolas", 8, "bold"), fill=HUD_THEME["text_dim"], anchor="w"
            )
            
            eq_x = 58
            y_base = 70
            max_eq_h = 13
            for bar_i in range(12):
                bx = eq_x + bar_i * 5
                self.canvas_telemetry.create_line(bx, y_base, bx, y_base - max_eq_h, fill=HUD_THEME["text_dark"], width=2)
                bh_val = max(1, int(self.smooth_spectrum[bar_i] * max_eq_h)) if listening else 1
                b_color = HUD_THEME["hud_cyan"] if listening else HUD_THEME["panel_border"]
                self.canvas_telemetry.create_line(bx, y_base, bx, y_base - bh_val, fill=b_color, width=2)

            heat_display = self.smooth_heat if listening else 0.0
            self.canvas_telemetry.create_text(
                10, 92, text=f"🔥 ТЕПЛО: {heat_display:5.2f} KTS",
                font=("Consolas", 8, "bold"), fill="#FFB703", anchor="w"
            )

            tx = cw - 18
            tube_top = 56
            tube_bot = 88
            tube_h = tube_bot - tube_top
            
            self.canvas_telemetry.create_rectangle(tx - 1, tube_top, tx + 2, tube_bot, fill=HUD_THEME["text_dark"], outline="")
            merc_h = min(tube_h - 2, max(2, int((heat_display / 100.0) * (tube_h - 2))))
            self.canvas_telemetry.create_rectangle(tx - 1, tube_bot - merc_h, tx + 2, tube_bot, fill="#FFB703", outline="")
            self.canvas_telemetry.create_oval(tx - 4, 88, tx + 5, 97, fill="#FB8500", outline=HUD_THEME["panel_border"], width=1)
            for tick in range(3):
                tick_y = tube_top + 4 + tick * 10
                self.canvas_telemetry.create_line(tx + 4, tick_y, tx + 7, tick_y, fill=HUD_THEME["panel_border_light"], width=1)

        if w > 40 and h > 40:
            self.canvas_reactor.delete("all")
            cx, cy = w / 2, h / 2

            grid_gap = 26
            for gx in range(0, int(w), grid_gap):
                self.canvas_reactor.create_line(gx, 0, gx, h, fill="#0A0E15", width=1)
            for gy in range(0, int(h), grid_gap):
                self.canvas_reactor.create_line(0, gy, w, gy, fill="#0A0E15", width=1)

            self.scanline_y = (self.scanline_y + 1.8 * motion) % h
            self.canvas_reactor.create_line(0, self.scanline_y, w, self.scanline_y, fill=HUD_THEME["hud_cyan_dim"], width=1)

            clock_name = self.t("clock_prefix", "ЛИСЬЕ_ВРЕМЯ")
            self.hud_clock_lbl.configure(text=f"{clock_name}: {time.strftime('%H:%M:%S')}")

            if not listening:
                if getattr(self, "fox_avatar_dim_tk", None):
                    self.canvas_reactor.create_image(cx, cy - 6, image=self.fox_avatar_dim_tk)
                elif self.fox_avatar_tk:
                    self.canvas_reactor.create_image(cx, cy - 6, image=self.fox_avatar_tk)
                else:
                    self.canvas_reactor.create_text(
                        cx, cy, text="🦊", font=("Segoe UI Emoji", 56), fill=HUD_THEME["text_dark"]
                    )

                if h > 300:
                    status_standby = self.t("btn_sleep", "💤 РЕЖИМ ТИШИНЫ")
                    self.canvas_reactor.create_text(
                        cx, cy + (min(w, h) * 0.40), text=f"[ {status_standby} // STANDBY ]",
                        font=("Consolas", 10, "bold"), fill=HUD_THEME["text_dim"]
                    )
                self.after(frame_ms, self.animate_hud)
                return

            self.flame_time += 0.085 * motion
            self.rot_ring_inner += 0.016 * motion
            self.rot_ring_outer -= 0.011 * motion

            low_energy = float(np.mean(target_spectrum[:6])) if len(target_spectrum) >= 6 else 0.0

            if is_cat_locked:
                col_light = "#F0ABFC"
                col_dark = "#701A75"
            else:
                col_light = HUD_THEME["flame_gold"]
                col_dark = HUD_THEME["flame_crimson"]

            r_inner = min(w, h) * 0.43
            self.canvas_reactor.create_oval(
                cx - r_inner, cy - r_inner, cx + r_inner, cy + r_inner,
                outline=HUD_THEME["panel_border"], width=1, dash=(4, 8)
            )
            inner_runes = ["狐", "火", "霊", "気", "神", "幻", "炎", "魂", "零", "芯", "術", "零"]
            for idx, sym in enumerate(inner_runes):
                ang = math.radians(idx * (360 / len(inner_runes))) + self.rot_ring_inner
                rx = cx + r_inner * math.cos(ang)
                ry = cy + r_inner * math.sin(ang)
                self.canvas_reactor.create_text(
                    rx, ry, text=sym, font=("Consolas", 9, "bold"),
                    fill=HUD_THEME["hud_cyan"] if idx % 2 == 0 else "#FFB703"
                )

            r_outer = min(w, h) * 0.47
            self.canvas_reactor.create_oval(
                cx - r_outer, cy - r_outer, cx + r_outer, cy + r_outer,
                outline=HUD_THEME["panel_border_light"], width=1, dash=(2, 6)
            )
            outer_runes = ["キツネ", "システム", "コア", "フレーム", "ネオン", "マトリックス", "プロト", "アイ"]
            for idx, sym in enumerate(outer_runes):
                ang = math.radians(idx * (360 / len(outer_runes))) + self.rot_ring_outer
                rx = cx + r_outer * math.cos(ang)
                ry = cy + r_outer * math.sin(ang)
                self.canvas_reactor.create_text(
                    rx, ry, text=sym, font=("Consolas", 8, "bold"),
                    fill="#FB8500" if idx % 2 == 0 else HUD_THEME["hud_cyan"]
                )

            r_radar = min(w, h) * 0.36
            for deg in range(0, 360, 6):
                rad = math.radians(deg) - self.rot_ring_inner * 0.5
                is_major = (deg % 30 == 0)
                l_tick = 8 if is_major else 3
                col_tick = "#FFB703" if is_major else HUD_THEME["panel_border"]
                x0 = cx + (r_radar - l_tick) * math.cos(rad)
                y0 = cy + (r_radar - l_tick) * math.sin(rad)
                x1 = cx + r_radar * math.cos(rad)
                y1 = cy + r_radar * math.sin(rad)
                self.canvas_reactor.create_line(x0, y0, x1, y1, fill=col_tick, width=1.5 if is_major else 1)

            num_radial_pts = 36
            base_r = min(w, h) * 0.20

            def build_compact_flame_ring(layer_scale, time_mult, noise_mult, color_fill):
                pts = []
                for i in range(num_radial_pts):
                    theta = (2 * math.pi * i) / num_radial_pts
                    band_idx = int((i / num_radial_pts) * 14) % NUM_BANDS
                    spec_val = target_spectrum[band_idx]

                    upward_boost = max(0.0, -math.sin(theta)) * 14.0
                    wave1 = math.sin(self.flame_time * time_mult + i * 0.7) * 5.5
                    wave2 = math.cos(self.flame_time * (time_mult * 1.2) - i * 1.0) * 3.8
                    
                    voice_flare = (spec_val * 22.0 + low_energy * 10.0) * (0.8 + 0.2 * math.sin(i + self.flame_time))

                    r = (base_r * layer_scale) + (wave1 + wave2) * noise_mult + upward_boost + voice_flare
                    px = cx + r * math.cos(theta)
                    py = cy + r * math.sin(theta)
                    pts.extend([px, py])

                if len(pts) >= 6:
                    self.canvas_reactor.create_polygon(pts, fill=color_fill, outline="", smooth=True)

            build_compact_flame_ring(1.30, 1.8, 1.1, HUD_THEME["flame_smoke"] if not is_cat_locked else "#3B0764")
            build_compact_flame_ring(1.15, 2.5, 0.85, col_dark)
            build_compact_flame_ring(1.00, 3.3, 0.55, col_light)

            if self.fox_avatar_tk:
                self.canvas_reactor.create_image(cx, cy - 2, image=self.fox_avatar_tk)
            else:
                self.canvas_reactor.create_text(cx, cy, text="🦊", font=("Segoe UI Emoji", 56), fill=col_light)

            if h > 300:
                if is_cat_locked:
                    status_txt = self.t("status_cat_active")
                elif is_speaking:
                    status_txt = self.t("status_talking")
                elif low_energy > 0.1:
                    status_txt = self.t("status_listening")
                else:
                    status_txt = self.t("status_ready")

                bx, by, bw_b, bh_b = cx, cy + (min(w, h) * 0.40), 150, 20
                badge_pts = [
                    bx - bw_b, by - bh_b,
                    bx + bw_b - 8, by - bh_b,
                    bx + bw_b, by - bh_b + 8,
                    bx + bw_b, by + bh_b,
                    bx - bw_b + 8, by + bh_b,
                    bx - bw_b, by + bh_b - 8
                ]
                self.canvas_reactor.create_polygon(badge_pts, fill="#080C14", outline="#FB8500", width=1.5)
                self.canvas_reactor.create_text(
                    bx, by + 1, text=f"🦊 {status_txt}",
                    font=("Consolas", 9, "bold"), fill="#FFB703"
                )

            sz, pad = 20, 14
            self.canvas_reactor.create_line(pad, pad + sz, pad, pad + 6, pad + 6, pad, pad + sz, pad, fill=HUD_THEME["hud_cyan"], width=2)
            self.canvas_reactor.create_line(w - pad - sz, pad, w - pad - 6, pad, w - pad, pad + 6, w - pad, pad + sz, fill=HUD_THEME["hud_cyan"], width=2)
            self.canvas_reactor.create_line(pad, h - pad - sz, pad, h - pad - 6, pad + 6, h - pad, pad + sz, h - pad, fill=HUD_THEME["hud_cyan"], width=2)
            self.canvas_reactor.create_line(w - pad - sz, h - pad, w - pad - 6, h - pad, w - pad, h - pad - 6, w - pad, h - pad - sz, fill=HUD_THEME["hud_cyan"], width=2)

        self.after(frame_ms, self.animate_hud)

    # =========================================================================
    # РЕДАКТОР СВИТКА ПОВАДОК (МУЛЬТИ-ШАГОВЫЙ КОНСТРУКТОР)
    # =========================================================================

    def build_editor_screen(self):
        self.frame_editor = ctk.CTkFrame(self.container, fg_color="transparent")
        
        top_bar = ctk.CTkFrame(self.frame_editor, fg_color="transparent")
        top_bar.pack(fill="x", pady=(0, 10))
        
        self.lbl_editor_title = ctk.CTkLabel(
            top_bar, text=self.t("editor_title"), 
            font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
            text_color=HUD_THEME["text_bright"]
        )
        self.lbl_editor_title.pack(side="left")
        
        self.btn_load_packs = ctk.CTkButton(
            top_bar, text=self.t("editor_btn_load_packs"), 
            fg_color=HUD_THEME["panel_card"],
            hover_color="#FB8500",
            text_color=HUD_THEME["text_bright"],
            border_color=HUD_THEME["panel_border"], border_width=1.5,
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            height=32, corner_radius=6,
            command=self.load_sample_packs
        )
        self.btn_load_packs.pack(side="right", padx=(8, 0))

        self.btn_new_cmd = ctk.CTkButton(
            top_bar, text=self.t("editor_btn_new"), 
            fg_color="#FB8500",
            hover_color="#D94400",
            text_color=HUD_THEME["chassis_dark"],
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            height=32, corner_radius=6,
            command=self.create_new_command_form
        )
        self.btn_new_cmd.pack(side="right")

        split_box = ctk.CTkFrame(self.frame_editor, fg_color="transparent")
        split_box.pack(fill="both", expand=True)

        left_panel = ctk.CTkFrame(
            split_box, width=310,
            fg_color=HUD_THEME["panel_card"],
            border_color=HUD_THEME["panel_border"], border_width=1.5,
            corner_radius=8
        )
        left_panel.pack(side="left", fill="y", padx=(0, 10))
        left_panel.pack_propagate(False)

        self.entry_cmd_search = ctk.CTkEntry(
            left_panel, placeholder_text=self.t("editor_search_ph"),
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            font=ctk.CTkFont(family="Consolas", size=11), corner_radius=6, height=32
        )
        self.entry_cmd_search.pack(fill="x", padx=10, pady=10)
        
        ev_key_release = '<' + 'KeyRelease' + '>'
        self.entry_cmd_search.bind(ev_key_release, lambda e: self.refresh_editor_command_list())

        self.cmd_list_scroll = ctk.CTkScrollableFrame(
            left_panel, fg_color="transparent"
        )
        self.cmd_list_scroll.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self.right_panel = ctk.CTkFrame(
            split_box,
            fg_color=HUD_THEME["panel_card"],
            border_color=HUD_THEME["panel_border"], border_width=1.5,
            corner_radius=8
        )
        self.right_panel.pack(side="right", fill="both", expand=True)

        self.lbl_editor_mode = ctk.CTkLabel(
            self.right_panel, text=self.t("editor_creating_title"),
            font=ctk.CTkFont(family="Consolas", size=13, weight="bold"),
            text_color="#FFB703", anchor="w"
        )
        self.lbl_editor_mode.pack(fill="x", padx=16, pady=(12, 6))

        form_meta = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        form_meta.pack(fill="x", padx=16, pady=(0, 8))

        self.lbl_editor_phrase = ctk.CTkLabel(
            form_meta, text=self.t("editor_lbl_phrase"),
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=HUD_THEME["text_dim"], anchor="w"
        )
        self.lbl_editor_phrase.pack(anchor="w")

        self.entry_cmd_name = ctk.CTkEntry(
            form_meta, placeholder_text=self.t("editor_ph_phrase"),
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6, height=32
        )
        self.entry_cmd_name.pack(fill="x", pady=(2, 6))

        self.lbl_editor_synonyms = ctk.CTkLabel(
            form_meta, text=self.t("editor_lbl_synonyms"),
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=HUD_THEME["text_dim"], anchor="w"
        )
        self.lbl_editor_synonyms.pack(anchor="w")

        self.entry_synonyms = ctk.CTkEntry(
            form_meta, placeholder_text=self.t("editor_ph_synonyms"),
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            font=ctk.CTkFont(family="Consolas", size=11), corner_radius=6, height=32
        )
        self.entry_synonyms.pack(fill="x", pady=(2, 4))

        steps_header_bar = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        steps_header_bar.pack(fill="x", padx=16, pady=(6, 4))

        self.lbl_steps_header = ctk.CTkLabel(
            steps_header_bar, text=self.t("editor_steps_header"),
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            text_color=HUD_THEME["hud_cyan"], anchor="w"
        )
        self.lbl_steps_header.pack(side="left")

        self.btn_add_step = ctk.CTkButton(
            steps_header_bar, text=self.t("editor_btn_add_step"),
            fg_color="#180C11", hover_color="#2A121A",
            border_color="#FB8500", border_width=1.5,
            text_color="#FFB703",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            height=28, corner_radius=6,
            command=lambda: self.add_step_row(action="speak", value="")
        )
        self.btn_add_step.pack(side="right")

        self.steps_container = ctk.CTkScrollableFrame(
            self.right_panel,
            fg_color=HUD_THEME["panel_inner"],
            border_color=HUD_THEME["panel_border"], border_width=1,
            corner_radius=8
        )
        self.steps_container.pack(fill="both", expand=True, padx=16, pady=(0, 10))

        bottom_bar = ctk.CTkFrame(self.right_panel, fg_color="transparent")
        bottom_bar.pack(fill="x", padx=16, pady=(0, 12))

        self.btn_save_cmd = ctk.CTkButton(
            bottom_bar, text=self.t("editor_btn_save_cmd"),
            fg_color="#FB8500", hover_color="#D94400",
            text_color=HUD_THEME["chassis_dark"],
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            height=36, corner_radius=6,
            command=self.save_current_command
        )
        self.btn_save_cmd.pack(side="left", padx=(0, 10))

        self.btn_delete_cmd = ctk.CTkButton(
            bottom_bar, text=self.t("editor_btn_delete_cmd"),
            fg_color="#280C11", hover_color="#450A14",
            border_color="#D90429", border_width=1,
            text_color="#F8FAFC",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            height=36, corner_radius=6,
            command=self.delete_current_command
        )
        self.btn_delete_cmd.pack(side="left", padx=(0, 10))

        self.btn_clear_cmd = ctk.CTkButton(
            bottom_bar, text=self.t("editor_btn_clear"),
            fg_color="transparent", hover_color=HUD_THEME["panel_inner"],
            text_color=HUD_THEME["text_dim"],
            font=ctk.CTkFont(family="Consolas", size=11),
            height=36, corner_radius=6,
            command=self.create_new_command_form
        )
        self.btn_clear_cmd.pack(side="left")

        self.refresh_editor_command_list()
        self.create_new_command_form()

    def refresh_editor_command_list(self):
        for widget in self.cmd_list_scroll.winfo_children():
            widget.destroy()

        search_q = self.entry_cmd_search.get().lower().strip()
        active_dict = self.core.commands_en if self.cur_lang == "en" else self.core.commands_ru

        matched_keys = []
        for cmd_name, cmd_data in active_dict.items():
            syns = " ".join(cmd_data.get("synonyms", [])).lower()
            if not search_q or search_q in cmd_name.lower() or search_q in syns:
                matched_keys.append(cmd_name)

        if not matched_keys:
            lbl_empty = ctk.CTkLabel(
                self.cmd_list_scroll, text=self.t("editor_no_cmds"),
                font=ctk.CTkFont(family="Consolas", size=11),
                text_color=HUD_THEME["text_dim"]
            )
            lbl_empty.pack(pady=20)
            return

        for cmd in matched_keys:
            is_active = (cmd == self.current_editing_cmd)
            step_count = len(active_dict[cmd].get("steps", []))
            
            btn = ctk.CTkButton(
                self.cmd_list_scroll,
                text=f"🦊 {cmd}  [{step_count}]",
                anchor="w",
                font=ctk.CTkFont(family="Consolas", size=11, weight="bold" if is_active else "normal"),
                fg_color=HUD_THEME["panel_inner"] if not is_active else "#22141C",
                hover_color="#2A1620",
                text_color="#FFB703" if is_active else HUD_THEME["text_bright"],
                border_color="#FB8500" if is_active else HUD_THEME["panel_border"],
                border_width=1.5 if is_active else 1,
                corner_radius=6, height=32,
                command=lambda c=cmd: self.load_command_into_editor(c)
            )
            btn.pack(fill="x", pady=2)

    def load_command_into_editor(self, cmd_name):
        active_dict = self.core.commands_en if self.cur_lang == "en" else self.core.commands_ru
        if cmd_name not in active_dict:
            return

        self.current_editing_cmd = cmd_name
        cmd_data = active_dict[cmd_name]

        self.lbl_editor_mode.configure(text=self.t("editor_editing_title").format(name=cmd_name))
        
        self.entry_cmd_name.delete(0, 'end')
        self.entry_cmd_name.insert(0, cmd_name)

        self.entry_synonyms.delete(0, 'end')
        self.entry_synonyms.insert(0, ", ".join(cmd_data.get("synonyms", [])))

        self.clear_step_rows()

        steps = cmd_data.get("steps", [])
        if steps:
            for s in steps:
                self.add_step_row(action=s.get("action", "speak"), value=s.get("value", ""))
        else:
            self.add_step_row(action="speak", value="")

        self.refresh_editor_command_list()

    def create_new_command_form(self):
        self.current_editing_cmd = None
        self.lbl_editor_mode.configure(text=self.t("editor_creating_title"))

        self.entry_cmd_name.delete(0, 'end')
        self.entry_synonyms.delete(0, 'end')

        self.clear_step_rows()
        self.add_step_row(action="speak", value="")
        self.refresh_editor_command_list()

    def clear_step_rows(self):
        for row in self.step_rows:
            row["frame"].destroy()
        self.step_rows.clear()

    def add_step_row(self, action="speak", value=""):
        row_frame = ctk.CTkFrame(
            self.steps_container, fg_color=HUD_THEME["panel_card"],
            border_color=HUD_THEME["panel_border"], border_width=1, corner_radius=6
        )
        row_frame.pack(fill="x", pady=3, padx=2)

        lbl_num = ctk.CTkLabel(
            row_frame, text=f"#{len(self.step_rows) + 1:02d}",
            font=ctk.CTkFont(family="Consolas", size=10, weight="bold"),
            text_color="#FFB703", width=34
        )
        lbl_num.pack(side="left", padx=(8, 4))

        act_values = list(self.actions_dict.keys())
        combo = ctk.CTkComboBox(
            row_frame, values=act_values, width=240,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=11), corner_radius=6,
            state="readonly"  # Запрет на редактирование/стирание текста вручную
        )
        loc_name = self.rev_actions_dict.get(action, act_values[0])
        combo.set(loc_name)
        combo.pack(side="left", padx=4, pady=6)

        entry_val = ctk.CTkEntry(
            row_frame, placeholder_text=self.t("editor_ph_param"),
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            font=ctk.CTkFont(family="Consolas", size=11), corner_radius=6
        )
        entry_val.insert(0, str(value))
        entry_val.pack(side="left", fill="x", expand=True, padx=4, pady=6)

        row_data = {
            "frame": row_frame,
            "lbl_num": lbl_num,
            "combo": combo,
            "entry": entry_val,
            "action_key": action
        }

        btn_del = ctk.CTkButton(
            row_frame, text="✕", width=28, height=28,
            fg_color="#200B0E", hover_color="#450A14",
            border_color="#D90429", border_width=1,
            text_color="#F8FAFC",
            font=ctk.CTkFont(family="Consolas", size=11, weight="bold"),
            corner_radius=6,
            command=lambda r=row_data: self.remove_step_row(r)
        )
        btn_del.pack(side="right", padx=(4, 8), pady=6)

        self.step_rows.append(row_data)

    def remove_step_row(self, row_data):
        if row_data in self.step_rows:
            self.step_rows.remove(row_data)
            row_data["frame"].destroy()
            self.renumber_step_rows()

        if len(self.step_rows) == 0:
            self.add_step_row(action="speak", value="")

    def renumber_step_rows(self):
        for idx, row in enumerate(self.step_rows):
            row["lbl_num"].configure(text=f"#{idx + 1:02d}")

    def save_current_command(self):
        name = self.entry_cmd_name.get().lower().strip()
        if not name:
            return

        raw_syns = self.entry_synonyms.get().split(",")
        synonyms = [s.strip().lower() for s in raw_syns if s.strip()]

        steps = []
        for row in self.step_rows:
            chosen_display = row["combo"].get()
            act_key = self.actions_dict.get(chosen_display, "speak")
            val = row["entry"].get().strip()
            steps.append({"action": act_key, "value": val})

        if not steps:
            steps = [{"action": "speak", "value": "OK"}]

        self.core.save_command_definition(
            cmd_name=name,
            steps=steps,
            synonyms=synonyms,
            old_name=self.current_editing_cmd,
            lang=self.cur_lang
        )

        self.current_editing_cmd = name
        self.lbl_editor_mode.configure(text=self.t("editor_editing_title").format(name=name))
        self.refresh_editor_command_list()
        self.update_chat("System", self.t("editor_status_saved").format(name=name))

    def delete_current_command(self):
        if not self.current_editing_cmd:
            return

        cmd_to_del = self.current_editing_cmd
        if self.core.delete_command(cmd_to_del, lang=self.cur_lang):
            self.create_new_command_form()
            self.update_chat("System", self.t("editor_status_deleted").format(name=cmd_to_del))

    # =========================================================================
    # НАСТРОЙКИ
    # =========================================================================

    def build_settings_screen(self):
        self.frame_settings = ctk.CTkFrame(self.container, fg_color="transparent")
        
        self.lbl_settings_title = ctk.CTkLabel(
            self.frame_settings, text=self.t("settings_title"), 
            font=ctk.CTkFont(family="Consolas", size=18, weight="bold"),
            text_color=HUD_THEME["text_bright"]
        )
        self.lbl_settings_title.pack(anchor="w", pady=(0, 16))
        
        settings_shell = ctk.CTkFrame(
            self.frame_settings,
            fg_color=HUD_THEME["panel_card"],
            border_color=HUD_THEME["panel_border"], border_width=1.5,
            corner_radius=8
        )
        settings_shell.pack(fill="both", expand=True, padx=2, pady=10)
        box = ctk.CTkScrollableFrame(settings_shell, fg_color="transparent")
        box.pack(fill="both", expand=True, padx=4, pady=4)
        
        self.check_wake = ctk.CTkCheckBox(
            box, text=self.t("settings_chk_wake"),
            text_color=HUD_THEME["text_bright"],
            fg_color="#FB8500",
            hover_color="#D94400",
            font=ctk.CTkFont(family="Consolas", size=12)
        )
        if self.core.config.get("require_wake_word", True):
            self.check_wake.select()
        self.check_wake.pack(anchor="w", padx=22, pady=(22, 12))
        
        self.lbl_settings_wake = ctk.CTkLabel(box, text=self.t("settings_lbl_wake"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_wake.pack(anchor="w", padx=22, pady=(10, 2))
        
        self.entry_wake = ctk.CTkEntry(
            box, width=320, 
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6
        )
        self.entry_wake.insert(0, self.core.config.get("wake_word", "лисичка"))
        self.entry_wake.pack(anchor="w", padx=22, pady=(0, 16))

        self.lbl_settings_lang = ctk.CTkLabel(box, text=self.t("settings_lbl_lang"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_lang.pack(anchor="w", padx=22, pady=(5, 2))
        
        self.combo_lang = ctk.CTkComboBox(
            box, values=list(LANG_OPTIONS.keys()), width=400,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6,
            command=self.on_language_selected,
            state="readonly"  # Запрет на редактирование
        )
        initial_lang_label = "🇷🇺 Русский (RU)" if self.cur_lang == "ru" else "🇬🇧 English (EN)"
        self.combo_lang.set(initial_lang_label)
        self.combo_lang.pack(anchor="w", padx=22, pady=(0, 16))

        self.lbl_settings_mic = ctk.CTkLabel(box, text=self.t("settings_lbl_mic"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_mic.pack(anchor="w", padx=22, pady=(5, 2))

        input_devices = self.get_available_input_devices()
        mic_display_values = [self.t("mic_default")] + [d[1] for d in input_devices]

        self.combo_mic = ctk.CTkComboBox(
            box, values=mic_display_values, width=400,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6,
            state="readonly"  # Запрет на редактирование
        )
        cur_mic = self.core.config.get("microphone", "")
        if not cur_mic or cur_mic not in mic_display_values:
            self.combo_mic.set(mic_display_values[0])
        else:
            self.combo_mic.set(cur_mic)
        self.combo_mic.pack(anchor="w", padx=22, pady=(0, 16))

        self.lbl_settings_voice = ctk.CTkLabel(box, text=self.t("settings_lbl_voice"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_voice.pack(anchor="w", padx=22, pady=(5, 2))
        
        self.combo_voice = ctk.CTkComboBox(
            box, values=list(self.voice_options.keys()), width=400,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6,
            state="readonly"  # Запрет на редактирование
        )
        self.combo_voice.set(self._voice_display_for_config())
        self.combo_voice.pack(anchor="w", padx=22, pady=(0, 16))

        # --- Выбор голоса Silero и kokoro (только полный режим) ---
        self.silero_speakers = tts_local.silero_speakers() if tts_local else ["baya"]
        self.kokoro_voices = tts_local.kokoro_voices() if tts_local else ["sveta"]

        self.lbl_settings_silero = ctk.CTkLabel(box, text=self.t("settings_lbl_silero"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_kokoro = ctk.CTkLabel(box, text=self.t("settings_lbl_kokoro"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))

        self.combo_silero = ctk.CTkComboBox(
            box, values=self.silero_speakers, width=200,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6,
            state="readonly"
        )
        cur_silero = self.core.config.get("silero_speaker", "") or "baya"
        self.combo_silero.set(cur_silero if cur_silero in self.silero_speakers else self.silero_speakers[0])

        self.combo_kokoro = ctk.CTkComboBox(
            box, values=self.kokoro_voices, width=200,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6,
            state="readonly"
        )
        cur_kokoro = self.core.config.get("kokoro_voice", "") or "sveta"
        self.combo_kokoro.set(cur_kokoro if cur_kokoro in self.kokoro_voices else self.kokoro_voices[0])

        if self._full_mode():
            self.lbl_settings_silero.pack(anchor="w", padx=22, pady=(5, 2))
            self.combo_silero.pack(anchor="w", padx=22, pady=(0, 16))
            self.lbl_settings_kokoro.pack(anchor="w", padx=22, pady=(5, 2))
            self.combo_kokoro.pack(anchor="w", padx=22, pady=(0, 16))
        
        self.lbl_settings_timeout = ctk.CTkLabel(box, text=self.t("settings_lbl_timeout"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_timeout.pack(anchor="w", padx=22, pady=(5, 2))
        
        self.entry_timeout = ctk.CTkEntry(
            box, width=120,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6
        )
        self.entry_timeout.insert(0, str(self.core.config.get("wake_timeout", 7.0)))
        self.entry_timeout.pack(anchor="w", padx=22, pady=(0, 16))

        self.check_grammar = ctk.CTkCheckBox(
            box, text=self.t("settings_chk_grammar"),
            text_color=HUD_THEME["text_bright"],
            fg_color="#00F0FF",
            hover_color="#0E3846",
            font=ctk.CTkFont(family="Consolas", size=12)
        )
        if self.core.config.get("asr_grammar", True):
            self.check_grammar.select()
        self.check_grammar.pack(anchor="w", padx=22, pady=(4, 6))

        self.check_asr_debug = ctk.CTkCheckBox(
            box, text=self.t("settings_chk_asr_debug"),
            text_color=HUD_THEME["text_bright"],
            fg_color="#FFB703",
            hover_color="#D94400",
            font=ctk.CTkFont(family="Consolas", size=12)
        )
        if self.core.config.get("asr_debug", False):
            self.check_asr_debug.select()
        self.check_asr_debug.pack(anchor="w", padx=22, pady=(0, 16))

        self.lbl_settings_asr_engine = ctk.CTkLabel(box, text=self.t("settings_lbl_asr_engine"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_asr_engine.pack(anchor="w", padx=22, pady=(5, 2))

        self.asr_engine_values = self._asr_engine_options()
        self.combo_asr_engine = ctk.CTkComboBox(
            box, values=list(self.asr_engine_values.keys()), width=400,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6,
            state="readonly"  # Запрет на редактирование
        )
        self._set_engine_combo(self.core.config.get("asr_engine", "auto"))
        self.combo_asr_engine.pack(anchor="w", padx=22, pady=(0, 16))

        self.lbl_settings_weather = ctk.CTkLabel(box, text=self.t("settings_lbl_weather"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_weather.pack(anchor="w", padx=22, pady=(5, 2))
        self.weather_provider_labels = {"wttr.in": "wttr", "WeatherAPI.com": "weatherapi"}
        self.combo_weather = ctk.CTkComboBox(
            box, values=list(self.weather_provider_labels.keys()), width=400,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6,
            state="readonly"
        )
        current_provider = str(self.core.config.get("weather_provider", "weatherapi")).lower()
        self.combo_weather.set("wttr.in" if current_provider == "wttr" else "WeatherAPI.com")
        self.combo_weather.pack(anchor="w", padx=22, pady=(0, 12))

        self.lbl_settings_weather_place = ctk.CTkLabel(box, text=self.t("settings_lbl_weather_place"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_weather_place.pack(anchor="w", padx=22, pady=(5, 2))
        try:
            import weather as weather_mod
            place_values = list(weather_mod.PRESET_LOCATIONS)
        except Exception:
            place_values = ["Долгопрудный", "Сити", "Москва", "Коломна", "Питер", "Барнаул"]
        self.combo_weather_place = ctk.CTkComboBox(
            box, values=place_values, width=400,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            dropdown_fg_color=HUD_THEME["panel_card"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6
        )
        self.combo_weather_place.set(str(self.core.config.get("weather_location") or "Долгопрудный"))
        self.combo_weather_place.pack(anchor="w", padx=22, pady=(0, 12))

        self.lbl_settings_weather_key = ctk.CTkLabel(box, text=self.t("settings_lbl_weather_key"), text_color=HUD_THEME["text_dim"], font=ctk.CTkFont(family="Consolas", size=12))
        self.lbl_settings_weather_key.pack(anchor="w", padx=22, pady=(5, 2))
        self.entry_weather_key = ctk.CTkEntry(
            box, width=400,
            fg_color=HUD_THEME["panel_inner"], border_color=HUD_THEME["panel_border"],
            font=ctk.CTkFont(family="Consolas", size=12), corner_radius=6
        )
        self.entry_weather_key.insert(0, str(self.core.config.get("weatherapi_key") or ""))
        self.entry_weather_key.pack(anchor="w", padx=22, pady=(0, 16))

        self.btn_save_settings = ctk.CTkButton(
            box, text=self.t("settings_btn_save"), 
            fg_color="#FB8500",
            hover_color="#D94400",
            font=ctk.CTkFont(family="Consolas", size=12, weight="bold"),
            height=40, corner_radius=6,
            command=self.save_settings
        )
        self.btn_save_settings.pack(anchor="w", padx=22, pady=(0, 22))

    def update_chat(self, sender, text):
        def _call():
            ts = time.strftime("%H:%M:%S")
            prefix = "[SYS_LINK]" if sender == "System" else f"[{sender}]"
            self.chat_box.configure(state="normal")
            self.chat_box.insert("end", f"[{ts}] {prefix}: {text}\n")
            self.chat_box.see("end")
            self.chat_box.configure(state="disabled")
        self.after(0, _call)

    def update_status_badge(self, text, color):
        def _call():
            self.status_lbl.configure(text=f"◆ {text.upper()}", text_color=color)
        self.after(0, _call)

    def save_settings(self):
        self.core.config["require_wake_word"] = bool(self.check_wake.get())
        self.core.config["wake_word"] = self.entry_wake.get().lower().strip()
        
        chosen_lang_label = self.combo_lang.get()
        new_lang = LANG_OPTIONS.get(chosen_lang_label, "ru")
        self.core.config["language"] = new_lang

        choice = self.combo_voice.get()
        tts_engine_changed = False
        if choice in self.voice_options:
            engine_type, voice_name, pitch_mod, rate_mod = self.voice_options[choice]
            # Базовый режим не даёт выбрать локальный движок, но конфиг мог
            # остаться от полного: такие значения не сохраняем.
            if engine_type in ("silero", "kokoro") and not self._full_mode():
                return
            tts_engine_changed = engine_type != str(self.core.config.get("tts_engine", "auto")).lower()
            self.core.config["tts_engine"] = engine_type
            if engine_type == "edge-tts":
                # Голос, питч и темп задаются только у сетевого движка.
                # У локальных свои настройки, а tts_voice остаётся резервом:
                # если модель не поднялась, реплику озвучит привычный edge-голос,
                # а не SAPI с пустым именем голоса.
                self.core.config["tts_voice"] = voice_name
                self.core.config["tts_pitch"] = pitch_mod
                self.core.config["tts_rate_edge"] = rate_mod
            elif engine_type == "silero":
                self.core.config["silero_speaker"] = voice_name
            elif engine_type == "kokoro":
                self.core.config["kokoro_voice"] = voice_name

        if self._full_mode():
            # Конкретный голос выбранного движка: применяется в ядре при
            # пересборке, даже если тип движка не менялся.
            if hasattr(self, "combo_silero") and self.combo_silero.get() in self.silero_speakers:
                if self.core.config.get("silero_speaker") != self.combo_silero.get():
                    tts_engine_changed = True
                self.core.config["silero_speaker"] = self.combo_silero.get()
            if hasattr(self, "combo_kokoro") and self.combo_kokoro.get() in self.kokoro_voices:
                if self.core.config.get("kokoro_voice") != self.combo_kokoro.get():
                    tts_engine_changed = True
                self.core.config["kokoro_voice"] = self.combo_kokoro.get()

        chosen_mic_label = self.combo_mic.get()
        default_mic_text = self.t("mic_default")
        if chosen_mic_label == default_mic_text or not chosen_mic_label:
            self.core.config["microphone"] = ""
        else:
            self.core.config["microphone"] = chosen_mic_label

        self.core.config["asr_grammar"] = bool(self.check_grammar.get())
        self.core.config["asr_debug"] = bool(self.check_asr_debug.get())
        selected_engine = self.asr_engine_values.get(self.combo_asr_engine.get(), "auto")
        if selected_engine in ("whisper_gpu", "gpu"):
            selected_engine = "whisper"
        if not self._full_mode():
            # Базовый режим: whisper недоступен независимо от выбора в списке.
            selected_engine = "vosk"
        engine_changed = selected_engine != str(self.core.config.get("asr_engine", "auto")).lower()
        self.core.config["asr_engine"] = selected_engine

        try:
            self.core.config["wake_timeout"] = float(self.entry_timeout.get())
        except ValueError:
            self.core.config["wake_timeout"] = 7.0

        chosen_weather = self.weather_provider_labels.get(self.combo_weather.get(), "weatherapi")
        self.core.config["weather_provider"] = chosen_weather
        chosen_place = self.combo_weather_place.get().strip()
        self.core.config["weather_location"] = chosen_place or "Долгопрудный"
        self.core.config["weatherapi_key"] = self.entry_weather_key.get().strip()

        # Полный режим: недостающие пакеты и веса выбранных движков качаем сейчас.
        if self._full_mode() and full_deps is not None:
            pending = dict(self.core.config)
            jobs = []
            whisper_model = full_deps.whisper_model_for_engine(
                selected_engine, self.core.config.get("whisper_model", "")
            )
            if full_deps.needs_for_asr_engine(selected_engine, whisper_model=whisper_model):
                jobs.append(("asr", whisper_model or None))
            tts_want = str(self.core.config.get("tts_engine", "auto")).lower()
            if tts_want in ("silero", "kokoro") and full_deps.needs_for_tts_engine(tts_want):
                jobs.append((tts_want, None))
            if jobs:
                self.core.save_config()
                self._start_component_install(jobs, pending_config=pending)
                return

        self.core.save_config()
        if engine_changed:
            self.core._init_asr_engine()
        if tts_engine_changed:
            self.core._init_tts_engine()
        self.core.rebuild_recognizer()
        self.core.reset_wake_state()

    def load_sample_packs(self):
        self.core.load_all_commands()
        self.refresh_editor_command_list()
        self.update_chat("System", self.t("editor_packs_reloaded"))

    def show_chat(self):
        self.frame_editor.pack_forget()
        self.frame_settings.pack_forget()
        self.frame_chat.pack(fill="both", expand=True)
        self._highlight_active_nav("chat")

    def show_editor(self):
        self.frame_chat.pack_forget()
        self.frame_settings.pack_forget()
        self.frame_editor.pack(fill="both", expand=True)
        self._highlight_active_nav("editor")

    def show_settings(self):
        self.frame_chat.pack_forget()
        self.frame_editor.pack_forget()
        self.frame_settings.pack(fill="both", expand=True)
        self._highlight_active_nav("settings")

    def toggle_listen(self):
        if not self.core.is_listening:
            self.btn_listen.configure(
                text=self.t("btn_sleep"),
                fg_color="#180C11",
                hover_color="#2A121A",
                border_color="#FB8500",
                border_width=1.5
            )
            self.core.start_listening()
        else:
            self.btn_listen.configure(
                text=self.t("btn_wake"),
                fg_color="#FB8500",
                hover_color="#D94400",
                border_width=0
            )
            self.core.stop_listening()
            self.core.latest_spectrum = [0.0] * NUM_BANDS

if __name__ == "__main__":
    app = FoxAssistantApp()
    app.mainloop()