"""Главное окно, трей и горячие клавиши."""
import json
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
from PIL import Image

from . import APP_NAME, APP_TITLE, audio, config, system
from .icons import app_icon, save_ico
from .recorder import ENCODER_NAMES, Recorder, make_thumbnail

try:
    import keyboard
except Exception:  # noqa: BLE001
    keyboard = None
try:
    import pystray
except Exception:  # noqa: BLE001
    pystray = None

ACCENT = "#c01878"
ACCENT_HOVER = "#9c1262"
CARD = ("#ffffff", "#1f2125")
CARD_BORDER = ("#e3e5e8", "#2b2e33")
SIDEBAR = ("#eef0f2", "#16171a")
BG = ("#f6f7f8", "#111214")
MUTED = ("#6b7078", "#9096a0")
STATUS_COLORS = {"recording": ACCENT, "paused": "#e0a800", "error": "#e5484d",
                 "stopped": "#7a7f87", "starting": "#4c8dff"}

ENCODERS = {"Авто": "auto", "NVIDIA NVENC": "h264_nvenc", "AMD AMF": "h264_amf",
            "Intel QuickSync": "h264_qsv", "Процессор (x264)": "libx264"}
APPEARANCE = {"Тёмная": "dark", "Светлая": "light", "Как в системе": "system"}
HOTKEYS = [("hotkey_save", "Сохранить мгновенный повтор"),
           ("hotkey_toggle", "Пауза / продолжить запись"),
           ("hotkey_window", "Открыть окно программы"),
           ("hotkey_replays", "Открыть галерею повторов")]
MAX_GALLERY = 60


def fmt_duration(sec):
    sec = int(round(sec))
    if sec < 60:
        return f"{sec} с"
    m, s = divmod(sec, 60)
    return f"{m} мин" + (f" {s} с" if s else "")


def fmt_clock(sec):
    sec = int(round(sec))
    return f"{sec // 60}:{sec % 60:02d}"


def fmt_size(b):
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if b < 1024 or unit == "ГБ":
            return f"{b:.0f} {unit}" if unit in ("Б", "КБ") else f"{b:.1f} {unit}"
        b /= 1024
    return ""


def pretty_hotkey(hk):
    return " + ".join(p.capitalize() if len(p) > 1 else p.upper() for p in hk.split("+")) if hk else "—"


def _patch_theme():
    t = ctk.ThemeManager.theme
    neutral, neutral_hover = ["#e7e9ec", "#2a2d33"], ["#d9dce0", "#353941"]
    text = ["#1d1f23", "#e6e8eb"]
    t["CTkOptionMenu"].update(fg_color=neutral, button_color=neutral_hover,
                              button_hover_color=["#cfd3d8", "#40454e"], text_color=text)
    t["CTkComboBox"].update(button_color=neutral_hover, border_color=neutral_hover)
    t["CTkSegmentedButton"].update(selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                                   unselected_color=neutral, unselected_hover_color=neutral_hover,
                                   fg_color=neutral, text_color=text)
    t["CTkSwitch"].update(progress_color=ACCENT)
    t["CTkSlider"].update(progress_color=ACCENT, button_color=ACCENT, button_hover_color=ACCENT_HOVER)
    t["CTkProgressBar"].update(progress_color=ACCENT)
    t["CTkEntry"].update(fg_color=neutral, border_color=neutral_hover)
    t["CTkScrollbar"].update(button_color=neutral_hover, button_hover_color=["#cfd3d8", "#40454e"])
    t["DropdownMenu"].update(fg_color=["#ffffff", "#24272c"], hover_color=neutral_hover, text_color=text)


class Card(ctk.CTkFrame):
    def __init__(self, master, title=None, **kw):
        super().__init__(master, fg_color=CARD, border_color=CARD_BORDER, border_width=1,
                         corner_radius=14, **kw)
        self.grid_columnconfigure(0, weight=1)
        self.row = 0
        if title:
            ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=15, weight="bold"),
                         anchor="w").grid(row=0, column=0, columnspan=3, sticky="ew", padx=18, pady=(14, 4))
            self.row = 1

    def add_row(self, label, widget_factory, hint=None):
        f = ctk.CTkFrame(self, fg_color="transparent")
        f.grid(row=self.row, column=0, sticky="ew", padx=18, pady=7)
        f.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(f, text=label, anchor="w").grid(row=0, column=0, sticky="w")
        if hint:
            ctk.CTkLabel(f, text=hint, anchor="w", text_color=MUTED,
                         font=ctk.CTkFont(size=12)).grid(row=1, column=0, sticky="w")
        w = widget_factory(f)
        w.grid(row=0, column=1, rowspan=2, sticky="e", padx=(12, 0))
        self.row += 1
        return w

    def pad_bottom(self):
        ctk.CTkFrame(self, fg_color="transparent", height=8).grid(row=self.row, column=0)
        self.row += 1


class App(ctk.CTk):
    def __init__(self, start_hidden=False):
        self.cfg = config.load()
        if config.IS_WIN:
            # автозагрузку мог включить установщик — берём состояние из реестра
            self.cfg["autostart"] = system.get_autostart()
            if self.cfg["autostart"]:
                try:
                    system.set_autostart(True)  # обновить путь к exe
                except Exception:  # noqa: BLE001
                    pass
        ctk.set_appearance_mode(self.cfg.get("appearance", "dark"))
        ctk.set_default_color_theme("green")
        _patch_theme()
        super().__init__(fg_color=BG)
        self.title(APP_TITLE)
        self.geometry("1040x680")
        self.minsize(940, 600)
        try:
            ico = save_ico(config.TEMP_DIR / "icon.ico")
            self.iconbitmap(str(ico))
        except Exception:  # noqa: BLE001
            pass

        self.q = queue.Queue()
        self.rec = None
        self.recorder_error = ""
        self.tray = None
        self.pending_keys = set()
        self.restart_job = None
        self.capturing_hotkey = None
        self.last_saved = None
        self.thumb_index_path = config.TEMP_DIR / "thumbs" / "index.json"
        self.thumb_index = self._load_thumb_index()
        self.gallery_cards = {}
        self.hidden_hint_shown = False
        self.toast_job = None

        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew", padx=24, pady=20)
        self.content.grid_columnconfigure(0, weight=1)
        self.content.grid_rowconfigure(1, weight=1)
        self.page_title = ctk.CTkLabel(self.content, text="", font=ctk.CTkFont(size=24, weight="bold"),
                                       anchor="w")
        self.page_title.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        self.pages = {}
        self._build_home()
        self._build_gallery()
        self._build_video()
        self._build_audio()
        self._build_hotkeys()
        self._build_general()
        self.show_page("home")

        self.protocol("WM_DELETE_WINDOW", self.hide_window)
        self.after(50, self._poll_queue)
        self.after(1000, self._tick)


        system.start_instance_server(lambda: self.q.put(self.show_window))
        self._start_tray()
        self._register_hotkeys()
        threading.Thread(target=self._init_recorder, daemon=True).start()
        threading.Thread(target=self._load_devices, daemon=True).start()
        if start_hidden:
            self.withdraw()

    # ================= общее =================
    def call(self, fn, *args):
        """Выполнить fn в потоке интерфейса."""
        self.q.put(lambda: fn(*args))

    def _poll_queue(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    def log(self, msg):
        system.safe_print(f"[{datetime.now():%H:%M:%S}] {msg}")

    def set_cfg(self, key, value):
        if self.cfg.get(key) == value:
            return
        self.cfg[key] = value
        config.save(self.cfg)
        if key in config.RECORDER_KEYS:
            self.pending_keys.add(key)
            if self.restart_job:
                self.after_cancel(self.restart_job)
            self.restart_job = self.after(900, self._apply_recorder_settings)
        if key.startswith("hotkey_"):
            self._register_hotkeys()
            self._update_hotkey_labels()
        if key in ("replay_seconds", "height", "fps", "bitrate"):
            self._update_video_hint()

    def _apply_recorder_settings(self):
        self.restart_job = None
        keys, self.pending_keys = self.pending_keys, set()
        if not self.rec:
            return
        clear = bool(keys & config.VIDEO_KEYS)
        cfg = dict(self.cfg)
        threading.Thread(target=lambda: self.rec.restart(cfg, clear), daemon=True).start()
        self.toast("Настройки применены" + (", буфер записи начат заново" if clear else ""))

    # ================= запись =================
    def _init_recorder(self):
        try:
            rec = Recorder(self.cfg, self.log)
            rec.start()
            self.rec = rec
            self.recorder_error = ""
            self.log(f"Захват: {rec.capture}, энкодер: {rec.encoder}")
        except Exception as e:  # noqa: BLE001
            self.recorder_error = str(e)
            self.log(f"Ошибка запуска записи: {e}")
            self.call(self.show_window)

    def save_replay(self):
        if not self.rec:
            self.toast("Запись ещё не запущена", error=True)
            return

        def worker():
            out = self.rec.save_replay()
            self.call(self._on_saved, out)
        threading.Thread(target=worker, daemon=True).start()

    def _on_saved(self, out):
        if self.cfg["sound"]:
            self._beep(out is not None)
        if out:
            self.last_saved = out
            self.last_label.configure(text=f"Последний повтор: {out.name}")
            self.toast(f"Повтор сохранён: {out.name}")
            self.notify(f"Повтор сохранён ({fmt_duration(self.cfg['replay_seconds'])})")
            if self.current_page == "gallery":
                self.refresh_gallery()
        else:
            self.toast("Не удалось сохранить повтор", error=True)
            self.notify("Не удалось сохранить повтор")

    def toggle_pause(self):
        if not self.rec:
            return
        paused = not self.rec.paused
        threading.Thread(target=lambda: self.rec.set_paused(paused), daemon=True).start()
        self.toast("Запись на паузе" if paused else "Запись продолжена")
        self.notify("Запись на паузе" if paused else "Запись продолжена")
        self.after(300, self._update_tray)

    def _beep(self, ok):
        if config.IS_WIN:
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
            except Exception:  # noqa: BLE001
                pass

    def _tick(self):
        self._update_status()
        self.after(1000, self._tick)

    def _status(self):
        if self.recorder_error:
            return "error", self.recorder_error
        if not self.rec:
            return "starting", "Запуск…"
        return self.rec.status()

    def _update_status(self):
        state, text = self._status()
        color = STATUS_COLORS.get(state, "#888")
        self.status_dot.configure(text_color=color)
        self.status_text.configure(text=text)
        self.side_status.configure(text="●  " + {"recording": "Запись", "paused": "Пауза",
                                                 "error": "Ошибка", "starting": "Запуск",
                                                 "stopped": "Стоп"}.get(state, ""),
                                   text_color=color)
        c = self.cfg
        res = {720: "720p", 1080: "1080p", 1440: "2K"}.get(int(c["height"]), f"{c['height']}p")
        enc = ENCODER_NAMES.get(self.rec.encoder, self.rec.encoder) if self.rec else "…"
        self.status_info.configure(text=f"{res} · {c['fps']} FPS · H.264 · {config.bitrate_mbps(c)} Мбит/с · {enc}")
        total = int(c["replay_seconds"])
        buf = self.rec.buffered_seconds() if self.rec else 0
        self.buffer_bar.set(buf / total if total else 0)
        self.buffer_label.configure(text=f"Буфер: {fmt_clock(buf)} / {fmt_clock(total)}")
        paused = bool(self.rec and self.rec.paused)
        self.pause_btn.configure(text="▶  Продолжить" if paused else "⏸  Пауза")
        if self.tray and getattr(self, "_tray_state", None) != state:
            self._update_tray()

    # ================= боковая панель =================
    def _build_sidebar(self):
        sb = ctk.CTkFrame(self, width=220, corner_radius=0, fg_color=SIDEBAR)
        sb.grid(row=0, column=0, sticky="nsw")
        sb.grid_propagate(False)
        sb.grid_columnconfigure(0, weight=1)
        logo = ctk.CTkImage(app_icon(64), size=(34, 34))
        head = ctk.CTkFrame(sb, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=18, pady=(22, 26))
        ctk.CTkLabel(head, image=logo, text="").pack(side="left")
        ctk.CTkLabel(head, text="  Instant Replay", font=ctk.CTkFont(size=17, weight="bold")).pack(side="left")
        self.nav = {}
        items = [("home", "⌂   Главная"), ("gallery", "▶   Повторы"), ("video", "▣   Видео"),
                 ("audio", "♪   Звук"), ("hotkeys", "⌨   Горячие клавиши"), ("general", "⚙   Общие")]
        for i, (key, text) in enumerate(items, start=1):
            b = ctk.CTkButton(sb, text=text, anchor="w", height=40, corner_radius=10,
                              fg_color="transparent", text_color=("#1d1f23", "#d7dae0"),
                              hover_color=("#e0e3e7", "#24272c"), font=ctk.CTkFont(size=14),
                              command=lambda k=key: self.show_page(k))
            b.grid(row=i, column=0, sticky="ew", padx=12, pady=2)
            self.nav[key] = b
        sb.grid_rowconfigure(len(items) + 1, weight=1)
        self.side_status = ctk.CTkLabel(sb, text="●  Запуск", font=ctk.CTkFont(size=13, weight="bold"))
        self.side_status.grid(row=len(items) + 2, column=0, sticky="w", padx=24, pady=(0, 18))

    def show_page(self, key):
        titles = {"home": "Главная", "gallery": "Мгновенные повторы", "video": "Видео",
                  "audio": "Звук", "hotkeys": "Горячие клавиши", "general": "Общие настройки"}
        for k, p in self.pages.items():
            if k == key:
                p.grid(row=1, column=0, sticky="nsew")
            else:
                p.grid_forget()
        for k, b in self.nav.items():
            b.configure(fg_color=(ACCENT, ACCENT) if k == key else "transparent",
                        text_color=("#ffffff", "#ffffff") if k == key else ("#1d1f23", "#d7dae0"),
                        hover_color=(ACCENT_HOVER, ACCENT_HOVER) if k == key else ("#e0e3e7", "#24272c"))
        self.page_title.configure(text=titles[key])
        self.current_page = key
        if key == "gallery":
            self.refresh_gallery()

    def _page(self, key, scroll=True):
        if scroll:
            p = ctk.CTkScrollableFrame(self.content, fg_color="transparent")
        else:
            p = ctk.CTkFrame(self.content, fg_color="transparent")
        p.grid_columnconfigure(0, weight=1)
        self.pages[key] = p
        return p

    # ================= Главная =================
    def _build_home(self):
        p = self._page("home")
        st = Card(p)
        st.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        top = ctk.CTkFrame(st, fg_color="transparent")
        top.grid(row=0, column=0, sticky="ew", padx=22, pady=(20, 6))
        self.status_dot = ctk.CTkLabel(top, text="●", font=ctk.CTkFont(size=26))
        self.status_dot.pack(side="left")
        self.status_text = ctk.CTkLabel(top, text="Запуск…", font=ctk.CTkFont(size=20, weight="bold"))
        self.status_text.pack(side="left", padx=10)
        self.status_info = ctk.CTkLabel(st, text="", text_color=MUTED, anchor="w")
        self.status_info.grid(row=1, column=0, sticky="ew", padx=22)
        self.buffer_bar = ctk.CTkProgressBar(st, height=10, progress_color=ACCENT)
        self.buffer_bar.grid(row=2, column=0, sticky="ew", padx=22, pady=(16, 4))
        self.buffer_bar.set(0)
        self.buffer_label = ctk.CTkLabel(st, text="", text_color=MUTED, anchor="w", font=ctk.CTkFont(size=12))
        self.buffer_label.grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 18))

        act = Card(p)
        act.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        act.grid_columnconfigure((0, 1, 2), weight=1)
        self.save_btn = ctk.CTkButton(act, text="⏺  Сохранить повтор", height=56, corner_radius=12,
                                      font=ctk.CTkFont(size=17, weight="bold"), fg_color=ACCENT,
                                      hover_color=ACCENT_HOVER, text_color="#ffffff",
                                      command=self.save_replay)
        self.save_btn.grid(row=0, column=0, columnspan=3, sticky="ew", padx=22, pady=(22, 6))
        self.save_hint = ctk.CTkLabel(act, text="", text_color=MUTED, font=ctk.CTkFont(size=12))
        self.save_hint.grid(row=1, column=0, columnspan=3, pady=(0, 12))
        kw = dict(height=40, corner_radius=10, fg_color=("#e7e9ec", "#2a2d33"),
                  hover_color=("#d9dce0", "#353941"), text_color=("#1d1f23", "#e6e8eb"))
        self.pause_btn = ctk.CTkButton(act, text="⏸  Пауза", command=self.toggle_pause, **kw)
        self.pause_btn.grid(row=2, column=0, sticky="ew", padx=(22, 6), pady=(0, 22))
        ctk.CTkButton(act, text="▶  Галерея повторов", command=lambda: self.show_page("gallery"),
                      **kw).grid(row=2, column=1, sticky="ew", padx=6, pady=(0, 22))
        ctk.CTkButton(act, text="📁  Открыть папку", command=self.open_output_dir,
                      **kw).grid(row=2, column=2, sticky="ew", padx=(6, 22), pady=(0, 22))

        hk = Card(p, title="Горячие клавиши")
        hk.grid(row=2, column=0, sticky="ew")
        self.home_hotkeys = {}
        for key, label in HOTKEYS:
            self.home_hotkeys[key] = hk.add_row(
                label, lambda f: ctk.CTkLabel(f, text="", font=ctk.CTkFont(size=13, weight="bold"),
                                              fg_color=("#eceef1", "#2a2d33"), corner_radius=6, padx=10))
        hk.pad_bottom()
        self.last_label = ctk.CTkLabel(p, text="", text_color=MUTED, anchor="w")
        self.last_label.grid(row=3, column=0, sticky="ew", pady=10)
        self._update_hotkey_labels()

    # ================= Галерея =================
    def _build_gallery(self):
        p = self._page("gallery", scroll=False)
        p.grid_rowconfigure(1, weight=1)
        bar = ctk.CTkFrame(p, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.gallery_info = ctk.CTkLabel(bar, text="", text_color=MUTED)
        self.gallery_info.pack(side="left")
        kw = dict(height=34, width=130, corner_radius=8, fg_color=("#e7e9ec", "#2a2d33"),
                  hover_color=("#d9dce0", "#353941"), text_color=("#1d1f23", "#e6e8eb"))
        ctk.CTkButton(bar, text="📁  Папка", command=self.open_output_dir, **kw).pack(side="right")
        ctk.CTkButton(bar, text="⟳  Обновить", command=self.refresh_gallery, **kw).pack(side="right", padx=8)
        self.gallery = ctk.CTkScrollableFrame(p, fg_color="transparent")
        self.gallery.grid(row=1, column=0, sticky="nsew")
        for c in range(3):
            self.gallery.grid_columnconfigure(c, weight=1, uniform="g")
        self.placeholder = ctk.CTkImage(Image.new("RGB", (16, 9), (40, 43, 48)), size=(256, 144))

    def _load_thumb_index(self):
        try:
            return json.loads(self.thumb_index_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def refresh_gallery(self):
        for w in self.gallery.winfo_children():
            w.destroy()
        self.gallery_cards = {}
        try:
            files = sorted(Path(self.cfg["output_dir"]).glob("*.mp4"),
                           key=lambda f: f.stat().st_mtime, reverse=True)
        except OSError:
            files = []
        total = sum(f.stat().st_size for f in files)
        self.gallery_info.configure(
            text=f"{len(files)} видео · {fmt_size(total)}" +
                 (f"  (показаны последние {MAX_GALLERY})" if len(files) > MAX_GALLERY else ""))
        if not files:
            ctk.CTkLabel(self.gallery, text=f"Повторов пока нет.\nНажмите {pretty_hotkey(self.cfg['hotkey_save'])}, "
                                            "чтобы сохранить последние секунды игры.",
                         text_color=MUTED, font=ctk.CTkFont(size=14), justify="center"
                         ).grid(row=0, column=0, columnspan=3, pady=80)
            return
        todo = []
        for i, f in enumerate(files[:MAX_GALLERY]):
            card = self._gallery_card(f)
            card.grid(row=i // 3, column=i % 3, sticky="nsew", padx=6, pady=6)
            info = self.thumb_index.get(f.name)
            thumb = config.TEMP_DIR / "thumbs" / (f.stem + ".jpg")
            if info and thumb.exists() and info.get("mtime") == int(f.stat().st_mtime):
                self._set_card_thumb(f.name, thumb, info.get("duration", 0))
            else:
                todo.append((f, thumb))
        if todo and self.rec:
            threading.Thread(target=self._make_thumbs, args=(todo,), daemon=True).start()

    def _gallery_card(self, f):
        card = Card(self.gallery)
        img = ctk.CTkLabel(card, text="", image=self.placeholder, cursor="hand2", corner_radius=8)
        img.grid(row=0, column=0, columnspan=3, padx=10, pady=(10, 6))
        img.bind("<Button-1>", lambda _e: system.open_path(f))
        try:
            when = datetime.strptime(f.stem.replace("Replay_", ""), "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            when = datetime.fromtimestamp(f.stat().st_mtime)
        ctk.CTkLabel(card, text=when.strftime("%d.%m.%Y  %H:%M:%S"), anchor="w",
                     font=ctk.CTkFont(size=14, weight="bold")).grid(row=1, column=0, columnspan=3,
                                                                    sticky="ew", padx=14)
        meta = ctk.CTkLabel(card, text=fmt_size(f.stat().st_size), anchor="w", text_color=MUTED,
                            font=ctk.CTkFont(size=12))
        meta.grid(row=2, column=0, columnspan=3, sticky="ew", padx=14)
        btns = ctk.CTkFrame(card, fg_color="transparent")
        btns.grid(row=3, column=0, columnspan=3, sticky="ew", padx=10, pady=(6, 12))
        btns.grid_columnconfigure(0, weight=1)
        ctk.CTkButton(btns, text="▶  Смотреть", height=30, corner_radius=8, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER, text_color="#ffffff",
                      command=lambda: system.open_path(f)).grid(row=0, column=0, sticky="ew")
        kw = dict(width=36, height=30, corner_radius=8, fg_color=("#e7e9ec", "#2a2d33"),
                  hover_color=("#d9dce0", "#353941"), text_color=("#1d1f23", "#e6e8eb"))
        ctk.CTkButton(btns, text="📁", command=lambda: system.show_in_explorer(f),
                      **kw).grid(row=0, column=1, padx=(6, 0))
        ctk.CTkButton(btns, text="🗑", command=lambda: self._delete_replay(f),
                      **kw).grid(row=0, column=2, padx=(6, 0))
        self.gallery_cards[f.name] = (img, meta, f)
        return card

    def _make_thumbs(self, todo):
        for f, thumb in todo:
            try:
                dur = make_thumbnail(self.rec.ffmpeg, f, thumb)
                self.thumb_index[f.name] = {"duration": dur, "mtime": int(f.stat().st_mtime)}
                self.call(self._set_card_thumb, f.name, thumb, dur)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.thumb_index_path.parent.mkdir(parents=True, exist_ok=True)
            self.thumb_index_path.write_text(json.dumps(self.thumb_index), encoding="utf-8")
        except OSError:
            pass

    def _set_card_thumb(self, name, thumb, dur):
        if name not in self.gallery_cards:
            return
        img, meta, f = self.gallery_cards[name]
        try:
            im = Image.open(thumb)
            im.load()
            ratio = im.width / im.height
            img.configure(image=ctk.CTkImage(im, size=(256, int(256 / ratio))))
        except Exception:  # noqa: BLE001
            pass
        meta.configure(text=f"{fmt_clock(dur)}  ·  {fmt_size(f.stat().st_size)}")

    def _delete_replay(self, f):
        if messagebox.askyesno(APP_TITLE, f"Удалить {f.name}?", parent=self):
            try:
                f.unlink()
                (config.TEMP_DIR / "thumbs" / (f.stem + ".jpg")).unlink(missing_ok=True)
            except OSError as e:
                messagebox.showerror(APP_TITLE, f"Не удалось удалить: {e}", parent=self)
            self.refresh_gallery()

    # ================= Видео =================
    def _build_video(self):
        p = self._page("video")
        c = Card(p, title="Мгновенный повтор")
        c.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        dur_labels = {fmt_duration(s): s for s in config.DURATIONS}
        cur = fmt_duration(self.cfg["replay_seconds"])
        if cur not in dur_labels:
            dur_labels[cur] = int(self.cfg["replay_seconds"])
        c.add_row("Длительность повтора", lambda f: ctk.CTkOptionMenu(
            f, values=list(dur_labels), width=160, variable=ctk.StringVar(value=cur),
            command=lambda v: self.set_cfg("replay_seconds", dur_labels[v])),
            hint="Сколько последних секунд сохраняется по горячей клавише")
        res_by_h = {v: k for k, v in config.RESOLUTIONS.items()}
        c.add_row("Разрешение", lambda f: ctk.CTkSegmentedButton(
            f, values=list(config.RESOLUTIONS), variable=ctk.StringVar(value=res_by_h.get(int(self.cfg["height"]), "2K (1440p)")),
            command=lambda v: self.set_cfg("height", config.RESOLUTIONS[v])),
            hint="Высота кадра; ширина подбирается по пропорциям монитора")
        c.add_row("Частота кадров", lambda f: ctk.CTkSegmentedButton(
            f, values=[f"{x} FPS" for x in config.FPS_OPTIONS], selected_color=ACCENT,
            selected_hover_color=ACCENT_HOVER, variable=ctk.StringVar(value=f"{self.cfg['fps']} FPS"),
            command=lambda v: self.set_cfg("fps", int(v.split()[0]))))
        br_labels = {("Авто" if b == "Авто" else f"{b} Мбит/с"): b for b in config.BITRATES}
        cur_br = next((k for k, v in br_labels.items() if v == str(self.cfg["bitrate"])), "Авто")
        c.add_row("Битрейт", lambda f: ctk.CTkOptionMenu(
            f, values=list(br_labels), width=160, variable=ctk.StringVar(value=cur_br),
            command=lambda v: self.set_cfg("bitrate", br_labels[v])),
            hint="Чем выше, тем лучше качество и больше размер файла")
        self.video_hint = ctk.CTkLabel(c, text="", text_color=MUTED, anchor="w", font=ctk.CTkFont(size=12))
        self.video_hint.grid(row=c.row, column=0, sticky="ew", padx=18, pady=(4, 14))
        c.row += 1
        self._update_video_hint()

        c2 = Card(p, title="Захват")
        c2.grid(row=1, column=0, sticky="ew")
        mons = system.list_monitors()
        mon_labels = {name: i for i, name in mons}
        cur_mon = next((n for n, i in mon_labels.items() if i == int(self.cfg["monitor"])), mons[0][1])
        c2.add_row("Монитор", lambda f: ctk.CTkOptionMenu(
            f, values=list(mon_labels), width=260, variable=ctk.StringVar(value=cur_mon),
            command=lambda v: self.set_cfg("monitor", mon_labels[v])))
        cur_enc = next((k for k, v in ENCODERS.items() if v == self.cfg["encoder"]), "Авто")
        c2.add_row("Кодировщик", lambda f: ctk.CTkOptionMenu(
            f, values=list(ENCODERS), width=200, variable=ctk.StringVar(value=cur_enc),
            command=lambda v: self.set_cfg("encoder", ENCODERS[v])),
            hint="«Авто» выбирает видеокарту: NVIDIA → AMD → Intel → процессор")
        c2.add_row("Показывать курсор", lambda f: self._switch(f, "draw_mouse"))
        c2.pad_bottom()

    def _update_video_hint(self):
        if not hasattr(self, "video_hint"):
            return
        mb = config.bitrate_mbps(self.cfg) * int(self.cfg["replay_seconds"]) / 8
        self.video_hint.configure(text=f"Примерный размер одного повтора: {mb:.0f} МБ")

    def _switch(self, master, key, command=None):
        var = ctk.BooleanVar(value=bool(self.cfg[key]))

        def on():
            self.set_cfg(key, var.get())
            if command:
                command(var.get())
        return ctk.CTkSwitch(master, text="", width=46, variable=var, command=on, progress_color=ACCENT)

    def _slider(self, master, key, lo, hi, fmt, steps=None):
        f = ctk.CTkFrame(master, fg_color="transparent")
        val = ctk.CTkLabel(f, text=fmt(self.cfg[key]), width=64, anchor="e")

        def moved(v):
            val.configure(text=fmt(round(v)))
            self.set_cfg(key, int(round(v)))
        s = ctk.CTkSlider(f, from_=lo, to=hi, number_of_steps=steps or (hi - lo), width=240,
                          command=moved, progress_color=ACCENT, button_color=ACCENT,
                          button_hover_color=ACCENT_HOVER)
        s.set(self.cfg[key])
        s.pack(side="left")
        val.pack(side="left", padx=(8, 0))
        return f

    # ================= Звук =================
    def _build_audio(self):
        p = self._page("audio")
        c = Card(p, title="Звук системы (игра, музыка, голосовой чат)")
        c.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        c.add_row("Записывать звук системы", lambda f: self._switch(f, "system_audio"),
                  hint=None if audio.available() else "Нужен пакет pyaudiowpatch")
        self.sys_dev_menu = c.add_row("Устройство вывода", lambda f: ctk.CTkOptionMenu(
            f, values=[audio.DEFAULT_LABEL], width=300,
            variable=ctk.StringVar(value=self.cfg["system_device"] or audio.DEFAULT_LABEL),
            command=lambda v: self.set_cfg("system_device", "" if v == audio.DEFAULT_LABEL else v)))
        c.add_row("Громкость в видео", lambda f: self._slider(f, "system_volume", 0, 200, lambda v: f"{v}%"),
                  hint="100% — как слышно у вас")
        c.pad_bottom()

        m = Card(p, title="Микрофон")
        m.grid(row=1, column=0, sticky="ew")
        m.add_row("Записывать микрофон", lambda f: self._switch(f, "mic_enabled"))
        self.mic_menu = m.add_row("Микрофон", lambda f: ctk.CTkOptionMenu(
            f, values=[self.cfg["mic_device"] or "Загрузка…"], width=300,
            variable=ctk.StringVar(value=self.cfg["mic_device"] or "Загрузка…"),
            command=lambda v: self.set_cfg("mic_device", v)))
        m.add_row("Громкость", lambda f: self._slider(f, "mic_volume", 0, 100, lambda v: f"{v}%"))
        m.add_row("Усиление", lambda f: self._slider(f, "mic_gain_db", 0, 30, lambda v: f"+{v} дБ"),
                  hint="Для тихих микрофонов. Слишком большое усиление даст шум")
        ctk.CTkButton(m, text="⟳  Обновить список устройств", height=32, corner_radius=8,
                      fg_color=("#e7e9ec", "#2a2d33"), hover_color=("#d9dce0", "#353941"),
                      text_color=("#1d1f23", "#e6e8eb"),
                      command=lambda: threading.Thread(target=self._load_devices, daemon=True).start()
                      ).grid(row=m.row, column=0, sticky="w", padx=18, pady=(4, 16))
        m.row += 1

    def _load_devices(self):
        outs = audio.list_output_devices()
        try:
            mics = system.list_dshow_audio(system.find_ffmpeg(self.cfg.get("ffmpeg_path", "")))
        except Exception:  # noqa: BLE001
            mics = []
        self.call(self._set_devices, outs, mics)

    def _set_devices(self, outs, mics):
        self.sys_dev_menu.configure(values=[audio.DEFAULT_LABEL] + outs)
        if not mics:
            self.mic_menu.configure(values=["Микрофоны не найдены"])
            if not self.cfg["mic_device"]:
                self.mic_menu.set("Микрофоны не найдены")
            return
        self.mic_menu.configure(values=mics)
        if self.cfg["mic_device"] not in mics:
            self.mic_menu.set(mics[0])
            self.set_cfg("mic_device", mics[0])

    # ================= Горячие клавиши =================
    def _build_hotkeys(self):
        p = self._page("hotkeys")
        c = Card(p, title="Сочетания клавиш")
        c.grid(row=0, column=0, sticky="ew")
        self.hotkey_buttons = {}
        for key, label in HOTKEYS:
            self.hotkey_buttons[key] = c.add_row(label, lambda f, k=key: ctk.CTkButton(
                f, text="", width=200, height=34, corner_radius=8, fg_color=("#e7e9ec", "#2a2d33"),
                hover_color=("#d9dce0", "#353941"), text_color=("#1d1f23", "#e6e8eb"),
                font=ctk.CTkFont(size=13, weight="bold"), command=lambda: self.capture_hotkey(k)))
        ctk.CTkLabel(c, text="Нажмите на кнопку и затем нужное сочетание. Esc — отмена.\n"
                             "Если игра запущена от администратора, запустите программу тоже от администратора.",
                     text_color=MUTED, font=ctk.CTkFont(size=12), justify="left", anchor="w"
                     ).grid(row=c.row, column=0, sticky="ew", padx=18, pady=(6, 16))
        c.row += 1
        if keyboard is None:
            ctk.CTkLabel(p, text="Модуль keyboard недоступен — горячие клавиши не работают.",
                         text_color="#e5484d").grid(row=1, column=0, sticky="w", pady=10)
        self._update_hotkey_labels()

    def _update_hotkey_labels(self):
        for key, _ in HOTKEYS:
            txt = pretty_hotkey(self.cfg.get(key, ""))
            if hasattr(self, "hotkey_buttons") and key != self.capturing_hotkey:
                self.hotkey_buttons[key].configure(text=txt)
            if hasattr(self, "home_hotkeys"):
                self.home_hotkeys[key].configure(text=txt)
        if hasattr(self, "save_hint"):
            self.save_hint.configure(
                text=f"или нажмите {pretty_hotkey(self.cfg['hotkey_save'])} в любой игре")

    def capture_hotkey(self, key):
        if keyboard is None or self.capturing_hotkey:
            return
        self.capturing_hotkey = key
        self.hotkey_buttons[key].configure(text="Нажмите сочетание…", fg_color=ACCENT, text_color="#ffffff")
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:  # noqa: BLE001
            pass

        def worker():
            try:
                hk = keyboard.read_hotkey(suppress=False)
            except Exception:  # noqa: BLE001
                hk = ""
            self.call(self._hotkey_captured, key, hk)
        threading.Thread(target=worker, daemon=True).start()

    def _hotkey_captured(self, key, hk):
        self.capturing_hotkey = None
        self.hotkey_buttons[key].configure(fg_color=("#e7e9ec", "#2a2d33"), text_color=("#1d1f23", "#e6e8eb"))
        if hk and hk != "esc":
            for other, _ in HOTKEYS:
                if other != key and self.cfg.get(other) == hk:
                    self.toast(f"{pretty_hotkey(hk)} уже используется", error=True)
                    hk = ""
            if hk:
                self.cfg[key] = None  # чтобы set_cfg точно сохранил
                self.set_cfg(key, hk)
        self._register_hotkeys()
        self._update_hotkey_labels()

    def _register_hotkeys(self):
        if keyboard is None:
            return
        try:
            keyboard.unhook_all_hotkeys()
        except Exception:  # noqa: BLE001
            pass
        actions = {"hotkey_save": self.save_replay, "hotkey_toggle": self.toggle_pause,
                   "hotkey_window": self.toggle_window, "hotkey_replays": self.show_gallery}
        for key, fn in actions.items():
            hk = self.cfg.get(key)
            if not hk:
                continue
            try:
                keyboard.add_hotkey(hk, lambda f=fn: self.q.put(f), suppress=False)
            except Exception as e:  # noqa: BLE001
                self.log(f"Не удалось назначить {hk}: {e}")

    # ================= Общие =================
    def _build_general(self):
        p = self._page("general")
        c = Card(p, title="Запуск")
        c.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        c.add_row("Запускать вместе с Windows", lambda f: self._switch(f, "autostart", self._set_autostart),
                  hint="Программа стартует свёрнутой в трей и сразу начинает запись")
        c.add_row("Запускать свёрнутой в трей", lambda f: self._switch(f, "start_minimized"),
                  hint="При ручном запуске окно не показывается")
        c.pad_bottom()

        c2 = Card(p, title="Сохранение")
        c2.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        self.out_entry = c2.add_row("Папка для повторов", self._folder_picker)
        c2.add_row("Звук при сохранении", lambda f: self._switch(f, "sound"))
        c2.add_row("Уведомления Windows", lambda f: self._switch(f, "notifications"))
        c2.pad_bottom()

        c3 = Card(p, title="Внешний вид и прочее")
        c3.grid(row=2, column=0, sticky="ew")
        cur = next((k for k, v in APPEARANCE.items() if v == self.cfg.get("appearance")), "Тёмная")
        c3.add_row("Тема", lambda f: ctk.CTkSegmentedButton(
            f, values=list(APPEARANCE), selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
            variable=ctk.StringVar(value=cur), command=self._set_appearance))
        kw = dict(height=32, corner_radius=8, fg_color=("#e7e9ec", "#2a2d33"),
                  hover_color=("#d9dce0", "#353941"), text_color=("#1d1f23", "#e6e8eb"))
        c3.add_row("Журнал ffmpeg", lambda f: ctk.CTkButton(
            f, text="Открыть", width=120, command=self._open_log, **kw),
            hint="Пригодится, если запись не запускается")
        c3.add_row("Путь к ffmpeg.exe", lambda f: ctk.CTkButton(
            f, text="Выбрать…", width=120, command=self._pick_ffmpeg, **kw),
            hint=self.cfg.get("ffmpeg_path") or "Автоматически (рядом с программой или из PATH)")
        c3.pad_bottom()

    def _folder_picker(self, master):
        f = ctk.CTkFrame(master, fg_color="transparent")
        e = ctk.CTkEntry(f, width=300)
        e.insert(0, self.cfg["output_dir"])
        e.configure(state="readonly")
        e.pack(side="left")

        def pick():
            d = filedialog.askdirectory(initialdir=self.cfg["output_dir"], parent=self)
            if d:
                self.set_cfg("output_dir", str(Path(d)))
                if self.rec:
                    self.rec.cfg["output_dir"] = str(Path(d))
                e.configure(state="normal")
                e.delete(0, "end")
                e.insert(0, str(Path(d)))
                e.configure(state="readonly")
        ctk.CTkButton(f, text="Обзор…", width=90, height=28, corner_radius=8, command=pick,
                      fg_color=("#e7e9ec", "#2a2d33"), hover_color=("#d9dce0", "#353941"),
                      text_color=("#1d1f23", "#e6e8eb")).pack(side="left", padx=(8, 0))
        return f

    def _set_autostart(self, on):
        try:
            system.set_autostart(on)
        except Exception as e:  # noqa: BLE001
            self.toast(f"Не удалось изменить автозагрузку: {e}", error=True)

    def _set_appearance(self, label):
        mode = APPEARANCE[label]
        self.set_cfg("appearance", mode)
        ctk.set_appearance_mode(mode)

    def _open_log(self):
        log = config.TEMP_DIR / "buffer" / "ffmpeg.log"
        if log.exists():
            system.open_path(log)
        else:
            self.toast("Журнал пока пуст", error=True)

    def _pick_ffmpeg(self):
        path = filedialog.askopenfilename(title="ffmpeg.exe", parent=self,
                                          filetypes=[("ffmpeg", "ffmpeg.exe"), ("Все файлы", "*.*")])
        if not path:
            return
        self.set_cfg("ffmpeg_path", path)
        if self.rec:
            self.rec.stop()
        self.rec, self.recorder_error = None, ""
        threading.Thread(target=self._init_recorder, daemon=True).start()
        threading.Thread(target=self._load_devices, daemon=True).start()

    def open_output_dir(self):
        d = Path(self.cfg["output_dir"])
        d.mkdir(parents=True, exist_ok=True)
        system.open_path(d)

    # ================= окно и уведомления =================
    def show_window(self, page=None):
        self.deiconify()
        self.state("normal")
        self.lift()
        self.attributes("-topmost", True)
        self.after(300, lambda: self.attributes("-topmost", False))
        self.focus_force()
        if page:
            self.show_page(page)

    def hide_window(self):
        self.withdraw()
        if not self.hidden_hint_shown:
            self.hidden_hint_shown = True
            self.notify(f"Запись продолжается в фоне. {pretty_hotkey(self.cfg['hotkey_window'])} — открыть окно")

    def toggle_window(self):
        if self.state() == "normal" and self.focus_get() is not None:
            self.hide_window()
        else:
            self.show_window()

    def show_gallery(self):
        self.show_window("gallery")

    def toast(self, text, error=False):
        if self.toast_job:
            self.after_cancel(self.toast_job)
            self._toast.destroy()
        self._toast = ctk.CTkLabel(self, text=f"  {text}  ", corner_radius=10, height=40,
                                   fg_color="#e5484d" if error else ACCENT,
                                   text_color="#ffffff",
                                   font=ctk.CTkFont(size=13, weight="bold"))
        self._toast.place(relx=0.5, rely=1.0, y=-24, anchor="s")
        self.toast_job = self.after(3000, lambda: (self._toast.destroy(), setattr(self, "toast_job", None)))

    def notify(self, text):
        if self.tray and self.cfg.get("notifications"):
            try:
                self.tray.notify(text, APP_TITLE)
            except Exception:  # noqa: BLE001
                pass

    # ================= трей =================
    def _start_tray(self):
        if pystray is None:
            return
        P = pystray
        menu = P.Menu(
            P.MenuItem("Открыть Instant Replay", lambda: self.q.put(self.show_window), default=True),
            P.MenuItem("Сохранить повтор", lambda: self.q.put(self.save_replay)),
            P.MenuItem("Галерея повторов", lambda: self.q.put(self.show_gallery)),
            P.MenuItem("Пауза", lambda: self.q.put(self.toggle_pause),
                       checked=lambda _i: bool(self.rec and self.rec.paused)),
            P.Menu.SEPARATOR,
            P.MenuItem("Выход", lambda: self.q.put(self.quit_app)),
        )
        self.tray = P.Icon(APP_NAME, app_icon(64), APP_TITLE, menu)
        threading.Thread(target=self.tray.run, daemon=True).start()

    def _update_tray(self):
        if not self.tray:
            return
        state, text = self._status()
        self._tray_state = state
        try:
            self.tray.icon = app_icon(64, state)
            self.tray.title = f"{APP_TITLE} — {text}"[:120]
            self.tray.update_menu()
        except Exception:  # noqa: BLE001
            pass

    def quit_app(self):
        if keyboard:
            try:
                keyboard.unhook_all()
            except Exception:  # noqa: BLE001
                pass
        if self.rec:
            self.rec.stop()
        if self.tray:
            try:
                self.tray.stop()
            except Exception:  # noqa: BLE001
                pass
        try:
            system.PORT_FILE.unlink(missing_ok=True)
        except OSError:
            pass
        self.destroy()


def run(start_hidden=False):
    if system.signal_existing_instance():
        return
    if config.IS_WIN:
        try:
            import ctypes
            # по этому мьютексу установщик понимает, что программа запущена
            run.mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "InstantReplayAppMutex")
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    app = App(start_hidden=start_hidden)
    app.mainloop()
    time.sleep(0.2)
