"""Learning how often to speak, per site, kind and moment, in code.

On a fake clock: after repeated Esc on one site, remarks there become rarer
while another site is unchanged; replies bring them back; ignored remarks
change nothing; one negative never silences a site; a kind of remark or a
moment the user keeps stopping is dropped; Quiet, thumbs and asked-for remarks
count; and what was learned survives a restart.
"""

import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.config import AppConfig
from core.learning import Learning
from core.logging import setup_logging
from core.observer import DESCRIBE
from core.orchestrator import Orchestrator, site_of
from core.types import ScreenContext
from modules.ui.app import CompanionApp
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("which site a window belongs to")

for title, site in (("Mars rover finds organic molecules - NASA - Brave", "NASA"),
                    ("Antikythera mechanism - Wikipedia", "Wikipedia"),
                    ("(3) Inbox - someone@example.com - Gmail - Google Chrome", "Gmail"),
                    ("Settings", "Settings"), ("", "(untitled)")):
    check(f"{title!r} is {site}", site_of(title) == site, site_of(title))


def _word(n):
    """A made-up word, different for every n, all letters."""
    return "".join(chr(97 + (n // 26 ** k) % 26) for k in range(4)) + "qz"


class FakeLLM:
    """Always something new to say -- no two remarks share a word -- so only
    timing and learning decide whether a remark is made."""

    def __init__(self):
        self.made = 0

    def chat(self, messages, **kwargs):
        if messages[0].content == DESCRIBE:
            yield "reading something"
            return
        words = " ".join(_word(self.made * 8 + i) for i in range(8))
        self.made += 1
        yield json.dumps({"say": f"Honestly {words}.", "why": "because it is worth a word"})


now = [0.0]


def clock():
    return now[0]


ARTICLE = ("The Antikythera mechanism is an Ancient Greek hand-powered orrery, "
           "described as the oldest known example of an analogue computer.\n") * 10


def orchestrator(learning, **kwargs):
    policy = AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100, max_per_hour=100000,
                             clock=clock)
    return Orchestrator(FakeLLM(), policy, min_time_on_page_s=10, clock=clock, learning=learning,
                        outcome_window_s=120, **kwargs)


def visit(orch, title, react=None):
    """Open a page, give it time for a remark, react to it; returns the remark.

    50 s on the page: enough for the longest learned settle (10 s / 0.25).
    """
    now[0] += 30
    orch.observe(ScreenContext(text=ARTICLE, window_title=title, app_name="brave.exe", source="uia"))
    now[0] += 50
    remark = orch.poll()
    if remark is not None and react is not None:
        now[0] += 5
        orch.note_user_message(reply=(react == "reply"))
    return remark


print("\nstopping remarks on one site")

learning = Learning()
orch = orchestrator(learning)
check("before anything is learned, every page is as welcome as configured",
      learning.allowance("YouTube") == 1.0)
dismissed = 0
for i in range(60):
    if visit(orch, f"Clip {i} - YouTube - Brave", react="stop") is not None:
        dismissed += 1
    if dismissed >= 6:
        break
counts = learning.counts["sites"]["YouTube"]
check("each Esc on a remark counts against the site", counts["negative"] == 6, str(counts))
check("...and remarks there are now welcome at the floor, not at zero",
      learning.allowance("YouTube") == 0.25, str(learning.allowance("YouTube")))

youtube = sum(visit(orch, f"Video {i} - YouTube - Brave") is not None for i in range(40))
wikipedia = sum(visit(orch, f"Article {i} - Wikipedia - Brave") is not None for i in range(40))
check("remarks on that site became rarer", youtube <= 20, f"{youtube}/40 pages")
check("...while another site is unchanged", wikipedia == 40, f"{wikipedia}/40 pages")
check("...and never silenced", youtube > 0, f"{youtube}/40 pages")

print("\nreplies bring them back")

replied = 0
for i in range(200):
    if visit(orch, f"Talk {i} - YouTube - Brave", react="reply") is not None:
        replied += 1
    if learning.counts["sites"]["YouTube"]["positive"] >= 8:
        break
check("replies count for the site", learning.allowance("YouTube") > 1.0,
      f"x{learning.allowance('YouTube'):.2f} after {replied} replies")
youtube = sum(visit(orch, f"Lecture {i} - YouTube - Brave") is not None for i in range(20))
check("...and remarks there are back", youtube == 20, f"{youtube}/20 pages")

print("\nwhat counts, and what doesn't")

learning = Learning()
orch = orchestrator(learning)
visit(orch, "Some article - Wikipedia")
now[0] += 121
orch.poll()
entry = learning.counts["sites"]["Wikipedia"]
check("a remark nobody reacts to is logged as ignored", entry["ignored"] == 1, str(entry))
check("...and changes nothing", learning.allowance("Wikipedia") == 1.0)

single = Learning()
single.record("NASA", "opinion", "arrived", "negative")
check("one negative trims a site's allowance, it doesn't silence it",
      0.5 <= single.allowance("NASA") < 1.0, str(single.allowance("NASA")))
for _ in range(50):
    single.record("NASA", "opinion", "arrived", "negative")
check("...and even many can't take it below min_allowance", single.allowance("NASA") == 0.25)
for _ in range(50):
    single.record("Wikipedia", "opinion", "arrived", "positive")
check("...nor can many positives raise it past max_allowance", single.allowance("Wikipedia") == 1.5)

learning = Learning()
orch = orchestrator(learning)
visit(orch, "Paper - arXiv")
now[0] += 30
orch.note_quiet()
check("switching Quiet on right after a remark counts against it",
      learning.counts["sites"]["arXiv"]["negative"] == 1, str(learning.counts["sites"]))
visit(orch, "Other paper - arXiv")
now[0] += 500
orch.note_quiet()
check("...but not long after, when it says nothing about the remark",
      learning.counts["sites"]["arXiv"]["negative"] == 1)

orch.note_rating("Some clip - YouTube - Brave", "question", "media_end", "down")
check("a thumbs down on a remark counts against its site, kind and moment",
      learning.counts["sites"]["YouTube"]["negative"] == 1
      and learning.counts["moves"]["question"]["negative"] == 1
      and learning.counts["moments"]["media_end"]["negative"] == 1)
now[0] += 30
orch.observe(ScreenContext(text=ARTICLE, window_title="Asked about - Wikipedia", app_name="brave.exe"))
remark, _ = orch.remark_now()
check("asking for a remark counts for the site",
      remark is not None and learning.counts["sites"]["Wikipedia"]["positive"] == 1,
      str(learning.counts["sites"].get("Wikipedia")))

print("\nkinds of remark and moments")

learning = Learning()
for _ in range(4):
    learning.record("NASA", "question", "arrived", "negative")
orch = orchestrator(learning)
orch.last_move = "opinion"
check("a kind of remark stopped again and again is dropped: opinions may come twice running",
      orch._next_move() == "opinion")
check("...while one negative on a kind drops nothing",
      Learning().liked("moves", "question") and learning.liked("moves", "opinion"))

learning = Learning()
for _ in range(4):
    learning.record("Wikipedia", "opinion", "leaving", "negative")
orch = orchestrator(learning, natural_moments=True, long_stay_s=180)
now[0] += 30
orch.observe(ScreenContext(text=ARTICLE, window_title="Long read - Wikipedia", app_name="brave.exe"))
now[0] += 400
orch.observe(ScreenContext(text=ARTICLE, window_title="Next - Wikipedia", app_name="brave.exe"))
check("a moment stopped again and again is passed over: no remark on leaving",
      orch.parting is None)

print("\nkept between runs")

path = Path(tempfile.mkdtemp(prefix="companion-learning-")) / "learning.json"
kept = Learning(path)
kept.record("YouTube", "question", "media_end", "negative")
kept.record("YouTube", "opinion", "arrived", "positive")
again = Learning(path)
check("what was learned survives a restart", again.counts == kept.counts, str(again.counts))
path.write_text("{ not json", encoding="utf-8")
check("a damaged file starts afresh rather than failing", Learning(path).allowance("YouTube") == 1.0)

print("\nwhere the signals come from")

cfg = AppConfig.load(CONFIG_PATH)
worker = CompanionWorker(cfg)
quiet, rated = [], []
worker._attention = SimpleNamespace(muted=False)
worker._orchestrator = SimpleNamespace(note_quiet=lambda: quiet.append(1),
                                       note_rating=lambda *a: rated.append(a))
worker.set_muted(True)
check("switching Quiet on tells the orchestrator", quiet == [1])
worker.set_muted(False)
check("...switching it off doesn't", quiet == [1])
fake = SimpleNamespace(worker=worker, ratings=None, config=cfg)
CompanionApp._on_rated(fake, SimpleNamespace(kind="remark", page="Clip - YouTube", move="question",
                                             trigger="arrived"), "down")
CompanionApp._on_rated(fake, SimpleNamespace(kind="answer", page="x", move="", trigger=""), "down")
check("a thumbs down on a remark reaches learning; on an answer it doesn't",
      rated == [("Clip - YouTube", "question", "arrived", "down")], str(rated))
cfg.learning.enabled = False
check("switched off, there is no learning", worker._build_learning() is None)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
