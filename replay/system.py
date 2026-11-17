"""Платформенные вещи: ffmpeg, мониторы, устройства, автозагрузка, один экземпляр."""
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
from pathlib import Path

from . import APP_NAME
from .config import BASE_DIR, IS_WIN, TEMP_DIR

NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW


def run_quiet(args, timeout=20):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, timeout=timeout, creationflags=NO_WINDOW)


def find_ffmpeg(custom=""):
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    candidates = [Path(custom)] if custom else []
    candidates += [BASE_DIR / exe, BASE_DIR / "ffmpeg" / exe, BASE_DIR / "ffmpeg" / "bin" / exe]
    if getattr(sys, "_MEIPASS", None):
        candidates.append(Path(sys._MEIPASS) / exe)
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError("ffmpeg не найден.\nПоложите ffmpeg.exe рядом с программой "
                            "или укажите путь в настройках.")


# ---------------- мониторы ----------------
def list_monitors():
    """[(index, 'Монитор 1 — 2560×1440 (основной)')]"""
    if not IS_WIN:
        return [(0, "Монитор 1")]
    import ctypes
    from ctypes import wintypes
    mons = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    proc_t = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _lp):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        r = mi.rcMonitor
        mons.append((r.right - r.left, r.bottom - r.top, bool(mi.dwFlags & 1)))
        return 1

    try:
        ctypes.windll.user32.EnumDisplayMonitors(None, None, proc_t(cb), 0)
    except Exception:  # noqa: BLE001
        return [(0, "Монитор 1")]
    return [(i, f"Монитор {i + 1} — {w}×{h}" + (" (основной)" if prim else ""))
            for i, (w, h, prim) in enumerate(mons)] or [(0, "Монитор 1")]


# ---------------- микрофоны (DirectShow) ----------------
def list_dshow_audio(ffmpeg):
    if not IS_WIN:
        return []
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"])
    except Exception:  # noqa: BLE001
        return []
    out, section, devices = r.stderr.decode("utf-8", errors="ignore"), None, []
    for line in out.splitlines():
        if "DirectShow audio devices" in line:
            section = "audio"
        elif "DirectShow video devices" in line:
            section = "video"
        m = re.search(r'\]\s+"(.+)"(?:\s+\((audio|video)\))?\s*$', line)
        if m and "Alternative name" not in line:
            kind = m.group(2) or section
            if kind == "audio" and m.group(1) not in devices:
                devices.append(m.group(1))
    return devices


# ---------------- автозагрузка ----------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")
    script = BASE_DIR / "instant_replay.py"
    return f'"{pyw if pyw.exists() else py}" "{script}" --minimized'


def set_autostart(enabled):
    if not IS_WIN:
        return
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP_NAME)
            except FileNotFoundError:
                pass


def get_autostart():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP_NAME)
            return True
    except OSError:
        return False


# ---------------- один экземпляр ----------------
PORT_FILE = TEMP_DIR / "instance.port"


def signal_existing_instance():
    """Если программа уже запущена — просим её показать окно и возвращаем True."""
    try:
        port = int(PORT_FILE.read_text())
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            s.sendall(b"show")
        return True
    except Exception:  # noqa: BLE001
        return False


def start_instance_server(on_show):
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(2)
    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(srv.getsockname()[1]))

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                with conn:
                    if conn.recv(16) == b"show":
                        on_show()
            except Exception:  # noqa: BLE001
                return
    threading.Thread(target=loop, daemon=True).start()


def open_path(path):
    if IS_WIN:
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def show_in_explorer(path):
    if IS_WIN:
        subprocess.Popen(["explorer", "/select,", str(path)])
    else:
        open_path(Path(path).parent)
