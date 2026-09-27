"""Learning how often to speak -- per site, kind of remark and moment, in code.

A 2026 paper found a small non-LLM model beat LLMs at deciding when
a proactive assistant should speak, and far faster; it matches this project's
rule that timing is decided in code. So this is counting, not a model:

- **positive**: they replied to a remark, rated it up, or asked for one;
- **negative**: they stopped it (Esc), switched Quiet on right after it, or rated
  it down;
- **ignored**: nothing within the outcome window. Logged and kept, but it counts
  for nothing -- research saw about 35% ignored, and silence isn't a verdict.

The counts become an allowance per site, (positive + 2) / (negative + 2),
bounded: one negative trims it to 0.67, never to nothing; it can't fall below
`min_allowance` or rise above `max_allowance`. Quiet stays the real off switch.
A kind of remark or a moment is dropped only once it is clearly disliked: at
least three negatives and an allowance under 0.5.

Kept in a small JSON file on this machine; the settings page's "Your data" tab
resets it (core/reset.py).
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from core.logging import get_logger

log = get_logger(__name__)

POSITIVE, NEGATIVE, IGNORED = "positive", "negative", "ignored"
KINDS = ("sites", "moves", "moments")


class Learning:
    def __init__(self, path: Path | str | None = None, min_allowance: float = 0.25,
                 max_allowance: float = 1.5) -> None:
        self.path = Path(path) if path else None
        self.min_allowance = min_allowance
        self.max_allowance = max_allowance
        self._lock = threading.Lock()
        self.counts: dict[str, dict[str, dict[str, int]]] = {kind: {} for kind in KINDS}
        self._load()

    # -- the numbers -----------------------------------------------------------

    def record(self, site: str, move: str, moment: str, outcome: str) -> None:
        """One remark's outcome, counted against its site, kind and moment."""
        if outcome not in (POSITIVE, NEGATIVE, IGNORED):
            raise ValueError(f"unknown outcome {outcome!r}")
        with self._lock:
            for kind, name in (("sites", site), ("moves", move), ("moments", moment)):
                if name:
                    entry = self.counts[kind].setdefault(name, {POSITIVE: 0, NEGATIVE: 0, IGNORED: 0})
                    entry[outcome] = entry.get(outcome, 0) + 1
            self._save()
        log.info("learned: %s for a %s remark (%s) on %s -- remarks there now x%.2f",
                 outcome, move or "?", moment or "?", site, self.allowance(site))

    def _ratio(self, kind: str, name: str) -> float:
        entry = self.counts.get(kind, {}).get(name) or {}
        return (entry.get(POSITIVE, 0) + 2) / (entry.get(NEGATIVE, 0) + 2)

    def allowance(self, site: str) -> float:
        """How welcome remarks are on this site: 1 is as configured."""
        return min(self.max_allowance, max(self.min_allowance, self._ratio("sites", site)))

    def liked(self, kind: str, name: str) -> bool:
        """False only for a kind of remark or a moment that is clearly unwelcome."""
        entry = self.counts.get(kind, {}).get(name) or {}
        return not (entry.get(NEGATIVE, 0) >= 3 and self._ratio(kind, name) < 0.5)

    def summary(self) -> str:
        changed = [(site, self.allowance(site)) for site in self.counts["sites"]]
        changed = [f"{site} x{a:.2f}" for site, a in sorted(changed, key=lambda x: x[1]) if a != 1.0]
        disliked = [f"{name} ({kind[:-1]})" for kind in ("moves", "moments")
                    for name in self.counts[kind] if not self.liked(kind, name)]
        parts = []
        if changed:
            parts.append("sites: " + ", ".join(changed))
        if disliked:
            parts.append("dropped: " + ", ".join(disliked))
        return "; ".join(parts) or "nothing yet"

    def forget(self, before=None):
        """Start over: every count gone. `before` (moving the file aside) runs
        under the same lock, so an outcome recorded meanwhile can't save the old
        counts back. Returns what `before` returned."""
        with self._lock:
            result = before() if before is not None else None
            self.counts = {kind: {} for kind in KINDS}
        log.info("learning reset: remarks are as configured everywhere again")
        return result

    # -- the file ----------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning("could not read %s; starting learning afresh", self.path)
            return
        for kind in KINDS:
            section = data.get(kind) if isinstance(data, dict) else None
            if not isinstance(section, dict):
                continue
            for name, entry in section.items():
                if isinstance(entry, dict):
                    self.counts[kind][str(name)] = {
                        key: int(entry.get(key, 0)) for key in (POSITIVE, NEGATIVE, IGNORED)
                        if isinstance(entry.get(key, 0), (int, float))
                    }

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(self.path.suffix + ".tmp")
            temporary.write_text(json.dumps(self.counts, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError:
            log.warning("could not save what was learned to %s", self.path, exc_info=True)
