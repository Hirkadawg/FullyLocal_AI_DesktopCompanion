"""Your level with the companion, grown by your votes.

Asked for: a level that grows each time enough votes have been cast to make a
noticeable difference -- 50 votes for level 1, then 80 more for level 2, in the
user's example. Decided with the user: levels show from the start with
provisional steps, since how many votes really make a difference can't be
measured until a fine-tune on rated replies can; a ratings reset starts the
level over; careless votes aren't guarded against, and the level screen says
so. Steps: 50, 80, then 30 more each level. There were 15 votes then.
"""

import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR", r"C:\Windows\Fonts")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtWidgets import QApplication

from core import relationship
from core.config import AppConfig, RatingsConfig
from core.logging import setup_logging
from core.ratings import Rated, RatingStore
from core.relationship import count_votes, describe, level_for, step_for
from core.settings import SETTINGS
from modules.ui.app import CompanionApp
from modules.ui.window import ChatWindow

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("the steps")

check("the user's example: 50 votes for level 1, then 80 more for level 2",
      level_for(49).level == 0 and level_for(50).level == 1 and level_for(129).level == 1
      and level_for(130).level == 2)
check("each level needs more votes than the one before", [step_for(n) for n in range(6)] == [50, 80, 110, 140, 170, 200],
      str([step_for(n) for n in range(6)]))
now = level_for(15)
check("15 votes: level 0, 15 of 50, 35 to go",
      (now.level, now.into, now.step, now.to_next) == (0, 15, 50, 35))
check("progress starts again at each level", (level_for(75).into, level_for(75).step) == (25, 80))
check("no votes, or a nonsense count, is level 0", level_for(0).level == 0 and level_for(-3).level == 0)
check("the steps are marked provisional until measured", relationship.PROVISIONAL is True)

print("\nvotes, and the level screen")

store = RatingStore(Path(tempfile.mkdtemp(prefix="companion-level-")) / "ratings.jsonl")
first, second = Rated(kind="answer", reply="One."), Rated(kind="remark", reply="Two.")
store.rate(first, "up")
store.rate(second, "down")
store.rate(first, "down")
check("every rated reply counts once, up or down; changing a vote adds none", count_votes(store.ratings()) == (0, 2),
      str(count_votes(store.ratings())))
text = describe(level_for(15), 12, 3)
check("the level screen says the votes, what's left, that the steps are a first guess, about careless votes, "
      "and that a reset starts over",
      all(part in text for part in ("15 votes", "12 👍", "3 👎", "35 more for level 1", "50, 80, 110, 140",
                                    "first guess", "careless", "Your data")), text)

print("\nthe window")

qt = QApplication.instance() or QApplication(sys.argv)
cfg = AppConfig.load(CONFIG_PATH)
cfg.speech.enabled, cfg.speech.languages = True, ["en", "tr"]
cfg.speech.mic_button = cfg.speech.listen_button = True
cfg.proactive.enabled = True
window = ChatWindow(cfg)
check("hidden until the app sets a level", window.level_button.isHidden())
window.set_level(level_for(15), 12, 3)
check("a small badge beside the title: Lv 0 · 15/50",
      not window.level_button.isHidden() and window.level_button.text() == "Lv 0 · 15/50", window.level_button.text())
check(f"...and with every header button showing, the header still fits ui.width ({cfg.ui.width}px)",
      window.minimumSizeHint().width() <= cfg.ui.width, f"needs {window.minimumSizeHint().width()}px")
check("...its details are the level screen", window._level_details == text)
window.set_level(None)
check("...hidden again when the level is off", window.level_button.isHidden())


class FakeWindow:
    def __init__(self):
        self.levels, self.notices = [], []

    def set_level(self, level, up=0, down=0):
        self.levels.append(None if level is None else (level.level, level.into))

    def add_notice(self, text, colour=""):
        self.notices.append(text)


class FakeStore:
    def __init__(self, votes):
        self.records = [{"rating": "up"}] * votes

    def ratings(self):
        return list(self.records)


def app(votes, show=True, enabled=True):
    config = AppConfig.load(CONFIG_PATH)
    config.ratings.show_level, config.ratings.enabled = show, enabled
    return SimpleNamespace(config=config, ratings=FakeStore(votes), window=FakeWindow(), _level=None)


print("\nthe app")

a = app(49)
CompanionApp._refresh_level(a)
check("at start the window shows the level, announcing nothing", a.window.levels == [(0, 49)] and not a.window.notices)
a.ratings.records.append({"rating": "down"})
CompanionApp._refresh_level(a, announce=True)
check("the vote that reaches a new level says so", a.window.levels and a.window.levels[-1] == (1, 0) and a.window.notices
      and "now 1" in a.window.notices[-1], str(a.window.notices))
a.ratings.records.append({"rating": "up"})
CompanionApp._refresh_level(a, announce=True)
check("...once, not at every vote after", len(a.window.notices) == 1)
a.ratings.records.clear()
CompanionApp._refresh_level(a)
check("a ratings reset starts the level over, quietly",
      bool(a.window.levels) and a.window.levels[-1] == (0, 0) and len(a.window.notices) == 1)
off, disabled = app(10, show=False), app(10, enabled=False)
CompanionApp._refresh_level(off)
CompanionApp._refresh_level(disabled)
check("with the setting or ratings off, no level is shown", off.window.levels == [None] and disabled.window.levels == [None])
events = []
fake = SimpleNamespace(config=AppConfig.load(CONFIG_PATH), window=FakeWindow(),
                       ratings=SimpleNamespace(rate=lambda item, rating: events.append(rating)),
                       worker=SimpleNamespace(note_remark_rating=lambda *args: None),
                       _refresh_level=lambda announce=False: events.append(("level", announce)))
CompanionApp._on_rated(fake, Rated(kind="answer", reply="Yes."), "up")
check("a vote is saved, then the level is updated, announcing a new one", events == ["up", ("level", True)], str(events))
check("on in config.yaml, off in code, and on the settings page",
      RatingsConfig().show_level is False and AppConfig.load(CONFIG_PATH).ratings.show_level is True
      and any(s.key == "ratings.show_level" and s.kind == "bool" and s.live for s in SETTINGS))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
