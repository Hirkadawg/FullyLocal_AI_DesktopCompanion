"""When the companion has a reason to wait before speaking unprompted.

Almost all of these check that it stays QUIET. That is the point: the feature is
defined by what it refuses to do. The policy only ever says "not now" -- the
orchestrator keeps the page as a standing candidate and asks again, so waiting
never loses the remark (test_orchestrator.py covers that half).
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.logging import setup_logging

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def policy(**kw):
    clock = Clock()
    kw.setdefault("cooldown_s", 0.0)
    kw.setdefault("quiet_after_user_s", 0.0)
    kw.setdefault("min_chars", 400)
    return AttentionPolicy(clock=clock, **kw), clock


PAGE = 1000  # characters on a page with plenty to say about

print("with nothing against it, it may speak")

p, clock = policy()
check("no reason to wait", p.reason_to_wait(text_chars=PAGE) is None,
      str(p.reason_to_wait(text_chars=PAGE)))

print("\nthe reasons to wait")

p, clock = policy()
p.muted = True
check("muted", p.reason_to_wait(text_chars=PAGE) == "muted")

p, clock = policy()
check("busy answering or speaking", p.reason_to_wait(busy=True, text_chars=PAGE) == "busy")

p, clock = policy(quiet_after_user_s=45)
p.note_user_activity()
clock.now += 44
check("the user was just talking", p.reason_to_wait(text_chars=PAGE) == "user was just talking")
clock.now += 2
check("...and not once the quiet period is over", p.reason_to_wait(text_chars=PAGE) is None)

p, clock = policy(cooldown_s=75)
p.note_spoke()
clock.now += 74
check("too soon after the last remark", p.reason_to_wait(text_chars=PAGE) == "cooldown")
clock.now += 2
check("...and not once the cooldown has passed", p.reason_to_wait(text_chars=PAGE) is None)

p, clock = policy(max_per_hour=2)
for _ in range(2):
    p.note_spoke()
    clock.now += 60
check("the hourly budget is spent", p.reason_to_wait(text_chars=PAGE) == "hourly budget spent")
check("budget counted correctly", p.spoken_last_hour == 2)
clock.now += 3600
check("...and it refills after an hour", p.reason_to_wait(text_chars=PAGE) is None)

p, clock = policy()
check("too little on screen to have an opinion about",
      p.reason_to_wait(text_chars=len("Save   Cancel   File   Edit")) == "not enough on screen")
check("text length is only checked when given", p.reason_to_wait() is None)

print("\nthe most useful reason comes first")

p, clock = policy(cooldown_s=75)
p.note_spoke()
p.muted = True
check("muted outranks the cooldown", p.reason_to_wait(text_chars=PAGE) == "muted")

print("\nasking has no side effects")

# Asked every tick while a candidate waits, so anything stateful in here would
# run once a second.
p, clock = policy(cooldown_s=75)
p.note_spoke()
for _ in range(100):
    p.reason_to_wait(text_chars=PAGE)
check("a hundred questions don't change the answer or the counts",
      p.reason_to_wait(text_chars=PAGE) == "cooldown" and p.spoken_last_hour == 1)

print("\ntwo policies do not share state")

a, _ = policy(cooldown_s=75)
b, _ = policy(cooldown_s=75)
a.note_spoke()
check("one speaking doesn't put the other in cooldown",
      b.reason_to_wait(text_chars=PAGE) is None)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
