"""Cheap frame-change detection.

The whole point of ambient awareness is *not* running perception continuously.
OCR costs seconds and UI Automation costs a tree walk; doing either at 1 Hz
would burn a core for nothing, since most seconds the screen hasn't changed.

So the loop compares tiny greyscale thumbnails instead. A 160x90 thumbnail is
~14 kB and diffs in microseconds, which is what makes "always watching"
affordable.

Producing the thumbnail belongs to the screen source (`grab_thumbnail`), which
can stride mss's raw buffer directly; this class only decides what a difference
means.
"""

from __future__ import annotations

import numpy as np

from core.logging import get_logger

log = get_logger(__name__)


class FrameDiffer:
    """Tracks whether the screen has meaningfully changed since the last sample."""

    def __init__(self, threshold: float = 0.012, sample_long_edge: int = 160) -> None:
        self.threshold = threshold
        self.sample_long_edge = max(16, sample_long_edge)
        self._reference: np.ndarray | None = None
        self.last_difference = 0.0

    def compare(self, current: np.ndarray) -> tuple[bool, float]:
        """Return (changed, difference) and adopt this thumbnail as the reference.

        Difference is mean absolute per-pixel change, normalised to 0..1, so the
        threshold means the same thing on any monitor size.
        """
        if self._reference is None or self._reference.shape != current.shape:
            # First frame, or the resolution changed under us.
            self._reference = current
            self.last_difference = 1.0
            return True, 1.0

        difference = float(np.abs(current - self._reference).mean()) / 255.0
        self._reference = current
        self.last_difference = difference
        return difference >= self.threshold, difference

    def reset(self) -> None:
        """Forget the reference, so the next sample counts as a change."""
        self._reference = None
