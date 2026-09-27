"""Decides *when* a full screen read is worth doing.

A small state machine sitting between cheap sampling and expensive perception.
It exists so the companion can watch the screen continuously without paying for
it, and so it doesn't read a page you're still scrolling through.

    IDLE ──screen changed──> SETTLING ──still for stable_delay──> refresh ──> IDLE
                                  ^                   |
                                  └──changed again────┘

The settling delay is the important part. Without it, dragging a window or
scrolling an article would fire a full read on every frame, each one capturing a
half-scrolled screen. Waiting for the screen to hold still means the companion
reads pages, not motion blur.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum

from core.logging import get_logger
from modules.capture.screen import ScreenSource
from modules.perception.differ import FrameDiffer

log = get_logger(__name__)


class Verdict(str, Enum):
    """What the caller should do with this tick."""

    IDLE = "idle"  # nothing has changed; go back to sleep
    SETTLING = "settling"  # screen is in motion; wait for it to stop
    DUE = "due"  # screen has settled and is worth reading properly
    WAITING = "waiting"  # settled, but the last full read was too recent


@dataclass
class AmbientStats:
    samples: int = 0
    changes: int = 0
    refreshes: int = 0
    sample_ms_total: float = 0.0

    @property
    def mean_sample_ms(self) -> float:
        return self.sample_ms_total / self.samples if self.samples else 0.0


class AmbientObserver:
    """Samples cheaply and reports when a full read is warranted."""

    def __init__(
        self,
        screen: ScreenSource,
        differ: FrameDiffer,
        stable_delay_s: float = 0.8,
        min_refresh_interval_s: float = 3.0,
    ) -> None:
        self.screen = screen
        self.differ = differ
        self.stable_delay_s = stable_delay_s
        self.min_refresh_interval_s = min_refresh_interval_s
        self.stats = AmbientStats()

        self._settling_since: float | None = None
        self._last_refresh: float = 0.0

    def tick(self) -> Verdict:
        """Take one cheap sample and decide what it means."""
        started = time.perf_counter()
        thumbnail = self.screen.grab_thumbnail(self.differ.sample_long_edge)
        changed, difference = self.differ.compare(thumbnail)
        elapsed_ms = (time.perf_counter() - started) * 1000

        self.stats.samples += 1
        self.stats.sample_ms_total += elapsed_ms

        now = time.time()
        if changed:
            self.stats.changes += 1
            self._settling_since = now
            log.debug("ambient: change %.4f (sample %.1f ms)", difference, elapsed_ms)
            return Verdict.SETTLING

        if self._settling_since is None:
            return Verdict.IDLE

        if now - self._settling_since < self.stable_delay_s:
            return Verdict.SETTLING

        # Screen has been still long enough. Rate-limit anyway, so a page that
        # updates every second (a video, a live dashboard) can't pin the CPU.
        if now - self._last_refresh < self.min_refresh_interval_s:
            return Verdict.WAITING

        self._settling_since = None
        self._last_refresh = now
        self.stats.refreshes += 1
        return Verdict.DUE

    def invalidate(self) -> None:
        """Force the next settled screen to be treated as new."""
        self.differ.reset()
        self._settling_since = None
