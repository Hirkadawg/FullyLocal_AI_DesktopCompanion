"""Thumbs up or down on answers and remarks, kept on this machine.

Offline fine-tuning on hand-picked transcripts is the dependable way a companion
like this learns a voice. Ratings collected now are that hand-picking, and a
signal for learning when to speak. Nothing is sent anywhere.

One JSON object per line, UTF-8. A rating is appended; rating the same reply
again replaces its line instead of adding another, so the file holds one current
rating per reply. Kept forever; the settings page will manage it.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.logging import get_logger

log = get_logger(__name__)

RATINGS = ("up", "down")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Rated:
    """Something the companion said that can be rated."""

    kind: str  # "answer" or "remark"
    reply: str
    #: The user's message, for an answer.
    message: str = ""
    #: The window it was about.
    page: str = ""
    #: For remarks: the kind of remark, why it was made, and what prompted it.
    move: str = ""
    why: str = ""
    trigger: str = ""
    at: str = field(default_factory=_now)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])


class RatingStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def rate(self, item: Rated, rating: str) -> None:
        """Record a rating, replacing any earlier rating of the same reply."""
        if rating not in RATINGS:
            raise ValueError(f"rating must be one of {RATINGS}, not {rating!r}")
        record = {
            "id": item.id,
            "time": item.at,
            "rated_at": _now(),
            "kind": item.kind,
            "rating": rating,
            "message": item.message,
            "page": item.page,
            "reply": item.reply,
        }
        if item.kind == "remark":
            record.update(move=item.move, why=item.why, trigger=item.trigger)
        line = json.dumps(record, ensure_ascii=False)

        self.path.parent.mkdir(parents=True, exist_ok=True)
        lines = self._lines()
        for index, existing in enumerate(lines):
            if _id_of(existing) == item.id:
                lines[index] = line
                self._rewrite(lines)
                log.info("rating changed to %s: %s", rating, item.reply[:60])
                return
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        log.info("rated %s: %s", rating, item.reply[:60])

    def ratings(self) -> list[dict]:
        """Every readable record, oldest first."""
        records = []
        for line in self._lines():
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        return records

    def _lines(self) -> list[str]:
        if not self.path.is_file():
            return []
        text = self.path.read_text(encoding="utf-8")
        return [line for line in text.splitlines() if line.strip()]

    def _rewrite(self, lines: list[str]) -> None:
        # Written aside and swapped in, so a crash mid-write can't cost the
        # ratings already collected. Unreadable lines are kept as they were.
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
        os.replace(temporary, self.path)


def _id_of(line: str) -> str | None:
    try:
        return json.loads(line).get("id")
    except (ValueError, AttributeError):
        return None
