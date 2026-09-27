"""Privacy guard -- refuse to capture when a sensitive window is on screen.

The app captures a whole monitor, so checking only the foreground window would
be inadequate: anything else visible on that monitor lands in the screenshot
too. The guard therefore inspects every visible window overlapping the target
monitor and blocks the capture outright if any of them matches the ignore-list.

Blocking is deliberately loud. A silent redaction would leave you unsure whether
the companion saw something or not, and the whole point is to be sure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Sequence

from core.logging import get_logger
from core.types import WindowInfo

log = get_logger(__name__)


@dataclass(frozen=True)
class PrivacyBlock:
    """Why a capture was refused."""

    reason: str
    window: WindowInfo


class PrivacyGuard:
    """Matches visible windows against process and title ignore-lists."""

    def __init__(
        self,
        enabled: bool = True,
        blocked_processes: Sequence[str] = (),
        blocked_title_patterns: Sequence[str] = (),
    ) -> None:
        self.enabled = enabled
        self._processes = {p.strip().lower() for p in blocked_processes if p.strip()}
        self._patterns: list[re.Pattern[str]] = []
        for pattern in blocked_title_patterns:
            if not pattern.strip():
                continue
            try:
                self._patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as exc:
                # A bad regex must not take the app down, but it must be noticed:
                # a silently dropped rule is a privacy hole.
                log.error("ignoring invalid privacy pattern %r: %s", pattern, exc)

    def check(self, windows: Iterable[WindowInfo]) -> PrivacyBlock | None:
        """Return the first matching window, or None if the capture may proceed."""
        if not self.enabled:
            return None
        for window in windows:
            if window.process and window.process in self._processes:
                return PrivacyBlock(
                    reason=f"{window.process} is on the blocked-process list",
                    window=window,
                )
            for pattern in self._patterns:
                if pattern.search(window.title):
                    return PrivacyBlock(
                        reason=f"window title matches /{pattern.pattern}/",
                        window=window,
                    )
        return None

    def describe(self) -> str:
        if not self.enabled:
            return "privacy guard DISABLED"
        return (
            f"privacy guard on: {len(self._processes)} processes, "
            f"{len(self._patterns)} title patterns"
        )
