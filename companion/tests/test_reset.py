"""Reset buttons on the settings page's "Your data" tab.

Each reset moves one kind of saved data -- activity log, ratings, learned counts,
facts about the user -- into its own dated folder in the archive, deleting
nothing; files in the same folder that aren't that data stay put; the running
companion forgets the learned counts it holds, so they aren't saved back over the
reset; and the page asks first, then shows what is left.
"""

import inspect
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtWidgets import QApplication

from core import reset
from core.config import AppConfig
from core.learning import NEGATIVE, POSITIVE, Learning
from core.logging import setup_logging
from core.ratings import Rated, RatingStore
from core.reflection import about_facts
from core.settings import current_values
from modules.ui.app import CompanionApp
from modules.ui.settings_dialog import SettingsDialog
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


root = Path(tempfile.mkdtemp(prefix="companion-reset-"))
cfg = AppConfig.load(CONFIG_PATH)
cfg.root = root
cfg.activity.folder = "data/activity"
cfg.ratings.file = "data/ratings.jsonl"
cfg.learning.file = "data/learning.json"
cfg.reflection.file = "data/about_you.md"
cfg.archive_folder = "data/archive"
data = root / "data"
archive = data / "archive"
activity = data / "activity"
activity.mkdir(parents=True)
(activity / "activity-2026-08.csv").write_text("date,start\n2026-08-02,10:00\n", encoding="utf-8-sig")
(activity / "activity-2026-09.csv").write_text("date,start\n2026-09-14,15:00\n", encoding="utf-8-sig")
(activity / "my own notes.txt").write_text("keep me", encoding="utf-8")
RatingStore(data / "ratings.jsonl").rate(Rated(kind="answer", reply="Bronze.", message="gears?"), "up")
(data / "about_you.md").write_text("- Is learning Turkish (you said so, 14 Sep)\n", encoding="utf-8")
(data / "about_you.state.json").write_text('{"removed": ["Likes baking"]}', encoding="utf-8")

print("what a reset covers")

check("four resets: activity log, ratings, learned counts, facts",
      [r.id for r in reset.RESETS] == ["activity", "ratings", "learning", "facts"])
check("the page can say what is saved", reset.describe(cfg, "activity").startswith("2 files,")
      and reset.describe(cfg, "learning") == "nothing saved",
      f"{reset.describe(cfg, 'activity')!r} / {reset.describe(cfg, 'learning')!r}")
check("the activity log is only its monthly files",
      [p.name for p in reset.files(cfg, "activity")] == ["activity-2026-08.csv", "activity-2026-09.csv"])
check("the facts are about_you.md and its state file",
      sorted(p.name for p in reset.files(cfg, "facts")) == ["about_you.md", "about_you.state.json"])

print("\nmoved, never deleted")

now = datetime(2026, 9, 14, 18, 5, 0)
moved = reset.archive(cfg, "activity", now=now)
check("into a dated folder in the archive", moved.folder == archive / "activity-2026-09-14-180500",
      str(moved.folder))
check("both months are there, unchanged",
      sorted(p.name for p in moved.moved) == ["activity-2026-08.csv", "activity-2026-09.csv"]
      and "2026-09-14,15:00" in (moved.folder / "activity-2026-09.csv").read_text(encoding="utf-8-sig"))
check("...and gone from the log folder", not reset.files(cfg, "activity"))
check("a file of the user's own in that folder stays", (activity / "my own notes.txt").is_file())

(activity / "activity-2026-09.csv").write_text("date\n", encoding="utf-8")
again = reset.archive(cfg, "activity", now=now)
check("a second reset in the same second gets its own folder",
      again.folder == archive / "activity-2026-09-14-180500-2"
      and (moved.folder / "activity-2026-08.csv").is_file(), str(again.folder))

rated = reset.archive(cfg, "ratings", now=now)
check("ratings: moved, and the store starts empty",
      RatingStore(data / "ratings.jsonl").ratings() == []
      and "Bronze." in (rated.folder / "ratings.jsonl").read_text(encoding="utf-8"))
facts = reset.archive(cfg, "facts", now=now)
check("facts: both files moved, no facts left, deleted facts forgotten with them",
      about_facts(data / "about_you.md") == [] and not (data / "about_you.state.json").exists()
      and "Likes baking" in (facts.folder / "about_you.state.json").read_text(encoding="utf-8"))

before = sorted(archive.iterdir())
nothing = reset.archive(cfg, "facts", now=now)
check("with nothing saved, nothing moves and no folder is made",
      nothing.folder is None and nothing.moved == [] and sorted(archive.iterdir()) == before)

print("\nthe running companion forgets its learned counts")

learning = Learning(data / "learning.json")
learning.record("NASA", "opinion", "arrived", NEGATIVE)
learning.record("NASA", "opinion", "arrived", NEGATIVE)
worker = CompanionWorker(cfg)
worker._orchestrator = SimpleNamespace(learning=learning)
result = worker.reset_data("learning")
check("the file is moved to the archive",
      result.folder is not None and '"negative": 2' in (result.folder / "learning.json").read_text(encoding="utf-8"))
check("...and the counts in memory are gone", learning.counts == {"sites": {}, "moves": {}, "moments": {}}
      and learning.allowance("NASA") == 1.0, str(learning.counts))
learning.record("NASA", "opinion", "arrived", POSITIVE)
saved = json.loads((data / "learning.json").read_text(encoding="utf-8"))
check("the next outcome starts a fresh file, not the old counts saved back",
      saved["sites"]["NASA"] == {"positive": 1, "negative": 0, "ignored": 0}, str(saved["sites"]))
seen = []
learning.forget(lambda: seen.append(learning._lock.locked()))
check("the file is moved while outcomes are held off", seen == [True])
worker._orchestrator = None
(data / "learning.json").write_text("{}", encoding="utf-8")
check("with learning off, the reset still moves the file",
      worker.reset_data("learning").folder is not None and not (data / "learning.json").exists())

print("\nthe settings page")

qt = QApplication.instance() or QApplication(sys.argv)
RatingStore(data / "ratings.jsonl").rate(Rated(kind="remark", reply="Eclipses, from gears?"), "up")
calls = []


def on_reset(reset_id):
    calls.append(reset_id)
    return reset.archive(cfg, reset_id)


dialog = SettingsDialog(cfg, defaults=current_values(cfg), monitors=[(1, "Monitor 1")], on_reset=on_reset)
told = []
dialog._tell = lambda text, problem=False: told.append((text, problem))
check("there is a Your data tab",
      "Your data" in [dialog.tabs.tabText(i) for i in range(dialog.tabs.count())])
status, button = dialog.reset_row("ratings")
check("a row shows what is saved, with its Reset button ready",
      status.text().startswith("1 file,") and button.isEnabled(), status.text())
status_facts, button_facts = dialog.reset_row("facts")
check("with nothing saved, the button is greyed out",
      status_facts.text() == "nothing saved" and not button_facts.isEnabled())
text = dialog.confirm_text(next(r for r in reset.RESETS if r.id == "ratings"))
check("it asks first, saying the files are moved to the archive, not deleted",
      "not deleted" in text and str(archive) in text, text)

dialog._confirm_reset = lambda item: False
button.click()
check("answering No resets nothing", calls == [] and (data / "ratings.jsonl").is_file())
dialog._confirm_reset = lambda item: True
button.click()
check("answering Yes resets it", calls == ["ratings"] and not (data / "ratings.jsonl").exists())
check("...the row updates, and it says where the files went",
      status.text() == "nothing saved" and not button.isEnabled()
      and told and str(archive) in told[-1][0] and not told[-1][1], str(told[-1:]))


def broken(reset_id):
    raise PermissionError("the file is open in another program")


(data / "learning.json").write_text("{}", encoding="utf-8")
dialog.on_reset = broken
dialog._show_saved(next(r for r in reset.RESETS if r.id == "learning"))
dialog.reset_row("learning")[1].click()
check("a reset that fails says so, and the page carries on",
      told[-1][1] and "open in another program" in told[-1][0], str(told[-1:]))
check("the app gives the page the worker's reset, which also clears memory",
      "on_reset=self.worker.reset_data" in inspect.getsource(CompanionApp._open_settings))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
