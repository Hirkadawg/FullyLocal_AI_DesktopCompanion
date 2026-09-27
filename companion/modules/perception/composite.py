"""Fallback chain across perception sources.

Tries each source in order and takes the first that returns enough text. In
practice that means UI Automation answers for browsers, editors and documents,
and OCR picks up everything else -- video, games, remote desktop, canvas apps.

If every source comes up short, the best of them is returned rather than
nothing, so a partial read still reaches the model instead of being discarded.
"""

from __future__ import annotations

import time
from typing import Sequence

from core.logging import get_logger
from core.types import ScreenContext
from modules.perception.base import PerceptionSource

log = get_logger(__name__)


class FallbackPerception(PerceptionSource):
    """Runs sources in priority order until one produces usable text."""

    name = "fallback"

    def __init__(
        self, sources: Sequence[PerceptionSource], min_chars: int = 40
    ) -> None:
        if not sources:
            raise ValueError("FallbackPerception needs at least one source")
        self.sources = list(sources)
        self.min_chars = min_chars

    def read(self) -> ScreenContext:
        started = time.perf_counter()
        attempts: list[str] = []
        best: ScreenContext | None = None

        for source in self.sources:
            try:
                context = source.read()
            except Exception:
                # One broken source must not sink the chain -- that is the whole
                # reason there is a chain.
                log.warning("perception source %r failed", source.name, exc_info=True)
                attempts.append(f"{source.name}:error")
                continue

            attempts.append(f"{source.name}:{len(context.text)}")
            if best is None or len(context.text) > len(best.text):
                best = context

            if len(context.text.strip()) >= self.min_chars:
                context.timings_ms["chain"] = round(
                    (time.perf_counter() - started) * 1000, 1
                )
                log.debug("perception chain %s -> %s", " ".join(attempts), source.name)
                return context

        log.debug("perception chain %s -> none reached %d chars",
                  " ".join(attempts), self.min_chars)
        if best is None:
            best = ScreenContext(source="none")
        best.timings_ms["chain"] = round((time.perf_counter() - started) * 1000, 1)
        return best

    def close(self) -> None:
        for source in self.sources:
            source.close()
