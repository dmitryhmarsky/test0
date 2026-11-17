"""Запись экрана в кольцевой буфер сегментов и сохранение повторов."""
import math
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from . import audio
from .config import IS_WIN, TEMP_DIR, bitrate_mbps
from .system import NO_WINDOW, find_ffmpeg, run_quiet

SEGMENT = 2  # секунд в одном сегменте
_encoder_cache = {}


def encoder_works(ffmpeg, enc):
    pix = "yuv420p" if enc == "libx264" else "nv12"
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-loglevel", "error",
                       "-f", "lavfi", "-i", "color=c=black:s=1280x720:r=30:d=0.5",
                       "-pix_fmt", pix, "-c:v", enc, "-f", "null", "-"])
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def detect_encoder(ffmpeg):
    if ffmpeg not in _encoder_cache:
        for enc in ("h264_nvenc", "h264_amf", "h264_qsv", "libx264"):
            if encoder_works(ffmpeg, enc):
                _encoder_cache[ffmpeg] = enc
                break
        else:
            raise RuntimeError("В ffmpeg нет ни одного рабочего H.264-энкодера")
    return _encoder_cache[ffmpeg]


def has_ddagrab(ffmpeg):
    try:
        r = run_quiet([ffmpeg, "-hide_banner", "-filters"])
        return re.search(rb"\sddagrab\s", r.stdout) is not None
    except Exception:  # noqa: BLE001
        return False


ENCODER_NAMES = {"h264_nvenc": "NVIDIA NVENC", "h264_amf": "AMD AMF",
                 "h264_qsv": "Intel QuickSync", "libx264": "CPU (x264)"}


class Recorder:
    def __init__(self, cfg, log=print):
        self.cfg = dict(cfg)
        self.log = log
        self.ffmpeg = find_ffmpeg(cfg.get("ffmpeg_path", ""))
        self.buffer_dir = TEMP_DIR / "buffer"
        self.proc = None
        self.loopback = None
        self.running = False
        self.paused = False
        self.error = ""
        self.started_at = 0.0
        self._lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._gen = 0
        self._run_id = 0
        self.encoder = detect_encoder(self.ffmpeg) if cfg["encoder"] == "auto" else cfg["encoder"]
        cap = cfg["capture"]
        if cap == "auto":
            cap = "ddagrab" if IS_WIN and has_ddagrab(self.ffmpeg) else "gdigrab"
        self.capture = cap

    @property
    def output_dir(self):
        p = Path(self.cfg["output_dir"])
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def wrap(self):
        return math.ceil(int(self.cfg["replay_seconds"]) / SEGMENT) + 4

    def status(self):
        if self.error:
            return "error", self.error
        if self.paused:
            return "paused", "Пауза"
        if self.running:
            return "recording", "Идёт запись"
        return "stopped", "Остановлено"

    def buffered_seconds(self):
        if not self.running or self.paused:
            return 0
        return min(int(self.cfg["replay_seconds"]), int(time.time() - self.started_at))

    # ---------- команда ffmpeg ----------
    def _build_cmd(self, loopback):
        c = self.cfg
        fps, h = int(c["fps"]), int(c["height"])
        mouse = "1" if c["draw_mouse"] else "0"
        a = [self.ffmpeg, "-hide_banner", "-loglevel", "warning", "-y"]

        if self.capture == "ddagrab":
            a += ["-f", "lavfi", "-i",
                  f"ddagrab=output_idx={int(c['monitor'])}:framerate={fps}:draw_mouse={mouse}"]
            vpre = "hwdownload,format=bgra,"
        elif self.capture == "gdigrab":
            a += ["-f", "gdigrab", "-framerate", str(fps), "-draw_mouse", mouse,
                  "-thread_queue_size", "512", "-i", "desktop"]
            vpre = ""
        else:  # test — генератор картинки и тона, для отладки
            a += ["-re", "-f", "lavfi", "-i", f"testsrc2=s={h * 16 // 9}x{h}:r={fps}"]
            vpre = ""

        pix = "yuv420p" if self.encoder == "libx264" else "nv12"
        filters = [f"[0:v]{vpre}scale=-2:{h}:flags=bicubic,format={pix}[v]"]
        idx, labels = 1, []
        if loopback:
            a += loopback.ffmpeg_input_args()
            vol = int(c["system_volume"]) / 100
            filters.append(f"[{idx}:a]aresample=48000,aformat=channel_layouts=stereo,volume={vol:.2f}[sys]")
            labels.append("[sys]")
            idx += 1
        elif c["system_audio"] and self.capture == "test":
            a += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
            filters.append(f"[{idx}:a]aformat=channel_layouts=stereo,volume={int(c['system_volume']) / 100:.2f}[sys]")
            labels.append("[sys]")
            idx += 1
        if c["mic_enabled"] and c["mic_device"]:
            a += ["-f", "dshow", "-audio_buffer_size", "50", "-thread_queue_size", "512",
                  "-i", f"audio={c['mic_device']}"]
            mv = int(c["mic_volume"]) / 100 * 10 ** (float(c["mic_gain_db"]) / 20)
            filters.append(f"[{idx}:a]aresample=48000:async=1,aformat=channel_layouts=stereo,"
                           f"volume={mv:.3f}[mic]")
            labels.append("[mic]")
            idx += 1
        if len(labels) == 2:
            filters.append("[sys][mic]amix=inputs=2:duration=longest:normalize=0,alimiter=limit=0.97[a]")
        elif len(labels) == 1:
            filters.append(f"{labels[0]}alimiter=limit=0.97[a]")

        a += ["-filter_complex", ";".join(filters), "-map", "[v]"]
        if labels:
            a += ["-map", "[a]"]
        a += ["-r", str(fps)]

        br = f"{bitrate_mbps(c)}M"
        enc = self.encoder
        if enc == "h264_nvenc":
            a += ["-c:v", enc, "-preset", "p4", "-tune", "ll", "-rc", "vbr", "-bf", "0"]
        elif enc == "h264_amf":
            a += ["-c:v", enc, "-usage", "lowlatency", "-quality", "speed", "-rc", "vbr_peak"]
        elif enc == "h264_qsv":
            a += ["-c:v", enc, "-preset", "veryfast"]
        else:
            a += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency"]
        gop = fps * SEGMENT
        a += ["-b:v", br, "-maxrate", br, "-bufsize", br, "-g", str(gop), "-keyint_min", str(gop),
              "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT})"]
        if labels:
            a += ["-c:a", "aac", "-b:a", "192k"]
        a += ["-f", "segment", "-segment_time", str(SEGMENT), "-segment_wrap", str(self.wrap),
              "-segment_format", "mpegts", "-reset_timestamps", "1",
              str(self.buffer_dir / f"seg_{self._gen}_%04d.ts")]
        return a

    # ---------- управление ----------
    def start(self, clear=True):
        with self._lock:
            if self.running:
                return
            if clear:
                shutil.rmtree(self.buffer_dir, ignore_errors=True)
            self.buffer_dir.mkdir(parents=True, exist_ok=True)
            self.running, self.paused, self.error = True, False, ""
            self.started_at = time.time()
            self._run_id += 1
            self._spawn()
        threading.Thread(target=self._watch, args=(self._run_id,), daemon=True).start()

    def _spawn(self):
        self._gen += 1
        # удаляем сегменты прошлых запусков, которые уже не попадут в повтор
        limit = time.time() - int(self.cfg["replay_seconds"]) - 30
        for f in self.buffer_dir.glob("seg_*.ts"):
            try:
                if f.stat().st_mtime < limit:
                    f.unlink()
            except OSError:
                pass
        loopback = None
        if self.cfg["system_audio"] and self.capture != "test":
            try:
                loopback = audio.LoopbackSource(self.cfg["system_device"])
            except Exception as e:  # noqa: BLE001
                self.log(f"Системный звук недоступен: {e}")
        cmd = self._build_cmd(loopback)
        with open(self.buffer_dir / "ffmpeg.log", "ab") as logf:
            logf.write(("\n=== " + datetime.now().isoformat() + " " + " ".join(cmd) + "\n").encode())
            logf.flush()
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                         stderr=logf, creationflags=NO_WINDOW)
        self.loopback = loopback
        if loopback:
            try:
                loopback.start(self.proc.stdin)
            except Exception as e:  # noqa: BLE001
                self.log(f"Ошибка захвата системного звука: {e}")

    def _alive(self, run_id):
        return self.running and run_id == self._run_id

    def _watch(self, run_id):
        fails = 0
        while self._alive(run_id):
            time.sleep(1)
            with self._lock:
                if not self._alive(run_id) or self.paused or self.proc is None:
                    continue
                code = self.proc.poll()
                if code is None:
                    if time.time() - self.started_at > 5:
                        fails, self.error = 0, ""
                    continue
                fails += 1
                self.error = f"ffmpeg остановился (код {code}), перезапуск…"
                self.log(self.error + f" Лог: {self.buffer_dir / 'ffmpeg.log'}")
                self._kill()
            time.sleep(min(30, 2 * fails))
            with self._lock:
                if self._alive(run_id) and not self.paused and self.proc is None:
                    self._spawn()

    def _kill(self):
        p, self.proc = self.proc, None
        lb, self.loopback = self.loopback, None
        if lb:
            lb.stop()
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                p.kill()

    def stop(self):
        with self._lock:
            self.running = False
            self._kill()

    def set_paused(self, paused):
        with self._lock:
            if not self.running or paused == self.paused:
                return
            self.paused = paused
            if paused:
                self._kill()
            else:
                self.started_at = time.time()
                self._spawn()

    def restart(self, cfg, clear):
        self.stop()
        self.cfg = dict(cfg)
        if cfg["encoder"] != "auto":
            self.encoder = cfg["encoder"]
        self.start(clear=clear)

    # ---------- сохранение ----------
    def save_replay(self):
        """Склеивает последние replay_seconds в MP4. Возвращает путь или None."""
        if not self._save_lock.acquire(blocking=False):
            return None
        snap = self.buffer_dir / "snapshot"
        try:
            segs = [s for s in self.buffer_dir.glob("seg_*.ts") if s.stat().st_size > 0]
            segs.sort(key=lambda p: p.stat().st_mtime)
            if not segs:
                self.log("Буфер пуст — нечего сохранять")
                return None
            # mtime сегмента = время его окончания; берём всё, что закончилось
            # позже «сейчас минус длительность повтора»
            cutoff = time.time() - int(self.cfg["replay_seconds"])
            chosen = [s for s in segs if s.stat().st_mtime >= cutoff] or segs[-1:]
            shutil.rmtree(snap, ignore_errors=True)
            snap.mkdir()
            files = []
            for i, s in enumerate(chosen):
                dst = snap / f"{i:04d}.ts"
                with open(s, "rb") as src:
                    data = src.read()
                # последний сегмент ещё пишется — отрезаем неполный TS-пакет (188 байт)
                dst.write_bytes(data[:len(data) // 188 * 188])
                files.append(dst)
            lst = snap / "list.txt"
            lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
            out = self.output_dir / f"Replay_{datetime.now():%Y-%m-%d_%H-%M-%S}.mp4"
            r = run_quiet([self.ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                           "-f", "concat", "-safe", "0", "-i", str(lst),
                           "-map", "0", "-c", "copy", "-movflags", "+faststart", str(out)],
                          timeout=180)
            if r.returncode != 0 or not out.exists():
                self.log("Ошибка сохранения: " + r.stderr.decode(errors="ignore")[-400:])
                return None
            self.log(f"Сохранено: {out}")
            return out
        except Exception as e:  # noqa: BLE001
            self.log(f"Ошибка сохранения: {e}")
            return None
        finally:
            shutil.rmtree(snap, ignore_errors=True)
            self._save_lock.release()


# ---------- информация о готовых роликах (для галереи) ----------
_DUR_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+\.?\d*)")


def make_thumbnail(ffmpeg, video, thumb):
    """Создаёт превью и возвращает длительность в секундах."""
    thumb.parent.mkdir(parents=True, exist_ok=True)
    r = run_quiet([ffmpeg, "-hide_banner", "-y", "-ss", "1", "-i", str(video), "-frames:v", "1",
                   "-vf", "scale=320:-2", "-q:v", "4", str(thumb)], timeout=30)
    m = _DUR_RE.search(r.stderr.decode(errors="ignore"))
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else 0.0
