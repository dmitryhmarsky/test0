"""Instant Replay — мгновенный повтор для Windows (аналог NVIDIA ShadowPlay).

Запуск:  python instant_replay.py [--minimized]
"""
import sys

from replay import config
from replay.gui import run

if __name__ == "__main__":
    cfg = config.load()
    run(start_hidden="--minimized" in sys.argv or cfg.get("start_minimized", False))
