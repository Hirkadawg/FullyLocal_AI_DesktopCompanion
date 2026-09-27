"""The perception sources on the live screen. Counts and timings only -- never
screen text."""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_perception_sources
from core.config import AppConfig
from core.logging import setup_logging
from core.winapi import pick_target_window
from modules.capture.screen import MSSCapture, WindowCapture
from modules.perception.composite import FallbackPerception

setup_logging("WARNING")
cfg = AppConfig.load(CONFIG_PATH)

for mode in ("monitor", "window"):
    print(f"\n=== capture.mode = {mode} ===")
    screen = (
        WindowCapture(monitor_index=cfg.monitor_index)
        if mode == "window"
        else MSSCapture(monitor_index=cfg.monitor_index)
    )
    target = pick_target_window(screen.bounds())
    print(f"  monitor bounds : {screen.bounds()}")
    print(f"  target process : {target.process if target else '(none)'}")
    print(f"  grabbed size   : {screen.grab().size}")

    sources = build_perception_sources(cfg, screen)
    print(f"  chain          : {' -> '.join(s.name for s in sources)}")

    for src in sources:
        t = time.perf_counter()
        ctx = src.read()
        ms = (time.perf_counter() - t) * 1000
        verdict = "OK" if len(ctx.text.strip()) >= cfg.perception.min_chars else "thin"
        print(f"    {src.name:<5} {verdict:<4} {len(ctx.text):>6} chars  {ms:>7.0f} ms")

    chain = FallbackPerception(sources, min_chars=cfg.perception.min_chars)
    t = time.perf_counter()
    ctx = chain.read()
    ms = (time.perf_counter() - t) * 1000
    print(f"    chain won by '{ctx.source}': {len(ctx.text)} chars in {ms:.0f} ms")

    for src in sources:
        src.close()
    screen.close()
