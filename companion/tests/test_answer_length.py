"""How long an answer should be, decided in code for each turn.

Messages sort into replies (to a remark, or a few words), requests to carry on,
ordinary questions and requests for detail, in English and Turkish; each setting
gives each its note, worded in words; the note goes into that turn's message
and nowhere else; memory knows when the last thing said was a remark; and the
settings page offers the choice.
"""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import Companion
from core.config import AppConfig
from core.length import LENGTHS, length_note, message_kind
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.settings import SETTINGS, apply, current_values
from core.types import ScreenContext

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("what kind of message")

cases = [
    ("Yes.", False, "reply"), ("Not really.", False, "reply"), ("Evet.", False, "reply"),
    ("ok", False, "reply"), ("thanks!", False, "reply"), ("Tamam, anladım.", False, "reply"),
    ("I think it's the gears, honestly, the tiny bronze ones", True, "reply"),
    ("Why is the sky blue?", False, "question"), ("Bitkiler neden yeşil?", False, "question"),
    ("What is this article about?", False, "question"), ("ok?", False, "question"),
    # A few words that ask for something aren't a casual reply.
    ("why", False, "question"), ("explain the gears", False, "question"),
    ("Go on.", False, "continue"), ("continue", True, "continue"), ("devam et", False, "continue"),
    ("more", False, "continue"),
    ("Explain in detail how the Antikythera mechanism worked.", False, "detailed"),
    ("Walk me through setting up Python, step by step.", False, "detailed"),
    ("Bana fotosentezi detaylı anlat.", False, "detailed"), ("Adım adım anlatır mısın?", False, "detailed"),
    # Reported 15 Sep: "more details" was a reply and got one sentence back.
    ("more details", False, "detailed"), ("more detailed answer please", False, "detailed"),
    ("more details", True, "detailed"), ("tell me more", False, "detailed"),
    ("can you elaborate?", False, "detailed"), ("expand on that", False, "detailed"),
    ("daha fazla detay ver", False, "detailed"), ("biraz daha anlat", False, "detailed"),
]
wrong = [(m, after, message_kind(m, after), want) for m, after, want in cases if message_kind(m, after) != want]
check("replies, carrying on, questions and requests for detail, English and Turkish", not wrong, str(wrong))
check("a long message after a remark is a reply", message_kind("I think it's the gears, honestly, the tiny bronze ones", True) == "reply")

print("\nthe note for each setting")

check("normal: replies under 20 words, questions at most about 50",
      "under 20 words" in length_note("Yes.") and "about 50 words" in length_note("Why is the sky blue?"))
check("short: shorter still", "under 15 words" in length_note("Yes.", length="short")
      and "about 30 words" in length_note("Why is the sky blue?", length="short"))
check("detailed: a paragraph or two for questions, replies still brief",
      "paragraph or two" in length_note("Why is the sky blue?", length="detailed")
      and "short sentences" in length_note("Yes.", length="detailed"))
sizes = {l: length_note("more details", length=l) for l in LENGTHS}
check("asking for detail gets a size that grows with the setting: about 80, 150, 250 words",
      "about 80 words" in sizes["short"] and "about 150 words" in sizes["normal"]
      and "about 250 words" in sizes["detailed"], str(sizes))
check("...in plain prose, adding to the last answer rather than repeating it",
      all("no headings, lists or bold" in n and "rather than repeating it" in n for n in sizes.values()))
check("carrying on gets no note, so a cut-off answer can finish", length_note("Go on.") == "")
check("an unknown setting is treated as normal", length_note("Why?", length="huge") == length_note("Why?"))

print("\nremembering that the last thing said was a remark")

memory = ConversationMemory()
check("nothing said yet: no", memory.last_was_remark() is False)
memory.add_remark("That looks intense — did you find anything interesting?")
check("a remark: yes", memory.last_was_remark() is True)
memory.add_turn("Yes.", "Nice — what caught your eye?")
check("after a question and its answer: no", memory.last_was_remark() is False)
memory.add_remark("Timer 'tea' is done.")
check("an announcement after that: yes", memory.last_was_remark() is True)

print("\nin the turn's message")

comp = Companion.__new__(Companion)
comp.config = AppConfig.load(CONFIG_PATH)
comp.audio, comp.tools, comp.memory = None, None, ConversationMemory()
PAGE = ScreenContext(text="An article about gears.", window_title="Article", app_name="brave.exe", source="uia")


def turn(question):
    messages = comp.build_messages(question, PAGE)
    return messages[0].content, messages[-1].content


system, user = turn("Why is the sky blue?")
check("a question's turn says how long, just before the question",
      "[LENGTH — At most about 50 words" in user and user.index("[LENGTH") < user.index("Question:"))
check("...and the standing system prompt doesn't", "[LENGTH" not in system)
comp.memory.add_remark("That looks intense — did you find anything interesting?")
_, user = turn("Yes, the gears")
check("after a remark, a reply's note", "under 20 words" in user, user[-160:])
comp.config.llm.answer_length = "short"
comp.memory.clear()
_, user = turn("Why is the sky blue?")
check("the setting is read each turn, so a change takes effect straight away", "about 30 words" in user)

print("\nthe settings page")

setting = next(s for s in SETTINGS if s.key == "llm.answer_length")
check("Answer length is a choice of short, normal and detailed",
      setting.kind == "choice" and [v for v, _ in setting.choices] == list(LENGTHS))
cfg = AppConfig.load(CONFIG_PATH)
values = current_values(cfg)
values["llm.answer_length"] = "detailed"
changed = apply(cfg, values)
check("choosing one sets it", cfg.llm.answer_length == "detailed" and setting in changed)
values["llm.answer_length"] = "huge"
try:
    apply(cfg, values)
    refused = False
except ValueError:
    refused = True
check("a value that isn't one of the choices is refused", refused and cfg.llm.answer_length == "detailed")

from PySide6.QtWidgets import QApplication, QComboBox  # noqa: E402

from modules.ui.settings_dialog import SettingsDialog  # noqa: E402

qt = QApplication.instance() or QApplication(sys.argv)
dialog = SettingsDialog(cfg, defaults=current_values(cfg), monitors=[(1, "Monitor 1")])
combo = dialog.widget("llm.answer_length")
check("the page shows it as a drop-down with the current choice",
      isinstance(combo, QComboBox) and combo.currentData() == "detailed" and combo.count() == 3)
combo.setCurrentIndex(0)
check("...and gives back what is picked", dialog.values()["llm.answer_length"] == "short")

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
