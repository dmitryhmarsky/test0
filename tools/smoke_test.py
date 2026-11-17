"""Проверка на настоящей Windows: запись экрана 10 с и сохранение повтора."""
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from replay import config  # noqa: E402
from replay.recorder import Recorder  # noqa: E402

out = Path(tempfile.mkdtemp())
ok = True
for capture in ("ddagrab", "gdigrab"):
    cfg = dict(config.DEFAULTS, capture=capture, replay_seconds=8, height=1080, fps=30,
               output_dir=str(out / capture), system_audio=True)
    r = Recorder(cfg)
    print(f"== {capture}: encoder={r.encoder}")
    r.start()
    time.sleep(12)
    print("status:", r.status())
    f = r.save_replay()
    r.stop()
    log = r.buffer_dir / "ffmpeg.log"
    if log.exists():
        print(log.read_text(errors="ignore")[-1500:])
    if f:
        info = subprocess.run([r.ffmpeg, "-hide_banner", "-i", str(f)], capture_output=True, text=True).stderr
        print("\n".join(l for l in info.splitlines() if "Duration" in l or "Stream" in l))
    else:
        print(f"!! {capture}: повтор не сохранён")
        ok = False if capture == "gdigrab" else ok
sys.exit(0 if ok else 1)
