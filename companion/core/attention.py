"""Deciding whether this is a moment the companion may speak unprompted.

The hardest part of proactive commentary is not noticing things worth saying --
it is not saying them. A companion that comments on everything is unusable
within ten minutes, and the failure is asymmetric: one remark too many irritates
far more than one missed remark costs.

So the model is never asked "should you say something?" -- that question invites
a yes. This policy answers a narrower, deterministic question: is there a reason
to WAIT right now? Muted, busy, the user just spoke, too soon after the last
remark, the hourly budget spent, or too little on screen to have an opinion
about. All of it is free; no model is involved.

Waiting is not refusing. The orchestrator keeps the page as a standing candidate
and asks again on the next tick, so a remark held up by the cooldown still
happens once the cooldown ends. Before, a blocked moment was thrown away for
good.
"""

from __future__ import annotations

import time
from typing import Callable

from core.logging import get_logger

log = get_logger(__name__)


class AttentionPolicy:
    """Manners and rate limits for unprompted remarks."""

    def __init__(
        self,
        cooldown_s: float = 75.0,
        max_per_hour: int = 25,
        min_chars: int = 400,
        quiet_after_user_s: float = 45.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.cooldown_s = cooldown_s
        self.max_per_hour = max_per_hour
        self.min_chars = min_chars
        self.quiet_after_user_s = quiet_after_user_s
        self.clock = clock
        self.muted = False
        self._spoken_at: list[float] = []
        # -inf rather than 0: with a test clock starting at zero, 0 would read
        # as "the user spoke at the very start".
        self._last_user_at = float("-inf")

    def note_user_activity(self) -> None:
        """They asked, spoke, or stopped the companion. Buys a quiet period.

        Interrupting someone who just spoke to you is the rudest version of this
        feature, and the easiest to avoid.
        """
        self._last_user_at = self.clock()

    def note_spoke(self) -> None:
        now = self.clock()
        self._spoken_at = [t for t in self._spoken_at if t >= now - 3600]
        self._spoken_at.append(now)

    @property
    def spoken_last_hour(self) -> int:
        cutoff = self.clock() - 3600
        return sum(1 for t in self._spoken_at if t >= cutoff)

    def reason_to_wait(
        self, busy: bool = False, text_chars: int | None = None
    ) -> str | None:
        """Why this isn't a moment to speak, or None if it is.

        Pure: no counters, no logging. It is asked every tick while a candidate
        waits, so anything with side effects here would run once a second.
        """
        now = self.clock()
        if self.muted:
            return "muted"
        if busy:
            return "busy"
        if now - self._last_user_at < self.quiet_after_user_s:
            return "user was just talking"
        if self._spoken_at and now - self._spoken_at[-1] < self.cooldown_s:
            return "cooldown"
        if self.spoken_last_hour >= self.max_per_hour:
            return "hourly budget spent"
        if text_chars is not None and text_chars < self.min_chars:
            # Menus, empty pages, a desktop: remarking on these is what makes it
            # feel stupid. The model's NOTHING escape is not reliable enough to
            # leave this to it.
            return "not enough on screen"
        return None
