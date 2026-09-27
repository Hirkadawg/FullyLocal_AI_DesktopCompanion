"""Screen capture sources.

`MSSCapture` grabs a physical monitor on demand -- nothing runs between
questions, so the app costs nothing while idle. `StaticImageSource` replays a
saved PNG through the identical pipeline, which is what makes prompt and OCR
tuning reproducible.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Sequence

import mss
import numpy as np
from PIL import Image

from core.errors import CaptureError
from core.logging import get_logger
from core.types import WindowCandidate, WindowInfo
from core.winapi import foreground_window, pick_target_window, target_candidates

log = get_logger(__name__)


class ScreenSource(ABC):
    """Anything that can produce an image for perception to read."""

    #: Live sources are subject to the privacy guard; replayed files are not.
    is_live: bool = True

    @abstractmethod
    def grab(self) -> Image.Image:
        """Return the current image in RGB."""

    @abstractmethod
    def bounds(self) -> tuple[int, int, int, int]:
        """The monitor region this source works within.

        Note this is the *monitor*, not necessarily what `grab()` returns --
        `WindowCapture` grabs one window but still reports the whole display,
        because the privacy guard and window selection both need to reason
        about everything on that screen.
        """

    def window_hint(self) -> WindowInfo | None:
        """Which app this image is showing, when that is knowable."""
        return None

    def target_window(self) -> WindowInfo | None:
        """The window the companion should read, per the selection policy.

        Lives on the screen source so that pixel capture and UI Automation
        always agree on which window they are talking about.
        """
        return self.window_hint()

    def grab_thumbnail(self, long_edge: int = 160) -> np.ndarray:
        """A small greyscale array for change detection.

        Separate from `grab()` because the ambient loop runs it every second and
        does not need a full-resolution image -- only enough detail to notice
        the screen changed. Subclasses override this with a faster path.
        """
        image = self.grab()
        factor = max(1, round(max(image.width, image.height) / max(16, long_edge)))
        small = image.reduce(factor) if factor > 1 else image
        return np.asarray(small.convert("L"), dtype=np.int16)


def list_monitors() -> list[dict]:
    """Raw mss monitor table. Index 0 is the union of all monitors."""
    with mss.mss() as sct:
        return [dict(m) for m in sct.monitors]


class MSSCapture(ScreenSource):
    """Captures one physical monitor with mss.

    mss numbers `monitors[0]` as the virtual union of every display, so physical
    monitors start at index 1. That numbering follows Windows' enumeration
    order, which does not necessarily agree with the numbers shown in Display
    Settings -- verify with `--shot` rather than assuming.
    """

    is_live = True

    def __init__(
        self,
        monitor_index: int = 1,
        ignore_processes: Sequence[str] = (),
        min_window_on_monitor: float = 0.5,
        window_match: str = "",
    ) -> None:
        self.monitor_index = monitor_index
        self.ignore_processes = list(ignore_processes)
        self.min_window_on_monitor = min_window_on_monitor
        self.window_match = window_match.strip().lower()
        # mss objects are bound to the thread that creates them; created lazily
        # and reused so repeated captures stay cheap.
        self._sct: mss.base.MSSBase | None = None

    def _session(self) -> "mss.base.MSSBase":
        if self._sct is None:
            self._sct = mss.mss()
        return self._sct

    def _monitor(self) -> dict:
        monitors = self._session().monitors
        if not 0 <= self.monitor_index < len(monitors):
            available = ", ".join(
                f"{i}={m['width']}x{m['height']}" for i, m in enumerate(monitors)
            )
            raise CaptureError(
                f"monitor_index {self.monitor_index} does not exist. "
                f"Available: {available}. Run with --list-monitors."
            )
        return monitors[self.monitor_index]

    def grab(self) -> Image.Image:
        monitor = self._monitor()
        shot = self._session().grab(monitor)
        # mss hands back BGRA; "BGRX" tells Pillow to read it as BGR and skip
        # the alpha byte, which is the documented fast path.
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    def bounds(self) -> tuple[int, int, int, int]:
        m = self._monitor()
        return (m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"])

    def grab_thumbnail(self, long_edge: int = 160) -> np.ndarray:
        """Fast path: stride-sample mss's raw buffer, skipping PIL entirely.

        Converting to a PIL image and box-filtering it costs ~8 ms per sample;
        striding the buffer costs ~0.02 ms for the same 160x90 result. At one
        sample a second that difference is most of the ambient CPU budget.

        Always samples the whole monitor, even in window-capture mode -- the
        point is to notice *any* change on this display, including switching to
        a different window.
        """
        monitor = self._monitor()
        shot = self._session().grab(monitor)
        factor = max(1, round(max(shot.width, shot.height) / max(16, long_edge)))
        raw = np.frombuffer(shot.bgra, dtype=np.uint8).reshape(
            shot.height, shot.width, 4
        )
        # Green alone is a good stand-in for luminance and avoids averaging
        # three channels across millions of pixels.
        return raw[::factor, ::factor, 1].astype(np.int16)

    def window_hint(self) -> WindowInfo | None:
        return self.target_window() or foreground_window()

    def target_window(self) -> WindowInfo | None:
        if self.window_match:
            # Explicit pin, for when the automatic choice keeps guessing wrong.
            # Largest matching window first, same rule as automatic selection.
            matches = [
                c
                for c in self.candidates()
                if c.viable
                and self.window_match in f"{c.window.title} {c.window.process}".lower()
            ]
            if matches:
                return max(matches, key=lambda c: c.monitor_share).window
            log.warning(
                "no window on monitor %s matches %r; falling back to Z-order",
                self.monitor_index,
                self.window_match,
            )
        return pick_target_window(
            self.bounds(),
            ignore_processes=self.ignore_processes,
            min_on_monitor=self.min_window_on_monitor,
        )

    def candidates(self) -> list[WindowCandidate]:
        """Scored windows with rejection reasons, for `--probe`."""
        return target_candidates(
            self.bounds(),
            ignore_processes=self.ignore_processes,
            min_on_monitor=self.min_window_on_monitor,
        )

    def close(self) -> None:
        if self._sct is not None:
            self._sct.close()
            self._sct = None


class WindowCapture(MSSCapture):
    """Captures only the active window, not the whole monitor.

    Less to OCR means a faster and cleaner read: no taskbar, no second browser
    behind the one you're reading, no desktop icons. Falls back to the full
    monitor whenever a sensible window can't be identified.
    """

    def grab(self) -> Image.Image:
        monitor_rect = self.bounds()
        window = self.target_window()
        if window is None:
            log.debug("window capture: no target window, using whole monitor")
            return super().grab()

        rect = _intersect(window.rect, monitor_rect)
        if rect is None or (rect[2] - rect[0]) < 64 or (rect[3] - rect[1]) < 64:
            log.debug("window capture: target too small, using whole monitor")
            return super().grab()

        region = {
            "left": rect[0],
            "top": rect[1],
            "width": rect[2] - rect[0],
            "height": rect[3] - rect[1],
        }
        shot = self._session().grab(region)
        return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")

    # window_hint / target_window are inherited from MSSCapture unchanged.


def _intersect(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> tuple[int, int, int, int] | None:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return None
    return (left, top, right, bottom)


class StaticImageSource(ScreenSource):
    """Replays a saved image, for deterministic testing of the pipeline."""

    is_live = False

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise CaptureError(f"image not found: {self.path}")
        self._image = Image.open(self.path).convert("RGB")

    def grab(self) -> Image.Image:
        return self._image.copy()

    def bounds(self) -> tuple[int, int, int, int]:
        return (0, 0, self._image.width, self._image.height)

    def window_hint(self) -> WindowInfo | None:
        return WindowInfo(
            title=self.path.name, process="replay", rect=self.bounds()
        )
