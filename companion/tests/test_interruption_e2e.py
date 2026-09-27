"""Interrupted mid-reply, "go on" has to pick up where the speech stopped.

Measured before the INTERRUPTED note existed, on this fixture: the companion
had written three sleep tips, but was stopped aloud one sentence in. Asked "go
on" three times, it resumed the tips 0 times out of 3 -- it didn't even stay
on the subject, and went on about the article on screen instead, because as
far as it knew it had already finished. With the note: 3 out of 3 resumed at
exactly the sentence that had been cut off.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.types import Delivery

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False  # nothing here is about system audio
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
context = comp.refresh()

# Deliberately unrelated to the article on screen, so resuming can only come
# from the conversation and the note, never from the screen text.
TIPS = (
    "Keep your bedroom cool, around eighteen degrees.",
    "Stop drinking coffee after two in the afternoon.",
    "Put your phone in another room an hour before bed.",
)
# The first tip was heard; speech was cut off partway through the second.
CUT = Delivery(sentences=TIPS, finished=1, partial=True)


def ask_after_interruption(question):
    comp.memory = ConversationMemory()
    comp.memory.add_turn("Give me three tips for sleeping better.", " ".join(TIPS))
    return comp.ask(question, context=context, interrupted=CUT).text().strip()


print("'go on' after being cut off")

for attempt in (1, 2):
    reply = ask_after_interruption("go on")
    print(f"    {reply[:120]}")
    check(f"resumes at the sentence that was cut off (attempt {attempt})",
          "coffee" in reply.lower() or "caffeine" in reply.lower(), reply[:80])

print("\nsomething unrelated after being cut off")

reply = ask_after_interruption("What is this article about?")
print(f"    {reply[:120]}")
lowered = reply.lower()
check("the new question is answered",
      any(w in lowered for w in ("antikythera", "greek", "mechanism")), reply[:80])
mentioned = [w for w in ("interrupt", "cut off", "as i was saying", "where was i")
             if w in lowered]
check("without dwelling on the interruption", not mentioned, str(mentioned))
check("or dragging the old topic back in",
      "sleep" not in lowered and "coffee" not in lowered, reply[:80])

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
