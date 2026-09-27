"""Shared moments: replies they gave a 👍, remembered.

Asked for: votes should make the companion feel close; a remembered remark keeps
why it was made, since remarks are voted on for their reason. Moments are read
from the ratings file, chosen in code, and reach:
- an answer only when the question itself relates, or asks what was said before
  -- matched on the page too, 5 ordinary answers in 12 brought the moment up;
- a remark by the page, one moment a session each, told to say something new --
  "without repeating it" alone had 6 remarks in 11 re-ask the liked question.
"""

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.companion import Companion
from core.config import AppConfig, RatingsConfig
from core.logging import setup_logging
from core.memory import ConversationMemory
from core.moments import asks_to_remember, describe, liked_moments, relevant_moments, title_parts
from core.observer import DESCRIBE
from core.orchestrator import Orchestrator
from core.ratings import RatingStore
from core.settings import SETTINGS
from core.types import ScreenContext
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


NOW = datetime(2026, 9, 16, 18, 0)
LIKED_REMARK = "Does the text say how a baker actually knows the starter is ready?"
LIKED_WHY = "The article gives feeding times but never how the baker judges readiness."
RECORDS = [
    {"time": "2026-09-15T17:32:34", "kind": "answer", "rating": "up", "message": "I have been learning about sourdough",
     "page": "Sourdough starter - Wikipedia - Brave", "reply": "That's a deep dive into fermentation."},
    {"time": "2026-09-15T17:31:59", "kind": "remark", "rating": "up", "page": "Sourdough starter - Wikipedia - Brave",
     "reply": LIKED_REMARK, "move": "question", "why": LIKED_WHY, "trigger": "requested"},
    {"time": "2026-09-16T13:31:50", "kind": "remark", "rating": "up", "page": "Orbit Racer",
     "reply": "That last lap cost you the lead, buddy.", "why": "A direct reaction to the race result on screen."},
    {"time": "2026-09-16T13:33:29", "kind": "remark", "rating": "down", "page": "Harbour Lights - MusicBox",
     "reply": "Why switch to mono when you're already listening to solo artists?", "why": "Their library mixes acts."},
    {"time": "2026-09-15T22:32:18", "kind": "answer", "rating": "up", "message": "ekranımı görebiliyor musun",
     "page": "Notes", "reply": "Evet, ekranı görebiliyorum."},
    {"time": "not a time", "kind": "answer", "rating": "up", "message": "x", "reply": "unreadable time"},
]

print("the moments")

moments = liked_moments(RECORDS)
check("every 👍 reply is a moment, newest first; a 👎 and an unreadable time are not",
      [m.reply[:10] for m in moments] == ["That last ", "Evet, ekra", "That's a d", "Does the t"],
      str([m.reply[:10] for m in moments]))
remark = next(m for m in moments if m.kind == "remark" and "sourdough" in m.topic.lower())
check("a remark keeps why it was made, and its page as topic and site",
      remark.why == LIKED_WHY and (remark.topic, remark.site) == ("Sourdough starter", "Wikipedia"))
check("titles split into topic and site, the browser dropped",
      title_parts("Mars rover - NASA - Brave") == ("Mars rover", "NASA") and title_parts("Orbit Racer") == ("", "Orbit Racer"))
check("changing a vote to 👎 forgets the moment",
      len(liked_moments([dict(RECORDS[1], rating="down")])) == 0)

print("\nwhich moments bear on now")


def replies(text, title=""):
    return [m.reply[:10] for m in relevant_moments(moments, text, title)]


check("a question on the same topic brings both sourdough moments",
      sorted(replies("what is a starter?")) == ["Does the t", "That's a d"], str(replies("what is a starter?")))
check("the page's topic does too (for remarks)", len(replies("reading about starters", "Starter timing - Wikipedia - Brave")) == 2)
check("the same app counts: Orbit Racer", replies("who is leading?", "Orbit Racer") == ["That last "])
check("an unrelated question on an unrelated page: none",
      replies("what does this mean?", "Rust ownership - The Rust Book - Brave") == [])
check("everyday sites and Turkish question words connect nothing",
      replies("ne var burada, görebiliyor musun?", "Nature documentaries - YouTube - Brave") == ["Evet, ekra"]
      and replies("what's this video?", "Nature documentaries - YouTube - Brave") == [],
      str(replies("ne var burada, görebiliyor musun?", "Nature documentaries - YouTube - Brave")))
check("the moment sharing the most words comes first",
      replies("how does a baker tell a starter is ready?")[0] == "Does the t")
check("asking what was said before brings the latest ones, at most two",
      replies("do you remember what we talked about?") == ["That last ", "Evet, ekra"]
      and asks_to_remember("ekşi maya hakkında ne konuşmuştuk, hatırlıyor musun?")
      and not asks_to_remember("what happened before the war?"))
line = describe(remark, now=NOW)
check("a remark reads as said by the companion, unasked, with its reason, and when",
      line.startswith("yesterday, on Sourdough starter: YOU said to them, without being asked") and LIKED_WHY in line, line)
answer = next(m for m in moments if "deep dive" in m.reply)
check("an answer reads as their question and the companion's answer",
      "THEY asked \"I have been learning about sourdough\", and YOU answered" in describe(answer, now=NOW))

print("\nin answers")

folder = Path(tempfile.mkdtemp(prefix="companion-moments-"))
store_path = folder / "ratings.jsonl"
store_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n", encoding="utf-8")
cfg = AppConfig.load(CONFIG_PATH)
cfg.ratings.file = str(store_path)
comp = Companion.__new__(Companion)
comp.config, comp.audio, comp.memory = cfg, None, ConversationMemory()
comp.tools = None
SOURDOUGH = ScreenContext(text="A sourdough starter is flour and water full of wild yeast. " * 30,
                      window_title="Sourdough starter - Wikipedia - Brave", app_name="brave.exe", source="uia")


def turn(question, page=SOURDOUGH):
    return comp.build_messages(question, page)[-1].content


cfg.ratings.remember_moments = True
content = turn("How do bakers judge a starter?")
check("a question on a liked topic gets the moments, with the remark's reason",
      "[SHARED MOMENTS" in content and LIKED_REMARK in content and LIKED_WHY in content)
check("...told to touch on one only in a short clause", "short clause" in content)
check("an ordinary question on that same page gets none (measured: 5 in 12 brought it up)",
      "[SHARED MOMENTS" not in turn("What is this article about?"))
remember = turn("do you remember what we talked about?", ScreenContext(
    text="Google", window_title="Google - Brave", app_name="brave.exe", source="uia"))
check("asked what was said before, it answers from them alone, not the screen",
      "[SHARED MOMENTS" in remember and "answer from these alone" in remember and "last lap" in remember)
cfg.ratings.remember_moments = False
check("switched off, nothing", "[SHARED MOMENTS" not in turn("How do bakers judge a starter?"))
cfg.ratings.remember_moments, cfg.ratings.enabled = True, False
check("ratings off, nothing", "[SHARED MOMENTS" not in turn("How do bakers judge a starter?"))
cfg.ratings.enabled = True
RatingStore(store_path).path.write_text("", encoding="utf-8")
check("a ratings reset forgets them all", "[SHARED MOMENTS" not in turn("How do bakers judge a starter?"))
store_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in RECORDS) + "\n", encoding="utf-8")

print("\nin remarks")

worker = SimpleNamespace(_config=cfg)
page = SimpleNamespace(context=SOURDOUGH, activity=SimpleNamespace(summary="reading about sourdough starters"))
offered = CompanionWorker._shared_moments(worker, page)
check("the worker hands over the page's moments, as (line, what was said), a liked remark first",
      len(offered) == 2 and offered[0][1] == LIKED_REMARK and LIKED_WHY in offered[0][0], str(offered))
cfg.ratings.remember_moments = False
check("...and none when switched off", CompanionWorker._shared_moments(worker, page) == [])
cfg.ratings.remember_moments = True


class FakeLLM:
    def __init__(self, say):
        self.say, self.prompts = say, []

    def chat(self, messages, images=None, tools=None, stream=False, collect_tool_calls=None,
             temperature=None, json_schema=None):
        if messages[0].content == DESCRIBE:
            yield "reading about Sourdough starter modes"
        else:
            self.prompts.append(messages[-1].content)
            yield json.dumps({"say": self.say, "why": "the feeding schedule is the interesting part"})


def remark_with(say, shared):
    llm = FakeLLM(say)
    orch = Orchestrator(llm, AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100),
                        min_time_on_page_s=0, max_remarks_per_page=5, shared=shared)
    orch.observe(SOURDOUGH)
    return orch, llm, orch.poll()


orch, llm, made = remark_with("Twice-a-day feeding is what most people get wrong.", lambda c: offered)
check("a remark gets one moment, told to say something new",
      made is not None and llm.prompts and offered[0][0] in llm.prompts[0] and "say something NEW" in llm.prompts[0]
      and offered[1][0] not in llm.prompts[0])
orch._attempt(orch.current, orch.clock())
check("...and each moment is offered once a session: the next remark gets the other",
      len(llm.prompts) == 2 and offered[0][0] not in llm.prompts[1] and offered[1][0] in llm.prompts[1],
      str(len(llm.prompts)))
_, _, replayed = remark_with("Does the text say how a baker actually knows the starter is ready?",
                             lambda c: offered)
check("a remark repeating the liked one is dropped", replayed is None)
_, llm, plain = remark_with("Twice-a-day feeding is what most people get wrong.", None)
check("without moments, the prompt has none", plain is not None and "Something from before" not in llm.prompts[0])

print("\nthe setting")
check("off in code, on in config.yaml, on the settings page",
      RatingsConfig().remember_moments is False and AppConfig.load(CONFIG_PATH).ratings.remember_moments is True
      and any(s.key == "ratings.remember_moments" and s.kind == "bool" for s in SETTINGS))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
