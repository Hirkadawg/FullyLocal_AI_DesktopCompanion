"""Starting over: the reset buttons on the settings page's "Your data" tab.

Four things the companion keeps about how it has been used can be reset: the
activity log, the ratings, the counts of where remarks are welcome, and the
facts about the user. A reset **moves** the files into the archive folder
(`archive_folder`, data/archive), one dated folder per reset, and deletes
nothing -- the user wants everything kept safe, and a mistaken reset is undone
by moving the files back. Emptying the archive is left to the user.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from core.logging import get_logger
from core.reflection import _state_path

log = get_logger(__name__)


@dataclass(frozen=True)
class Reset:
    id: str
    label: str
    help: str


RESETS: tuple[Reset, ...] = (
    Reset("activity", "Activity log",
          "The pages you spent time on. \"What did I read yesterday?\" starts from nothing."),
    Reset("ratings", "Ratings",
          "Every thumbs up and down — and so what the liked-replies export can find."),
    Reset("learning", "Where remarks are welcome",
          "The counts per site, kind of remark and moment. Every site goes back to normal."),
    Reset("facts", "Facts about you",
          "about_you.md and its state file. Facts you had deleted may be learned again."),
)


@dataclass
class ResetResult:
    #: Where each file is now, inside the archive.
    moved: list[Path] = field(default_factory=list)
    #: The dated archive folder, or None when there was nothing to move.
    folder: Path | None = None


def _full(config, path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else config.root / p


def archive_folder(config) -> Path:
    return _full(config, config.archive_folder)


def files(config, reset_id: str) -> list[Path]:
    """The files a reset would move, as they are now."""
    if reset_id == "activity":
        folder = _full(config, config.activity.folder)
        # Only the log's own files: anything else kept in that folder stays.
        return sorted(folder.glob("activity-????-??.csv")) if folder.is_dir() else []
    if reset_id == "ratings":
        candidates = [_full(config, config.ratings.file)]
    elif reset_id == "learning":
        candidates = [_full(config, config.learning.file)]
    elif reset_id == "facts":
        facts = _full(config, config.reflection.file)
        candidates = [facts, _state_path(facts)]
    else:
        raise ValueError(f"unknown reset {reset_id!r}")
    return [p for p in candidates if p.is_file()]


def _size(n: int) -> str:
    if n < 1024:
        return f"{n} bytes"
    if n < 1024 * 1024:
        return f"{n / 1024:.0f} KB"
    return f"{n / 1024 / 1024:.1f} MB"


def describe(config, reset_id: str) -> str:
    """What is saved now, for the page: "2 files, 14 KB" or "nothing saved"."""
    found = files(config, reset_id)
    if not found:
        return "nothing saved"
    size = sum(p.stat().st_size for p in found)
    return f"{len(found)} file{'s' if len(found) != 1 else ''}, {_size(size)}"


def archive(config, reset_id: str, now: datetime | None = None) -> ResetResult:
    """Move one kind of data into a new dated folder in the archive."""
    found = files(config, reset_id)
    if not found:
        return ResetResult()
    now = now or datetime.now()
    base = archive_folder(config) / f"{reset_id}-{now:%Y-%m-%d-%H%M%S}"
    folder, n = base, 2
    while folder.exists():  # two resets in one second never share a folder
        folder = base.with_name(f"{base.name}-{n}")
        n += 1
    folder.mkdir(parents=True)
    result = ResetResult(folder=folder)
    for path in found:
        target = folder / path.name
        # A move, even across drives: copied, then removed only once copied.
        shutil.move(str(path), str(target))
        result.moved.append(target)
    log.info("reset %s: moved %d file(s) to %s", reset_id, len(result.moved), folder)
    return result
