"""Countdown timers.

Timers are polled rather than run on threading.Timer callbacks. Polling is
checked from the worker's existing idle loop, which means expiry is noticed on a
thread that is already allowed to speak and update the window -- no callbacks
arriving on a timer thread that then has to marshal across to the UI.

They are also persisted, so a timer survives restarting the companion. A
twenty-minute timer that silently dies because you quit the app is worse than
no timer at all: you stop watching the clock precisely because you set one.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from core.logging import get_logger
from modules.tools.base import Tool

log = get_logger(__name__)


@dataclass
class Timer:
    label: str
    due_at: float  # unix time
    created_at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @property
    def remaining(self) -> float:
        return max(0.0, self.due_at - time.time())

    @property
    def expired(self) -> bool:
        return time.time() >= self.due_at


class TimerStore:
    """Holds active timers and persists them across restarts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path) if path else None
        self.timers: list[Timer] = []
        self._load()

    def add(self, label: str, seconds: float) -> Timer:
        timer = Timer(label=label or "timer", due_at=time.time() + seconds)
        self.timers.append(timer)
        self._save()
        return timer

    def cancel(self, needle: str) -> Timer | None:
        needle = (needle or "").lower().strip()
        for timer in self.timers:
            if needle in (timer.label.lower(), timer.id):
                self.timers.remove(timer)
                self._save()
                return timer
        return None

    def due(self) -> list[Timer]:
        """Return expired timers and remove them. Safe to call frequently."""
        expired = [t for t in self.timers if t.expired]
        if expired:
            self.timers = [t for t in self.timers if not t.expired]
            self._save()
        return expired

    def active(self) -> list[Timer]:
        return sorted(self.timers, key=lambda t: t.due_at)

    # -- persistence ----------------------------------------------------------

    def _load(self) -> None:
        if not self.path or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.timers = [Timer(**item) for item in raw]
        except Exception:
            # A corrupt timer file must not stop the app starting.
            log.warning("could not read timers from %s", self.path, exc_info=True)
            self.timers = []
        else:
            # Anything that expired while the app was closed has already
            # missed its moment; report it rather than firing it hours late.
            stale = [t for t in self.timers if t.expired]
            if stale:
                log.info("discarding %d timer(s) that expired while closed", len(stale))
                self.timers = [t for t in self.timers if not t.expired]

    def _save(self) -> None:
        if not self.path:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps([asdict(t) for t in self.timers], indent=2),
                encoding="utf-8",
            )
        except OSError:
            log.warning("could not save timers to %s", self.path, exc_info=True)


class SetTimer(Tool):
    name = "set_timer"
    description = (
        "Start a countdown timer that alerts the user when it finishes. Use for "
        "'remind me in ten minutes', 'set a timer for the pasta', and similar."
    )
    parameters = {
        "type": "object",
        "properties": {
            "minutes": {
                "type": "number",
                "description": "How long, in minutes. Use fractions for seconds.",
            },
            "label": {
                "type": "string",
                "description": "Short name for what the timer is for.",
            },
        },
        "required": ["minutes"],
    }
    writes = True

    def __init__(self, store: TimerStore) -> None:
        self.store = store

    def run(self, minutes: float, label: str = "timer") -> str:
        try:
            minutes = float(minutes)
        except (TypeError, ValueError):
            return f"'{minutes}' is not a number of minutes."
        if minutes <= 0:
            return "A timer needs a positive length."
        if minutes > 24 * 60:
            return "That is longer than a day; set something shorter."

        timer = self.store.add(label, minutes * 60)
        return f"Timer '{timer.label}' set for {_describe(minutes * 60)}."


class ListTimers(Tool):
    name = "list_timers"
    description = "List timers that are currently running and how long is left."
    parameters = {"type": "object", "properties": {}}
    writes = False

    def __init__(self, store: TimerStore) -> None:
        self.store = store

    def run(self) -> str:
        active = self.store.active()
        if not active:
            return "No timers are running."
        return "; ".join(
            f"'{t.label}' with {_describe(t.remaining)} left" for t in active
        )


class CancelTimer(Tool):
    name = "cancel_timer"
    description = "Cancel a running timer by its label."
    parameters = {
        "type": "object",
        "properties": {
            "label": {"type": "string", "description": "Label of the timer to cancel"}
        },
        "required": ["label"],
    }
    writes = True

    def __init__(self, store: TimerStore) -> None:
        self.store = store

    def run(self, label: str) -> str:
        cancelled = self.store.cancel(label)
        if cancelled is None:
            active = self.store.active()
            if not active:
                return "There are no timers running."
            names = ", ".join(f"'{t.label}'" for t in active)
            return f"No timer called '{label}'. Running: {names}."
        return f"Cancelled the '{cancelled.label}' timer."


def _describe(seconds: float) -> str:
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    parts = []
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if minutes:
        parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
    if secs and not hours:
        parts.append(f"{secs} second{'s' if secs != 1 else ''}")
    return " ".join(parts) or "0 seconds"
