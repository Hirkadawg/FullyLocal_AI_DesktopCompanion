"""Clock tools.

Trivial, and worth having: the model has no idea what time it is, and will
happily invent one if asked. It also can't work out "how long until 3pm"
without knowing now.
"""

from __future__ import annotations

import time
from datetime import datetime

from modules.tools.base import Tool


class GetTime(Tool):
    name = "get_time"
    description = (
        "Get the current local date and time. Use this whenever the user asks "
        "what time or day it is, or when working out how long until something."
    )
    parameters = {"type": "object", "properties": {}}
    writes = False

    def run(self) -> str:
        now = datetime.now()
        return now.strftime("%A %d %B %Y, %H:%M:%S (%Z)").strip()


class Stopwatch(Tool):
    """A running stopwatch, separate from countdown timers."""

    name = "stopwatch"
    description = (
        "Start, check or stop a stopwatch that counts up. Use for measuring how "
        "long something takes. For counting DOWN to an alert, use set_timer."
    )
    parameters = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "check", "stop"],
                "description": "What to do with the stopwatch",
            }
        },
        "required": ["action"],
    }
    writes = True

    def __init__(self) -> None:
        self._started: float | None = None

    def run(self, action: str) -> str:
        action = (action or "").lower().strip()
        if action == "start":
            self._started = time.monotonic()
            return "Stopwatch started."
        if self._started is None:
            return "The stopwatch is not running."
        elapsed = time.monotonic() - self._started
        if action == "stop":
            self._started = None
            return f"Stopwatch stopped at {_describe(elapsed)}."
        return f"Stopwatch is at {_describe(elapsed)}."


def _describe(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
