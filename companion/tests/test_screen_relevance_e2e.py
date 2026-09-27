"""The screen only with messages about it, with the real model.

Measured before, with the screen's text on every message, on an article and on a
chat full of test output: 35 of 48 everyday messages were pulled onto the screen
("bugün çok yoruldum" -> "Antikythera Mekanismi hakkında bilgi veriyorsun";
"tell me a joke" -> "the screen shows your test suites passed"). After: 0 of 48,
the capital of Japan 8 of 8 (7 before), questions about the screen 12 of 14 as
before -- the two misses are "kaç dişlisi var?" answered with the largest gear's
223 teeth, with the screen sent either way.
"""

import re
import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from core.types import ScreenContext

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = cfg.perception.now_playing = False
cfg.ratings.remember_moments = False
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
ARTICLE = comp.refresh()
CHAT = ScreenContext(text=(
    "Assistant\nThe language pin now sets the spoken voice as well as the written answer.\n"
    "Pinned Turkish, English question | English 5/5 | Turkish 5/5\n"
    "Pinned English, Turkish question | Turkish 5/5 | English 5/5\n"
    "All quick suites pass. Next: reading the screen only when asked about.\n"
    "~/projects/app> pytest -q\n  128 passed in 42s"),
    window_title="Assistant", app_name="assistant.exe", source="uia")
SCREENS = [(ARTICLE, re.compile(r"antikythera|mechanism|gears?\b|orrery|shipwreck|article|bronze|analogue|"
                                r"mekanizma|dişli|makale", re.I)),
           (CHAT, re.compile(r"\bpinned|\bvoice\b|suites?\b|\btests?\b|pytest|passed|sabitle|madde", re.I))]
EVERYDAY = ["hi", "how are you?", "tell me a joke", "I'm tired today", "what should I cook tonight?",
            "what's the capital of Japan?", "merhaba", "nasılsın?", "bugün çok yoruldum",
            "Japonya'nın başkenti neresi?", "bana bir şaka anlat", "do you like music?"]
QUESTIONS = [("What is this article about?", r"antikythera|orrery|analogue|computer"), ("When was it found?", r"1901"),
             ("Who identified the gear?", r"stais"), ("how many gears?", r"\b30\b|thirty"),
             ("What am I reading?", r"antikythera"), ("Bu makale ne anlatıyor?", r"antikythera|mekanizma"),
             ("ne zaman bulunmuş?", r"1901")]

print("everyday messages on busy screens")
pulled = []
for page, pattern in SCREENS:
    for message in EVERYDAY:
        comp.memory.clear()
        reply = comp.ask(message, context=page).text()
        if pattern.search(reply):
            pulled.append(f"{message!r}: {reply[:90]!r}")
for line in pulled:
    print("   ", line)
check(f"hardly any pulled onto the screen ({len(pulled)}/{len(EVERYDAY) * 2}; before 35 of 48)",
      len(pulled) <= 2)

print("\nquestions about the screen")
right = 0
for question, fact in QUESTIONS * 2:
    comp.memory.clear()
    reply = comp.ask(question, context=ARTICLE).text()
    good = bool(re.search(fact, reply, re.I))
    right += good
    if not good:
        print(f"    wrong: {question!r}: {reply[:100]!r}")
check(f"still answered from the screen ({right}/{len(QUESTIONS) * 2})", right >= len(QUESTIONS) * 2 - 2)

print("\na follow-up keeps what the answer had")
comp.memory.clear()
comp.ask("When was it found?", context=ARTICLE).text()
reply = comp.ask("who found it?", context=ARTICLE).text()
check("'who found it?' after a screen question is answered from the screen", bool(re.search(r"stais|sponge|diver", reply, re.I)),
      reply[:100])

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
