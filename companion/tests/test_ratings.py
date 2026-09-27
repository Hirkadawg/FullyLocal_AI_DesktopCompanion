"""Thumbs up or down on answers and remarks, stored locally.

Neuro-sama's one confirmed way of learning is offline fine-tuning on hand-picked
transcripts. Ratings collected now become that selection and a
signal for when to speak (F10). A click appends one line to a JSONL file on this
machine; a second click on the same reply replaces its line.
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from core.orchestrator import Remark
from core.ratings import Rated, RatingStore
from core.types import Answer, ScreenContext
from modules.ui.app import CompanionApp
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("the store")

path = Path(tempfile.mkdtemp(prefix="companion-ratings-")) / "sub" / "ratings.jsonl"
store = RatingStore(path)
answer = Rated(kind="answer", reply="About 2,100 years old.", message="How old is it?",
               page="Antikythera mechanism - Wikipedia")
remark = Rated(kind="remark", reply="Dişliler bronzdan yapılmış, inanılmaz.",
               page="Antikythera mechanism - Wikipedia", move="opinion",
               why="the gears are the surprising part", trigger="requested")


def lines():
    return path.read_text(encoding="utf-8").splitlines()


store.rate(answer, "up")
check("a click appends one line, creating the folder", path.is_file() and len(lines()) == 1)
record = json.loads(lines()[0])
check("...with time, kind, rating, message, page and reply",
      all(record.get(k) for k in ("time", "rated_at", "kind", "rating", "message", "page", "reply"))
      and record["kind"] == "answer" and record["rating"] == "up", str(record))
check("an answer carries no remark fields", "move" not in record and "why" not in record)

store.rate(answer, "down")
check("a second click on the same reply replaces its rating", len(lines()) == 1
      and json.loads(lines()[0])["rating"] == "down", str(lines()))

store.rate(remark, "up")
check("another reply adds a line", len(lines()) == 2)
record = json.loads(lines()[1])
check("a remark carries its move, reason and trigger",
      record.get("move") == "opinion" and record.get("why") == remark.why
      and record.get("trigger") == "requested", str(record))
check("Turkish is stored readably, not escaped", "bronzdan yapılmış" in path.read_text(encoding="utf-8"))

with path.open("a", encoding="utf-8") as handle:
    handle.write("not json at all\n")
store.rate(remark, "down")
check("replacing a rating keeps an unreadable line rather than losing it",
      "not json at all" in path.read_text(encoding="utf-8") and len(lines()) == 3)
check("...and reading skips it", [r["rating"] for r in store.ratings()] == ["down", "down"],
      str(store.ratings()))

try:
    store.rate(answer, "meh")
    check("an unknown rating is refused", False)
except ValueError:
    check("an unknown rating is refused", True)

cfg = AppConfig.load(CONFIG_PATH)
check("tests write ratings to the test folder, not companion/data",
      helpers.TEST_DATA_DIR.resolve() in (cfg.root / cfg.ratings.file).resolve().parents,
      cfg.ratings.file)

print("\nthe window")

qt = QApplication.instance() or QApplication(sys.argv)
window = ChatWindow(cfg)
clicks = []
window.rated.connect(lambda item, rating: clicks.append((item.id, rating)))

window.add_question("How old is it?")
window.add_chunk("About 2,100 years old.")
first = Rated(kind="answer", reply="About 2,100 years old.")
window.add_rating(first)
window.add_notice("Those tiny bronze gears are wild.", "#9db8d6")
second = Rated(kind="remark", reply="Those tiny bronze gears are wild.")
window.add_rating(second)

text = window.transcript.toPlainText()
check("each reply gets a thumbs up and a thumbs down",
      text.count("👍") == 2 and text.count("👎") == 2, repr(text))
html = window.transcript.toHtml()
check("...as links to its own id", f"rate:{first.id}:up" in html and f"rate:{second.id}:down" in html)

window._on_anchor(QUrl(f"rate:{first.id}:up"))
check("clicking a thumb reports the item and the rating", clicks == [(first.id, "up")], str(clicks))
check("...and highlights the chosen thumb", "#34405a" in window.transcript.toHtml())
window._on_anchor(QUrl(f"rate:{first.id}:down"))
window._on_anchor(QUrl(f"rate:{second.id}:up"))
check("changing it, and rating a later reply, still find the right thumbs",
      clicks[1:] == [(first.id, "down"), (second.id, "up")], str(clicks))
check("...without duplicating any",
      window.transcript.toPlainText().count("👍") == 2, window.transcript.toPlainText())

window._on_anchor(QUrl("rate:nonexistent:up"))
window._on_anchor(QUrl(f"https://example.com/{first.id}"))
check("unknown ids and other links do nothing", len(clicks) == 3)

window.add_chunk("A new answer.")
found = window.transcript.document().find("A new answer.")
check("text after the thumbs is not a link",
      not found.isNull() and not found.charFormat().isAnchor(),
      "not found" if found.isNull() else found.charFormat().anchorHref())

cfg.ratings.enabled = False
off = ChatWindow(cfg)
off.add_rating(Rated(kind="answer", reply="x"))
check("with ratings off, no thumbs", "👍" not in off.transcript.toPlainText())
cfg.ratings.enabled = True

print("\nwhat reaches the window from the worker and the app")


class FakeCompanion:
    def __init__(self):
        from core.memory import ConversationMemory
        self.memory = ConversationMemory()

    def observe(self, max_age_s=0.0):
        return ScreenContext(text="SCREEN CONTENT", window_title="Gears - Wikipedia",
                             app_name="brave.exe", source="uia")

    def ask(self, question, context=None, interrupted=None):
        return Answer(context=context, chunks=iter(["About ", "2,100 years."]))


worker = CompanionWorker(cfg)
worker._companion = FakeCompanion()
finished = []
worker.answered.connect(finished.append)
worker._answer("How old is it?")
check("a finished answer reports its question, reply and page",
      finished == [{"question": "How old is it?", "reply": "About 2,100 years.",
                    "page": "Gears - Wikipedia"}], str(finished))

added, saved = [], []
fake = SimpleNamespace(
    config=cfg, icon=None, _idle_status="ready", ratings=SimpleNamespace(rate=lambda i, r: saved.append((i, r))),
    _refresh_level=lambda announce=False: None,
    tray=SimpleNamespace(showMessage=lambda *a: None),
    window=SimpleNamespace(add_rating=added.append, add_notice=lambda *a: None,
                           add_reason=lambda *a: None, isVisible=lambda: True,
                           is_answering=False, set_status=lambda *a: None),
)
CompanionApp._on_answered(fake, finished[0])
check("the app offers a rating of the answer",
      len(added) == 1 and added[0].kind == "answer" and added[0].message == "How old is it?"
      and added[0].page == "Gears - Wikipedia", str(added))

made = Remark(text="Wild gears.", move="question", why="the gears stand out",
              trigger="arrived", activity="reading", page="Gears - Wikipedia")
CompanionApp._on_remark(fake, made.text, made.why, made)
check("...and of a remark, with its move, reason, trigger and page",
      len(added) == 2 and added[1].kind == "remark" and added[1].move == "question"
      and added[1].why == made.why and added[1].trigger == "arrived"
      and added[1].page == "Gears - Wikipedia", str(added[-1:]))

CompanionApp._on_rated(fake, added[0], "up")
check("a click is saved to the store", saved == [(added[0], "up")], str(saved))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
