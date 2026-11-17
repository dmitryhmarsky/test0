"""Захват системного звука через WASAPI loopback (не нужен «Стерео микшер»).

PCM пишется в stdin ffmpeg. Запись привязана к часам: если звук не играет
(WASAPI в это время не отдаёт данные), подставляется тишина, поэтому звук
не уезжает относительно видео.
"""
import threading
import time

try:
    import pyaudiowpatch as pyaudio
except Exception:  # noqa: BLE001
    pyaudio = None

DEFAULT_LABEL = "Устройство вывода по умолчанию"


def available():
    return pyaudio is not None


def list_output_devices():
    """Имена устройств вывода, звук которых можно записать."""
    if not pyaudio:
        return []
    p = pyaudio.PyAudio()
    try:
        names = []
        for d in p.get_loopback_device_info_generator():
            name = d["name"].replace(" [Loopback]", "")
            if name not in names:
                names.append(name)
        return names
    except Exception:  # noqa: BLE001
        return []
    finally:
        p.terminate()


class LoopbackSource:
    LATENCY = 0.15   # с, запас буфера против рывков
    MAX_BUF = 0.5    # с, больше — отбрасываем (дрейф часов)

    def __init__(self, device_name=""):
        if not pyaudio:
            raise RuntimeError("Не установлен pyaudiowpatch — системный звук недоступен")
        self.pa = pyaudio.PyAudio()
        dev = None
        if device_name:
            for d in self.pa.get_loopback_device_info_generator():
                if d["name"].replace(" [Loopback]", "") == device_name:
                    dev = d
                    break
        if dev is None:
            dev = self.pa.get_default_wasapi_loopback()
        self.dev = dev
        self.rate = int(dev["defaultSampleRate"])
        self.channels = max(1, min(2, int(dev["maxInputChannels"])))
        self.frame = 2 * self.channels  # s16le
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._stream = None
        self._thread = None
        self._running = False

    def ffmpeg_input_args(self):
        return ["-f", "s16le", "-ar", str(self.rate), "-ac", str(self.channels), "-i", "pipe:0"]

    def _callback(self, data, frames, _time, _status):
        with self._lock:
            self._buf += data
        return (None, pyaudio.paContinue)

    def start(self, sink):
        """sink — поток (stdin ffmpeg), куда писать PCM."""
        self._running = True
        self._stream = self.pa.open(format=pyaudio.paInt16, channels=self.channels, rate=self.rate,
                                    input=True, input_device_index=self.dev["index"],
                                    frames_per_buffer=self.rate // 100,
                                    stream_callback=self._callback)
        self._thread = threading.Thread(target=self._pump, args=(sink,), daemon=True)
        self._thread.start()

    def _pump(self, sink):
        t0 = time.perf_counter()
        written = 0
        max_bytes = int(self.MAX_BUF * self.rate) * self.frame
        while self._running:
            time.sleep(0.01)
            target = int((time.perf_counter() - t0 - self.LATENCY) * self.rate)
            need = target - written
            if need <= 0:
                continue
            nb = need * self.frame
            with self._lock:
                if len(self._buf) > max_bytes:  # устройство спешит — выкидываем лишнее
                    del self._buf[:len(self._buf) - max_bytes // 2]
                chunk = bytes(self._buf[:nb])
                del self._buf[:nb]
            if len(chunk) < nb:  # звук не играет — дополняем тишиной
                chunk += b"\x00" * (nb - len(chunk))
            try:
                sink.write(chunk)
                sink.flush()
            except (OSError, ValueError):
                break
            written = target

    def stop(self):
        self._running = False
        try:
            if self._stream:
                self._stream.stop_stream()
                self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self.pa.terminate()
