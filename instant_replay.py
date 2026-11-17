"""
Instant Replay — мгновенный повтор для Windows (аналог NVIDIA ShadowPlay).

Постоянно пишет экран в кольцевой буфер из коротких сегментов (H.264),
а по горячей клавише склеивает последние N секунд в MP4 без перекодирования.
"""
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

APP_NAME = "InstantReplay"
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0  # CREATE_NO_WINDOW

if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).resolve().parent

CONFIG_PATH = BASE_DIR / "config.json"

DEFAULT_CONFIG = {
    "hotkey": "alt+f10",            # сохранить повтор
    "toggle_hotkey": "alt+shift+f10",  # пауза / продолжить запись
    "replay_seconds": 60,           # длина повтора
    "width": 2560,
    "height": 1440,
    "fps": 30,
    "bitrate": "20M",
    "encoder": "auto",              # auto | h264_nvenc | h264_amf | h264_qsv | libx264
    "capture": "auto",              # auto | ddagrab | gdigrab | test
    "monitor": 0,                   # номер монитора (для ddagrab)
    "draw_mouse": True,
    "audio_device": "",             # имя DirectShow-устройства, напр. "Stereo Mix (Realtek(R) Audio)"
    "mic_device": "",               # микрофон (необязательно)
    "segment_seconds": 2,
    "output_dir": str(Path.home() / "Videos" / APP_NAME),
    "buffer_dir": "",               # пусто = %TEMP%\InstantReplay
    "ffmpeg_path": "",              # пусто = рядом с программой или из PATH
    "sound": True,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception as e:  # noqa: BLE001
            print(f"Ошибка чтения config.json: {e}; используются настройки по умолчанию")
    else:
        try:
            CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return cfg


def find_ffmpeg(cfg):
    candidates = []
    if cfg.get("ffmpeg_path"):
        candidates.append(Path(cfg["ffmpeg_path"]))
    exe = "ffmpeg.exe" if IS_WIN else "ffmpeg"
    candidates += [BASE_DIR / exe, BASE_DIR / "ffmpeg" / "bin" / exe]
    for c in candidates:
        if c.is_file():
            return str(c)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise FileNotFoundError("ffmpeg не найден. Положите ffmpeg.exe рядом с программой или укажите ffmpeg_path в config.json")


def run_quiet(args, timeout=15):
    return subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          stdin=subprocess.DEVNULL, timeout=timeout, creationflags=NO_WINDOW)


def encoder_works(ffmpeg, enc):
    pix = "yuv420p" if enc == "libx264" else "nv12"
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-loglevel", "error",
                       "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30:d=0.5",
                       "-pix_fmt", pix, "-c:v", enc, "-f", "null", "-"])
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def has_filter(ffmpeg, name):
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-filters"])
        return any(line.split()[1:2] == [name] for line in r.stdout.decode(errors="ignore").splitlines() if line.strip())
    except Exception:  # noqa: BLE001
        return False


class Recorder:
    """Пишет экран в кольцевой буфер сегментов и сохраняет повторы."""

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.ffmpeg = find_ffmpeg(cfg)
        self.proc = None
        self.running = False
        self.paused = False
        self.save_lock = threading.Lock()
        self._watchdog = None
        self.seg = max(1, int(cfg["segment_seconds"]))
        self.wrap = math.ceil(cfg["replay_seconds"] / self.seg) + 4
        self.buffer_dir = Path(cfg["buffer_dir"] or (Path(os.environ.get("TEMP", "/tmp")) / APP_NAME))
        self.output_dir = Path(cfg["output_dir"])
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.encoder = cfg["encoder"]
        if self.encoder == "auto":
            for enc in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264"):
                if encoder_works(self.ffmpeg, enc):
                    self.encoder = enc
                    break
            else:
                raise RuntimeError("Не найден ни один рабочий H.264-энкодер в ffmpeg")
        self.capture = cfg["capture"]
        if self.capture == "auto":
            self.capture = "ddagrab" if IS_WIN and has_filter(self.ffmpeg, "ddagrab") else "gdigrab"
        self.log(f"ffmpeg: {self.ffmpeg}")
        self.log(f"Захват: {self.capture}, энкодер: {self.encoder}, "
                 f"{cfg['width']}x{cfg['height']}@{cfg['fps']}, буфер {cfg['replay_seconds']} c")

    # ---------- построение команды ffmpeg ----------
    def _build_cmd(self):
        c = self.cfg
        fps, w, h = int(c["fps"]), int(c["width"]), int(c["height"])
        mouse = "1" if c["draw_mouse"] else "0"
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]

        if self.capture == "ddagrab":
            args += ["-f", "lavfi", "-i",
                     f"ddagrab=output_idx={int(c['monitor'])}:framerate={fps}:draw_mouse={mouse}"]
            vf = "hwdownload,format=bgra,"
        elif self.capture == "gdigrab":
            args += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", mouse,
                     "-thread_queue_size", "512", "-i", "desktop"]
            vf = ""
        else:  # test
            args += ["-re", "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:r={fps}"]
            vf = ""

        audio_inputs = []
        for dev in (c.get("audio_device"), c.get("mic_device")):
            if dev:
                args += ["-f", "dshow", "-audio_buffer_size", "50", "-thread_queue_size", "512",
                         "-i", f"audio={dev}"]
                audio_inputs.append(len(audio_inputs) + 1)

        pix = "yuv420p" if self.encoder == "libx264" else "nv12"
        vf += f"scale={w}:{h}:flags=bicubic,format={pix}"
        args += ["-map", "0:v"]
        if len(audio_inputs) == 1:
            args += ["-map", f"{audio_inputs[0]}:a"]
        elif len(audio_inputs) == 2:
            args += ["-filter_complex", "[1:a][2:a]amix=inputs=2:duration=longest:normalize=0[aout]",
                     "-map", "[aout]"]
        args += ["-vf", vf, "-r", str(fps)]

        br = str(c["bitrate"])
        gop = fps * self.seg
        enc = self.encoder
        if enc == "h264_nvenc":
            args += ["-c:v", enc, "-preset", "p4", "-tune", "ll", "-rc", "vbr",
                     "-b:v", br, "-maxrate", br, "-bufsize", br, "-bf", "0"]
        elif enc == "h264_amf":
            args += ["-c:v", enc, "-usage", "lowlatency", "-quality", "speed", "-rc", "vbr_peak",
                     "-b:v", br, "-maxrate", br, "-bufsize", br]
        elif enc == "h264_qsv":
            args += ["-c:v", enc, "-preset", "veryfast", "-b:v", br, "-maxrate", br, "-bufsize", br]
        else:
            args += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                     "-b:v", br, "-maxrate", br, "-bufsize", br]
        args += ["-g", str(gop), "-keyint_min", str(gop),
                 "-force_key_frames", f"expr:gte(t,n_forced*{self.seg})"]
        if audio_inputs:
            args += ["-c:a", "aac", "-b:a", "160k", "-ar", "48000"]

        args += ["-f", "segment", "-segment_time", str(self.seg), "-segment_wrap", str(self.wrap),
                 "-segment_format", "mpegts", "-reset_timestamps", "1",
                 str(self.buffer_dir / "seg_%04d.ts")]
        return args

    # ---------- управление записью ----------
    def start(self):
        if self.running:
            return
        shutil.rmtree(self.buffer_dir, ignore_errors=True)
        self.buffer_dir.mkdir(parents=True, exist_ok=True)
        self.running = True
        self.paused = False
        self._spawn()
        self._watchdog = threading.Thread(target=self._watch, daemon=True)
        self._watchdog.start()

    def _spawn(self):
        cmd = self._build_cmd()
        logf = open(self.buffer_dir / "ffmpeg.log", "ab")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                     stderr=logf, creationflags=NO_WINDOW)
        logf.close()

    def _watch(self):
        fails = 0
        while self.running:
            time.sleep(1)
            if not self.running or self.paused or self.proc is None:
                continue
            if self.proc.poll() is not None:
                fails += 1
                self.log(f"ffmpeg завершился (код {self.proc.returncode}), перезапуск... "
                         f"см. {self.buffer_dir / 'ffmpeg.log'}")
                time.sleep(min(30, 2 * fails))
                if self.running and not self.paused:
                    self._spawn()
            else:
                fails = 0

    def _kill(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.stdin.write(b"q")
                p.stdin.flush()
                p.wait(timeout=5)
            except Exception:  # noqa: BLE001
                p.kill()

    def stop(self):
        self.running = False
        self._kill()

    def toggle_pause(self):
        if not self.running:
            return
        if self.paused:
            self.paused = False
            self._spawn()
            self.log("Запись возобновлена")
        else:
            self.paused = True
            self._kill()
            self.log("Запись на паузе")
        return self.paused

    # ---------- сохранение повтора ----------
    def save_replay(self):
        if not self.save_lock.acquire(blocking=False):
            return None
        try:
            segs = sorted(self.buffer_dir.glob("seg_*.ts"), key=lambda p: p.stat().st_mtime)
            segs = [s for s in segs if s.stat().st_size > 0]
            if not segs:
                self.log("Буфер пуст — нечего сохранять")
                return None
            need = math.ceil(self.cfg["replay_seconds"] / self.seg) + 1
            chosen = segs[-need:]

            # Снимок сегментов, чтобы кольцевой буфер не перезаписал их во время склейки
            snap = self.buffer_dir / "snapshot"
            shutil.rmtree(snap, ignore_errors=True)
            snap.mkdir()
            files = []
            for i, s in enumerate(chosen):
                dst = snap / f"{i:04d}.ts"
                shutil.copyfile(s, dst)
                files.append(dst)
            lst = snap / "list.txt"
            lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")

            out = self.output_dir / f"Replay_{datetime.now():%Y-%m-%d_%H-%M-%S}.mp4"
            r = run_quiet([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                           "-f", "concat", "-safe", "0", "-i", str(lst),
                           "-map", "0", "-c", "copy", "-bsf:a", "aac_adtstoasc",
                           "-movflags", "+faststart", str(out)], timeout=120)
            shutil.rmtree(snap, ignore_errors=True)
            if r.returncode != 0 or not out.exists():
                self.log("Ошибка сохранения: " + r.stderr.decode(errors="ignore")[-500:])
                return None
            self.log(f"Сохранено: {out}")
            return out
        finally:
            self.save_lock.release()


# ====================== интерфейс (трей + горячие клавиши) ======================
def beep(ok=True):
    if IS_WIN:
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_OK if ok else winsound.MB_ICONHAND)
        except Exception:  # noqa: BLE001
            pass


def make_icon_image(paused=False):
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, 60, 60), fill=(40, 40, 40, 255))
    if paused:
        d.rectangle((20, 18, 28, 46), fill=(200, 200, 200, 255))
        d.rectangle((36, 18, 44, 46), fill=(200, 200, 200, 255))
    else:
        d.ellipse((18, 18, 46, 46), fill=(118, 185, 0, 255))
    return img


def main():
    import keyboard
    import pystray

    cfg = load_config()
    icon = None

    def log(msg):
        print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)

    def notify(msg):
        log(msg)
        if icon is not None:
            try:
                icon.notify(msg, APP_NAME)
            except Exception:  # noqa: BLE001
                pass

    try:
        rec = Recorder(cfg, log)
    except Exception as e:  # noqa: BLE001
        log(f"Ошибка запуска: {e}")
        if IS_WIN:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, str(e), APP_NAME, 0x10)
        sys.exit(1)

    def do_save(*_):
        def worker():
            out = rec.save_replay()
            if cfg["sound"]:
                beep(out is not None)
            if out:
                notify(f"Повтор сохранён: {out.name}")
        threading.Thread(target=worker, daemon=True).start()

    def do_toggle(*_):
        paused = rec.toggle_pause()
        if icon is not None:
            icon.icon = make_icon_image(bool(paused))
            icon.title = f"{APP_NAME} — {'пауза' if paused else 'запись'}"

    def open_folder(*_):
        if IS_WIN:
            os.startfile(rec.output_dir)  # noqa: S606

    def quit_app(*_):
        keyboard.unhook_all()
        rec.stop()
        icon.stop()

    rec.start()
    keyboard.add_hotkey(cfg["hotkey"], do_save, suppress=False)
    if cfg.get("toggle_hotkey"):
        keyboard.add_hotkey(cfg["toggle_hotkey"], do_toggle, suppress=False)

    menu = pystray.Menu(
        pystray.MenuItem(f"Сохранить повтор ({cfg['hotkey']})", do_save, default=True),
        pystray.MenuItem("Пауза / продолжить", do_toggle),
        pystray.MenuItem("Открыть папку с повторами", open_folder),
        pystray.MenuItem("Выход", quit_app),
    )
    icon = pystray.Icon(APP_NAME, make_icon_image(), f"{APP_NAME} — запись", menu)
    log(f"Готово. Нажмите {cfg['hotkey']} чтобы сохранить последние {cfg['replay_seconds']} c.")
    icon.run()


if __name__ == "__main__":
    main()
