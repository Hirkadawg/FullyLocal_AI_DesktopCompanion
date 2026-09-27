"""Shared moments, with the real model.

Measured before, with moments collected in use and moments off:
- "do you remember what we talked about with sourdough?": "I don't have access to our
  past conversations" -- or, in Turkish, "hatırlıyorum" and a summary of the
  page on screen, invented; the liked question came back 1 time in 6;
- "why did you ask me that question about sourdough?": "I didn't ask you about
  finding where to start" 0 of 6 with the reason (2 regex hits were the page);
- "what did we talk about last time?" on Google: nothing real, 0 of 6;
- remarks on the sourdough page: 1 or 2 in 12 near the liked question, by chance.

After, while building:
- recall 6 of 6 in English and Turkish, once Turkish question words ("musun")
  stopped matching an unrelated Turkish moment; the reason 6 of 6; something
  real 6 of 6;
- who said what: a remark addressed to "you" ("That last lap cost you the
  lead, buddy") came back as "you mentioned" 3-4 times in 12, whatever the wording;
- remarks called back to the moment 8 in 12, re-asking the same question 3 in
  12 (6 in 11 with "without repeating it").
"""

import json
import re
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from core.orchestrator import MOVE_ORDER, Orchestrator, load_persona
from core.types import ScreenContext
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0
RUNS = 6


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


yesterday = (datetime.now() - timedelta(days=1)).replace(hour=17, minute=30, second=0, microsecond=0)
today = datetime.now().replace(hour=9, minute=0, second=0, microsecond=0)
LIKED = "Does the text say how a baker actually knows the starter is ready?"
RECORDS = [
    {"time": yesterday.isoformat(), "kind": "answer", "rating": "up",
     "message": "I have been learning about sourdough", "page": "Sourdough starter - Wikipedia - Brave",
     "reply": "That's a deep dive into fermentation, especially the timing details that decide the crumb."},
    {"time": (yesterday - timedelta(minutes=1)).isoformat(), "kind": "remark", "rating": "up",
     "page": "Sourdough starter - Wikipedia - Brave", "reply": LIKED, "move": "question", "trigger": "requested",
     "why": "The article gives feeding times but never says how the baker judges that it is ready."},
    {"time": today.isoformat(), "kind": "remark", "rating": "up", "page": "Orbit Racer",
     "reply": "That paint job is lovely, but the rest of the grid looks thrown together.",
     "why": "It comments on the visual oddity without giving advice."},
    {"time": (today - timedelta(minutes=5)).isoformat(), "kind": "remark", "rating": "up", "page": "Orbit Racer",
     "reply": "That last lap cost you the lead, buddy.", "why": "A direct reaction to the race result on screen."},
]
ratings = Path(tempfile.mkdtemp(prefix="companion-moments-e2e-")) / "ratings.jsonl"
ratings.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n", encoding="utf-8")

cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = cfg.vision.enabled = False
cfg.perception.now_playing = False  # this PC's music would leak in
cfg.ratings.file = str(ratings)
cfg.ratings.remember_moments = True
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()

SOURDOUGH = ScreenContext(text=(
    "Sourdough starter\nFrom Wikipedia, the free encyclopedia\n"
    "A sourdough starter is a fermented mixture of flour and water holding wild yeasts and lactic acid bacteria. "
    "It is kept alive by discarding part of it and feeding the rest with fresh flour and water, usually once or "
    "twice a day at room temperature. The yeasts make the carbon dioxide that raises the dough, while the bacteria "
    "make the acids that give the bread its sour taste and keep moulds away. A starter is often said to be ready "
    "when it doubles within four to six hours of a feeding and smells tangy rather than sharp. Bakers judge it by "
    "eye and by smell, and many keep a spoonful in the fridge between bakes."),
    window_title="Sourdough starter - Wikipedia - Brave", app_name="brave.exe", source="uia")
GOOGLE = ScreenContext(text="Google\nSearch Google or type a URL\nGmail Images", window_title="Google - Brave",
                       app_name="brave.exe", source="uia")


def answers(questions, page):
    replies = []
    for i in range(RUNS):
        comp.memory.clear()
        replies.append(comp.ask(questions[i % len(questions)], context=page).text())
        print(f"    {replies[-1][:130]!r}")
    return replies


def count(pattern, replies):
    return sum(bool(re.search(pattern, reply, re.I)) for reply in replies)


print("do you remember what we talked about, on the sourdough page")
got = answers(["do you remember what we talked about with sourdough?",
               "ekşi maya hakkında ne konuşmuştuk, hatırlıyor musun?"], SOURDOUGH)
found = count(r"ready|readiness|feed|judge|double|hazır|besle", got)
check(f"the liked question about knowing it is ready ({found}/{RUNS}; before 1/6)", found >= RUNS - 1)

print("\nwhy it asked")
got = answers(["do you remember why you asked me that question about sourdough?",
               "why did you ask me about knowing when a starter is ready?"], SOURDOUGH)
found = count(r"feeding|times|ready|judge|besle|hazır", got)
check(f"the remark's reason ({found}/{RUNS}; before 0/6)", found >= RUNS - 1)

print("\nwhat did we talk about, on a page nothing matches")
got = answers(["what did we talk about last time?", "do you remember anything we talked about?"], GOOGLE)
real = count(r"orbit|lap|paint|grid|sourdough|starter", got)
check(f"something that really happened ({real}/{RUNS}; before 0/6)", real >= RUNS - 1)
mixed = count(r"you (?:mentioned|said|told|noted)", got)
check(f"...mostly keeping its own remarks as its own ({RUNS - mixed}/{RUNS}; measured 8-9 in 12)", mixed <= 2)

print("\nremarks on the sourdough page")
persona = load_persona(cfg.root / cfg.proactive.persona_file)
worker = SimpleNamespace(_config=cfg)
made = []
for i in range(RUNS * 2):
    orch = Orchestrator(comp.llm, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=cfg.proactive.min_chars),
                        max_words=cfg.proactive.max_words, temperature=cfg.proactive.temperature,
                        min_time_on_page_s=0, persona=persona,
                        shared=lambda c: CompanionWorker._shared_moments(worker, c))
    orch._last_tried = MOVE_ORDER[(i - 1) % len(MOVE_ORDER)]
    orch.observe(SOURDOUGH)
    remark = orch.poll()
    if remark is not None:
        made.append(remark.text)
        print(f"    {remark.text}")
callback = sum(bool(re.search(r"ready|readiness|feed|judge|double|smell|again|yesterday|earlier", t, re.I))
               for t in made)
rehash = sum(bool(re.search(r"(?=.*(?:know|tell|judge|decide|figure))"
                            r"(?=.*(?:ready|readiness|done|risen))", t, re.I | re.S)) for t in made)
check(f"remarks are still made ({len(made)}/{RUNS * 2})", len(made) >= RUNS * 2 - 2)
check(f"they call back to the liked moment ({callback}/{len(made)}; before 1-2 in 12)", callback >= len(made) // 3)
check(f"...without mostly re-asking it ({rehash}/{len(made)}; measured 3 in 12)", rehash <= len(made) // 2)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
