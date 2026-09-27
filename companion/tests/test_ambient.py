"""Ambient watching tests, driven by synthetic frames and a fake clock so the
debounce behaviour is checked exactly rather than by sleeping."""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PIL import Image, ImageDraw

import core.ambient as ambient_mod
from core.ambient import AmbientObserver, Verdict
from core.logging import setup_logging
from modules.perception.differ import FrameDiffer

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def frame(text="", shade=255):
    img = Image.new("RGB", (1280, 720), (shade, shade, shade))
    if text:
        ImageDraw.Draw(img).rectangle([100, 100, 1100, 600], fill=(0, 0, 0))
    return img


import numpy as np


def thumb(image, long_edge=160):
    factor = max(1, round(max(image.width, image.height) / long_edge))
    return np.asarray(image.reduce(factor).convert("L"), dtype=np.int16)


class FakeScreen:
    is_live = True

    def __init__(self):
        self.next = frame()
        self.grabs = 0

    def grab(self):
        self.grabs += 1
        return self.next

    def grab_thumbnail(self, long_edge=160):
        self.grabs += 1
        return thumb(self.next, long_edge)

    def bounds(self):
        return (0, 0, 1280, 720)


# Fake clock so debounce timing is exact and the test runs instantly.
clock = {"t": 1000.0}
ambient_mod.time.time = lambda: clock["t"]


def build(stable=0.8, min_interval=0.0):
    screen = FakeScreen()
    obs = AmbientObserver(screen, FrameDiffer(threshold=0.012),
                          stable_delay_s=stable, min_refresh_interval_s=min_interval)
    return screen, obs


print("change detection:")

differ = FrameDiffer(threshold=0.012)
changed, _ = differ.compare(thumb(frame()))
check("first frame always counts as a change", changed)
changed, diff = differ.compare(thumb(frame()))
check("identical frame is not a change", not changed, f"diff={diff:.5f}")
changed, diff = differ.compare(thumb(frame(text="big")))
check("large visual change is detected", changed, f"diff={diff:.5f}")

differ2 = FrameDiffer(threshold=0.012)
differ2.compare(thumb(frame(shade=255)))
changed, diff = differ2.compare(thumb(frame(shade=253)))
check("tiny shift stays below threshold", not changed, f"diff={diff:.5f}")

print("\ndebounce state machine:")

screen, obs = build()
check("first tick settles (nothing seen before)", obs.tick() is Verdict.SETTLING)

clock["t"] += 1.0
check("still screen after settle delay is DUE", obs.tick() is Verdict.DUE)

clock["t"] += 1.0
check("no repeat read while nothing changes", obs.tick() is Verdict.IDLE)

# Screen changes, then keeps changing: must not fire mid-motion.
screen.next = frame(text="a")
check("change starts settling", obs.tick() is Verdict.SETTLING)
clock["t"] += 0.3
screen.next = frame(text="a", shade=200)
check("still changing -> still settling", obs.tick() is Verdict.SETTLING)
clock["t"] += 0.3
check("not yet settled long enough", obs.tick() is Verdict.SETTLING)
clock["t"] += 0.9
check("settled long enough -> DUE", obs.tick() is Verdict.DUE)

print("\nrate limiting:")

screen, obs = build(stable=0.1, min_interval=5.0)
obs.tick()
clock["t"] += 1.0
check("first read allowed", obs.tick() is Verdict.DUE)
screen.next = frame(text="x")
obs.tick()
clock["t"] += 1.0
check("second read blocked by min interval", obs.tick() is Verdict.WAITING)
clock["t"] += 10.0
check("allowed again once the interval passes", obs.tick() is Verdict.DUE)

print("\nstats and invalidation:")

screen, obs = build()
for _ in range(5):
    obs.tick()
check("every tick is counted", obs.stats.samples == 5, f"{obs.stats.samples}")
check("sample cost is recorded", obs.stats.mean_sample_ms > 0,
      f"{obs.stats.mean_sample_ms:.2f} ms")

obs.invalidate()
check("invalidate makes the next frame count as new",
      obs.tick() is Verdict.SETTLING)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
