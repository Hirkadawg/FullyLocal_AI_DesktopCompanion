"""A log of the pages the user spends time on, in files they can read.

One CSV file per month (`activity-2026-09.csv`), one line per visit: date, start,
end, minutes, app, window title, the one-line description when one was made, and
whether the companion remarked on it and how that went. It opens in Notepad++ or
Excel, and lines edited or deleted by hand are simply what the log holds from then
on -- there is no hidden database behind it.

Never stored: page text, screenshots, anything from a window on the privacy list
(those screens never reach the orchestrator). Nothing leaves this machine.

Measured before choosing plain files over SQLite (14 September 2026): about 170
bytes a visit (SQLite with a search index took 240); five years at 100 pages a
day is 29 MB; searching one month takes 14 ms and all five years under a second.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

from core.logging import get_logger

log = get_logger(__name__)

FIELDS = ("date", "start", "end", "minutes", "app", "title", "activity", "remarks", "outcome")

#: Words that say nothing about which page is meant.
_STOPWORDS = frozenset("""
the and that this what was were with about from have had has for you your earlier
yesterday today tonight read reading watched watching watch saw seen looked look page
article video site website post thread some which when where who how did does been
there their them they its it's i'm into onto just last week month morning time
bir bu şu ne neydi dün bugün önce daha okuduğum izlediğim baktığım hakkında vardı
olan geçen sabah
""".split())


@dataclass
class Visit:
    started: datetime
    ended: datetime
    app: str
    title: str
    activity: str = ""
    remarks: int = 0
    #: "replied", "dismissed" or "ignored" when remarked on; "" otherwise.
    outcome: str = ""

    @property
    def minutes(self) -> int:
        return max(0, round((self.ended - self.started).total_seconds() / 60))

    def row(self) -> list[str]:
        return [
            f"{self.started:%Y-%m-%d}", f"{self.started:%H:%M}", f"{self.ended:%H:%M}",
            str(self.minutes), self.app, self.title, self.activity,
            str(self.remarks), self.outcome,
        ]

    @classmethod
    def from_row(cls, row: dict) -> "Visit | None":
        """A line from the file, or None if it was edited into something unreadable."""
        try:
            day = datetime.strptime(row["date"].strip(), "%Y-%m-%d")
            start = datetime.strptime(row["start"].strip(), "%H:%M")
            end = datetime.strptime(row["end"].strip(), "%H:%M")
        except (KeyError, ValueError, AttributeError):
            return None
        started = day.replace(hour=start.hour, minute=start.minute)
        ended = day.replace(hour=end.hour, minute=end.minute)
        if ended < started:  # past midnight
            ended += timedelta(days=1)
        try:
            remarks = int((row.get("remarks") or "0").strip() or 0)
        except ValueError:
            remarks = 0
        return cls(started, ended, (row.get("app") or "").strip(), (row.get("title") or "").strip(),
                   (row.get("activity") or "").strip(), remarks, (row.get("outcome") or "").strip())

    def line(self) -> str:
        """How the search tool shows it to the model."""
        text = (f"{self.started:%a %d %b}, {self.started:%H:%M}-{self.ended:%H:%M} "
                f"({self.minutes} min): {self.title or '(untitled)'}")
        if self.app:
            text += f", in {self.app}"
        if self.activity:
            text += f" -- {self.activity}"
        if self.remarks:
            text += f" (you remarked on it{', they ' + self.outcome if self.outcome else ''})"
        return text


def time_range(since: str, now: datetime | None = None) -> tuple[datetime | None, datetime | None]:
    """(start, end) for "today", "yesterday", "this week", "this month" or "any"."""
    now = now or datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    since = (since or "any").strip().lower()
    if since == "today":
        return today, None
    if since == "yesterday":
        return today - timedelta(days=1), today
    if since == "this week":
        return today - timedelta(days=today.weekday()), None
    if since == "this month":
        return today.replace(day=1), None
    return None, None


class ActivityLog:
    def __init__(self, folder: Path | str) -> None:
        self.folder = Path(folder)

    def file_for(self, when: datetime) -> Path:
        return self.folder / f"activity-{when:%Y-%m}.csv"

    def record(self, visit: Visit) -> None:
        path = self.file_for(visit.started)
        self.folder.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            # utf-8-sig once, at the top: Excel then shows "ş" and "ı" correctly.
            with path.open("w", newline="", encoding="utf-8-sig") as handle:
                csv.writer(handle).writerow(FIELDS)
        with path.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle).writerow(visit.row())
        log.info("logged %d min on %s", visit.minutes, visit.title[:60])

    def visits(self, start: datetime | None = None, end: datetime | None = None) -> Iterator[Visit]:
        """Every readable visit in range, newest first."""
        if not self.folder.is_dir():
            return
        files = sorted(self.folder.glob("activity-????-??.csv"), reverse=True)
        for path in files:
            month = path.stem[len("activity-"):]
            if start is not None and month < f"{start:%Y-%m}":
                break
            try:
                with path.open(newline="", encoding="utf-8-sig") as handle:
                    rows = list(csv.DictReader(handle))
            except (OSError, csv.Error, UnicodeDecodeError):
                log.warning("could not read %s; skipping it", path)
                continue
            for row in reversed(rows):
                visit = Visit.from_row(row)
                if visit is None:
                    continue
                if start is not None and visit.started < start:
                    continue
                if end is not None and visit.started >= end:
                    continue
                yield visit

    def search(self, query: str, start: datetime | None = None, end: datetime | None = None,
               limit: int = 5) -> list[Visit]:
        """Visits whose title, app or description share the most words with `query`.

        With no meaningful words -- "what did I read yesterday?" -- the most
        recent visits in range.
        """
        terms = [w for w in re.findall(r"\w{3,}", (query or "").casefold()) if w not in _STOPWORDS]
        if not terms:
            return list(_take(self.visits(start, end), limit))
        scored = []
        for visit in self.visits(start, end):
            text = f"{visit.title} {visit.app} {visit.activity}".casefold()
            score = sum(term in text for term in terms)
            if score:
                scored.append((score, visit.started, visit))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [visit for _, _, visit in scored[:limit]]


def _take(items: Iterator[Visit], limit: int) -> Iterator[Visit]:
    for i, item in enumerate(items):
        if i >= limit:
            return
        yield item
