import json
import os
import sys
from pathlib import Path

from . import APP_NAME

IS_WIN = os.name == "nt"

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("APPDATA") or Path.home() / ".config") / APP_NAME
CONFIG_PATH = DATA_DIR / "config.json"
TEMP_DIR = Path(os.environ.get("TEMP") or "/tmp") / APP_NAME

RESOLUTIONS = {"720p": 720, "1080p": 1080, "2K (1440p)": 1440}
FPS_OPTIONS = [30, 60]
DURATIONS = [15, 30, 45, 60, 90, 120, 180, 300, 600, 900, 1200]
BITRATES = ["Авто", "8", "12", "16", "20", "30", "40", "50"]

# Битрейт по умолчанию (Мбит/с) для разрешения и частоты кадров
AUTO_BITRATE = {(720, 30): 8, (720, 60): 12, (1080, 30): 12, (1080, 60): 20,
                (1440, 30): 20, (1440, 60): 30}

DEFAULTS = {
    # горячие клавиши
    "hotkey_save": "alt+f10",
    "hotkey_toggle": "alt+shift+f10",
    "hotkey_window": "alt+f9",
    "hotkey_replays": "alt+f11",
    # видео
    "replay_seconds": 60,
    "height": 1440,
    "fps": 30,
    "bitrate": "Авто",
    "encoder": "auto",          # auto | h264_nvenc | h264_amf | h264_qsv | libx264
    "capture": "auto",          # auto | ddagrab | gdigrab | test
    "monitor": 0,
    "draw_mouse": True,
    # звук
    "system_audio": True,
    "system_device": "",        # "" = устройство вывода по умолчанию
    "system_volume": 100,       # %
    "mic_enabled": False,
    "mic_device": "",
    "mic_volume": 100,          # %
    "mic_gain_db": 0,           # усиление, дБ
    # общее
    "output_dir": str(Path.home() / "Videos" / APP_NAME),
    "autostart": False,
    "start_minimized": False,
    "sound": True,
    "notifications": True,
    "appearance": "dark",       # dark | light | system
    "ffmpeg_path": "",
}

# Ключи, изменение которых требует перезапуска записи
RECORDER_KEYS = {"replay_seconds", "height", "fps", "bitrate", "encoder", "capture", "monitor",
                 "draw_mouse", "system_audio", "system_device", "system_volume", "mic_enabled",
                 "mic_device", "mic_volume", "mic_gain_db"}
# Ключи, после изменения которых старый буфер нельзя склеивать с новым
VIDEO_KEYS = {"height", "fps", "encoder", "capture", "monitor", "system_audio", "mic_enabled", "mic_device"}


def load():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"Ошибка чтения {CONFIG_PATH}: {e}")
    return cfg


def save(cfg):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def bitrate_mbps(cfg):
    if str(cfg["bitrate"]).isdigit():
        return int(cfg["bitrate"])
    return AUTO_BITRATE.get((int(cfg["height"]), int(cfg["fps"])), 20)
