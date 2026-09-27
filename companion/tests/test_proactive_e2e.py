"""Acceptance: unprompted remarks are short, on topic, varied, and made
only when there is something to say.

Every remark here goes through the Orchestrator with the shipped persona -- the
path the app runs. Remarks are sampled at temperature 0.85, so properties of
their content are judged over a batch: a check on one remark passes only most
of the time, which is worse than no check.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from core.orchestrator import MOVE_ORDER, Orchestrator, load_persona
from core.types import ScreenContext

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)
cfg.proactive.enabled = True
cfg.audio.enabled = False  # nothing here is about system audio

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
context = comp.refresh()
PERSONA = load_persona(cfg.root / cfg.proactive.persona_file)


class Counting:
    """Passes calls through to the real model, counting them."""

    def __init__(self, llm):
        self.llm, self.calls = llm, 0

    def chat(self, *args, **kwargs):
        self.calls += 1
        return self.llm.chat(*args, **kwargs)


def live_remark(page=None, llm=None, kind=0):
    """One remark about a page that is due, through the real orchestrator.

    `kind` picks which kind of remark comes up in the rotation, so a batch can
    cover all of them even though each call builds a fresh orchestrator.
    """
    orch = Orchestrator(
        llm or comp.llm,
        AttentionPolicy(cooldown_s=0, quiet_after_user_s=0,
                        min_chars=cfg.proactive.min_chars),
        max_words=cfg.proactive.max_words,
        temperature=cfg.proactive.temperature,
        min_time_on_page_s=0,
        persona=PERSONA,
    )
    orch._last_tried = MOVE_ORDER[(kind - 1) % len(MOVE_ORDER)]
    orch.observe(page or context)
    return orch.poll()


print("remarks about a real article, judged eight at a time")

KEYWORDS = ("antikythera", "greek", "gear", "mechanism", "orrery", "eclipse",
            "bronze", "astronom", "ancient", "device", "tech", "clock", "machine")
results = [live_remark(kind=i) for i in range(8)]
for r in results:
    print(f"    - [{r.move}] {r.text}   (why: {r.why})" if r else "    - (nothing)")
spoken = [r for r in results if r]
texts = [r.text for r in spoken]

check("it has something to say about an article like this",
      len(spoken) >= 4, f"{len(spoken)} of 8")
check("every remark is short",
      all(len(t.split()) <= cfg.proactive.max_words + 15 for t in texts),
      str([len(t.split()) for t in texts]))
check("none asks more than one question", all(t.count("?") <= 1 for t in texts))
check("none greets or offers help",
      not any(p in t.lower() for t in texts
              for p in ("how can i help", "would you like", "let me know",
                        "hello!", "hi there")))
check("none announces itself",
      not any(p in t.lower() for t in texts
              for p in ("i notice that you", "i see that you are",
                        "it looks like you're looking")))
on_topic = sum(any(k in t.lower() for k in KEYWORDS) for t in texts)
check("at least half name something from the article", on_topic >= 4,
      f"{on_topic} of {len(texts)}")
check("they vary rather than repeating one sentence", len(set(texts)) >= 2,
      f"{len(set(texts))} distinct")
check("they come in more than one kind", len({r.move for r in spoken}) >= 2,
      str(sorted({r.move for r in spoken})))
# The old composer opened with "That's..." in 7 of 15 remarks across six pages,
# and in 7 of 8 on this fixture. The persona teaches by example instead; it
# measured 0 of 15. At most two of eight allows the occasional one.
thats = sum(t.replace("’", "'").lower().startswith(("that's", "that is")) for t in texts)
check("the old 'That's a pretty cool...' template is gone", thats <= 2,
      f"{thats} of {len(texts)} open with it")
examples = [line.strip()[3:-1].lower() for line in PERSONA.splitlines()
            if line.strip().startswith('- "')]
copied = [t for t in texts if any(e[:30] in t.lower() for e in examples)]
check("none copies an example from the persona", not copied and examples, str(copied))

print("\na screen with nothing to say about never reaches the model")

# The model's decline is not reliable -- it once explained what the OK button
# does -- so near-empty screens and interfaces are refused before any call.
blank = ScreenContext(text="File   Edit   View   Help   Save   Cancel   OK",
                      window_title="Untitled", app_name="notepad.exe", source="uia")
counting = Counting(comp.llm)
check("nothing is said about a near-empty window", live_remark(blank, counting) is None)
check("...and the model was never called", counting.calls == 0,
      f"{counting.calls} calls")

print("\ncomposing a remark does not drag in conversation history")

comp.memory.clear()
comp.ask("Remember the word ZEPHYRINE.", context=context).text()
remark = live_remark()
check("a remark is composed without earlier chatter",
      remark is None or "zephyrine" not in remark.text.lower(),
      remark.text[:70] if remark else "")

print("\n...but what it said IS remembered, so a reply is a reply")

# The reported failure: the companion asked "did you find anything
# interesting?", the user said "Yes.", and the answer was the current time.
# Two causes, both fixed -- remarks were never written to memory (add_turn
# stores a PAIR and a remark has no question in front of it), and the system
# prompt framed every user message as a question about the screen.
#
# The probe deliberately names something absent from the article, so the answer
# cannot be reconstructed from the screen. Without the remark in memory the
# model has no way to produce it.
comp.memory.clear()
comp.memory.add_remark(
    "Kind of makes you wonder what one of these would look like built in Lego."
)
reply = comp.ask("What did you just wonder about?", context=context).text().strip()
print(f"    {reply[:120]}\n")
check("it can recall what it said unprompted", "lego" in reply.lower(),
      reply[:90])
check("...and does not answer with the article instead",
      "lego" in reply.lower() and len(reply.split()) < 90,
      f"{len(reply.split())} words")

comp.memory.clear()
comp.memory.add_remark("That looks intense — did you find anything interesting?")
short = comp.ask("Yes.", context=context).text().strip()
print(f"    {short[:120]}\n")
# Length is the honest signal here, and the one that actually failed: the bug
# produced "The Antikythera mechanism is an ancient Greek device used to
# predict astronomical positions and eclipses, and track athletic cycles..."
# A conversational reply to "yes" runs a handful of words; a summary cannot.
# Checking the opening words instead would have passed on that very output,
# which is how this check was wrong the first time it was written.
check("a bare 'Yes.' gets a reply, not a summary of the page",
      len(short.split()) <= 25, f"{len(short.split())} words: {short[:70]}")

print("\nordinary screen questions are unaffected")

comp.memory.clear()
plain = comp.ask("What is this article about?", context=context).text().strip()
print(f"    {plain[:120]}\n")
check("a real question still gets a real answer",
      any(w in plain.lower() for w in ("antikythera", "greek", "mechanism")),
      plain[:70])
check("...and is not deflected into small talk", len(plain.split()) >= 15,
      f"{len(plain.split())} words")
# The prompt teaches by example ("nice -- what caught you?"), and models copy
# examples. It must not surface where nobody was chatting.
check("the prompt's worked example does not leak into answers",
      "what caught you" not in plain.lower(), plain[:70])

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
