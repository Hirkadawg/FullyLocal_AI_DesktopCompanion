"""Several per-turn notes on one question, with the real model.

Each note was measured alone when it was built: the answer's length, the pinned
reply language and its reminder after the question, looking at the screen, why a
remark was made, what is true right now. Nobody had checked them together.

Measured on real questions (the ones saved with ratings, and the ones reported
in use): 1-3 notes each, 3-4 with a language pinned, adding at most ~870
characters beside a 5,700-character system prompt. The worst stacks below held
6 of 6 when first run. A "why did you say that?" with Turkish pinned first
looked like 2 of 6 -- but the check was a regex, and the model misspells
"tutulma" (eclipse) as "tutum" and "eklips"; read by hand, and with 12 runs, it
was 12 of 12 and 10 of 12. Dropping the note above the question and keeping only
the reminder after it broke the reported pinned-English case (0-1 of 10), so
both stay.
"""

import dataclasses
import re
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, TESTS_DIR, VOICES_DIR  # noqa: F401

sys.path.insert(0, str(TESTS_DIR / "fixtures"))
import make_visual_pages  # noqa: E402

from core.companion import build_companion  # noqa: E402
from core.config import AppConfig  # noqa: E402
from core.logging import setup_logging  # noqa: E402
from modules.voice.language import guess_language  # noqa: E402

setup_logging("ERROR")
failures = 0
RUNS = 6


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


pages = make_visual_pages.make_all(Path(tempfile.mkdtemp(prefix="companion-stacked-e2e-")))
cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False
cfg.vision.enabled = True
cfg.llm.answer_length = "normal"
# Windows' media controls report whatever this PC is really playing, which
# outranks the test's audio lines, and a CJK title fooled the language check.
cfg.perception.now_playing = False
reader = build_companion(cfg, image_path=FIXTURE_IMAGE)
ARTICLE = reader.refresh()
STALE = dataclasses.replace(ARTICLE, image=None)
chart = build_companion(cfg, image_path=str(pages["chart"]))
chart.llm.health_check()

CHART = re.compile(r"visitor|chart|\bbars?\b|tuesday|dashboard|graph|ziyaret|grafik|çubuk|salı", re.I)
#: Eclipse, as the model writes it in Turkish too: tutulma, "tutum", eklips.
REASON = re.compile(r"eclipse|tutu|ekl", re.I)
SONG = re.compile(r"bohemian|don't stop|queen", re.I)
MARKUP = re.compile(r"^\s*(#|[-*] |\d+\. )|\*\*", re.M)
REMARK = ("Those tiny bronze gears are wild — did you know they predicted eclipses?",
          "the article's claim about eclipse prediction is the surprising part")


def english(reply):
    return guess_language(reply) == "en"


def turkish(reply):
    return guess_language(reply) == "tr"


def at_most(words):
    return lambda reply: len(reply.split()) <= words


def audio(lines):
    return SimpleNamespace(recent=lambda minutes, max_chars: lines, stop=lambda: None,
                           capture=SimpleNamespace(stop=lambda: None))


def stack(title, comp, questions, context, pinned, checks, remark=False, answered=None, lines=None):
    print(f"\n{title}")
    comp.reply_language = pinned
    comp.audio = audio(lines) if lines else None
    passed = dict.fromkeys(checks, 0)
    for i in range(RUNS):
        comp.memory.clear()
        if remark:
            comp.memory.add_remark(*REMARK)
        if answered:
            comp.memory.add_turn(*answered)
        question = questions[i % len(questions)]
        reply = comp.ask(question, context=context).text()
        failed = [label for label, test in checks.items() if not test(reply)]
        for label in checks:
            passed[label] += label not in failed
        print(f"    [{len(reply.split())}w{' missed: ' + ', '.join(failed) if failed else ''}] "
              f"{question!r}: {reply[:100]!r}")
    comp.audio = None
    for label, count in passed.items():
        check(f"{label} ({count}/{RUNS})", count >= RUNS - 1)


stack("pinned English + asked to look + a Turkish question + length",
      chart, ["Ekranıma bak, ne görüyorsun?", "ekranımı kontrol et, orada ne var?"], STALE, "en",
      {"in English": english, "from the screenshot": CHART.search, "within ~50 words": at_most(60)})

stack("pinned English + a Turkish reply to a remark + length",
      reader, ["evet", "hayır, pek değil"], ARTICLE, "en",
      {"in English": english, "within ~20 words": at_most(25)}, remark=True)

stack("pinned Turkish + an English 'why did you say that' + length",
      reader, ["why did you say that?", "what made you say that?"], ARTICLE, "tr",
      {"in Turkish": turkish, "gives the remark's reason": REASON.search, "within ~50 words": at_most(60)},
      remark=True)

now = time.time()
stack("pinned Turkish + English audio + a question about now + length",
      reader, ["what song is playing right now?", "şu an hangi şarkı çalıyor?"], ARTICLE, "tr",
      {"in Turkish": turkish, "names the song from the audio": SONG.search, "within ~50 words": at_most(60)},
      lines=[SimpleNamespace(at=now - 15, language="en",
                             text="That was Bohemian Rhapsody by Queen, up next is Don't Stop Me Now."),
             SimpleNamespace(at=now - 200, language="en", text="You're listening to classic rock radio.")])

stack("pinned English + a Turkish request for detail after an answer + length",
      reader, ["daha fazla detay ver", "bunu detaylı anlat"], ARTICLE, "en",
      {"in English": english, "longer: 60-180 words": lambda r: 60 <= len(r.split()) <= 180,
       "plain prose": lambda r: not MARKUP.search(r)},
      answered=("What is this article about?",
                "It's about the Antikythera mechanism, an ancient Greek bronze device that modelled the sky."))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
