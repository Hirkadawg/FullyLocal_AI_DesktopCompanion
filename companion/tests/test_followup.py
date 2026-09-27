"""Acceptance: a follow-up question must resolve against the previous
answer. Uses the fixture image so the result doesn't depend on the live screen."""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging

setup_logging("WARNING")
cfg = AppConfig.load(CONFIG_PATH)

IMG = FIXTURE_IMAGE

comp = build_companion(cfg, image_path=IMG)
comp.llm.health_check()

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


# 1. Grounded question, then a follow-up that only the previous answer resolves.
#
#    This deliberately asks for RECALL, not manipulation. An earlier version
#    asked the model to spell the surname backwards, which tested character
#    reversal -- something small models are bad at because they see tokens, not
#    letters -- and so failed while memory was working perfectly.
first = "Who identified the gear, and in what year was the wreck found?"
print(f"Q1: {first}")
a1 = "".join(comp.ask(first).chunks).strip()
print(f"A1: {a1}\n")
check("first answer is grounded in the article", "stais" in a1.lower(), a1[:70])

second = "What surname did you just tell me? Answer with the name only."
print(f"Q2: {second}")
a2 = "".join(comp.ask(second).chunks).strip()
print(f"A2: {a2}\n")
check("follow-up resolved from the previous turn", "stais" in a2.lower(), a2[:70])

# 2. A pure memory probe: nothing on screen can supply this answer.
comp.memory.clear()
print("Q3: (asking it to say a nonsense word)")
a3 = "".join(comp.ask("Reply with exactly the word ZEPHYRINE and nothing else.").chunks)
print(f"A3: {a3.strip()}\n")

fourth = "What word did I just ask you to say?"
print(f"Q4: {fourth}")
a4 = "".join(comp.ask(fourth).chunks).strip()
print(f"A4: {a4}\n")
check("a word only in conversation is remembered", "zephyrine" in a4.lower(), a4[:70])
check("memory holds both turns", comp.memory.turns == 2, f"{comp.memory.turns}")

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
