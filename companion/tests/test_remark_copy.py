"""A remark that reads the page back is dropped.

Remarks were compared three ways on six pages -- the remark before its reason
(as built), the reason first, and thinking on -- and scored blind. Thinking ran
to its 4,096-token limit and made 1 remark in 24, at 42 s each. The reason first
scored 0.87 against 0.74 (0-2) on 23 remarks each, within chance, with more of
the page's words (46% against 39%). So neither was adopted.

What the blind reading did find: two remarks were a YouTube comment on the page,
word for word. A run of 6 or more words from the page caught 3 remarks, all
scored bad; at 5 it caught a good one too.
"""

import json
import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.attention import AttentionPolicy
from core.logging import setup_logging
from core.observer import DESCRIBE
from core.orchestrator import MAX_COPIED_RUN, Orchestrator, copied_run
from core.types import ScreenContext

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


COMMENT = "It's wild that the people who wrote the original code had to be called out of retirement to help."
PAGE = ScreenContext(text=(
    "How Voyager 1 is still talking to us after 47 years\n"
    "Voyager 1 is more than 24 billion kilometres away, and a single command takes almost 23 hours to reach it.\n"
    "The survey only counted shops that chose to respond, and ignored businesses that rely on parking.\n"
    f"Comments\n{COMMENT}\nImagine debugging something where every test takes two days to come back.\n") * 3,
    window_title="How Voyager 1 is still talking to us - YouTube - Brave", app_name="brave.exe", source="uia")

print("how much of the page a remark reads back")
check("a comment word for word: all of it", copied_run(COMMENT, PAGE.text) == 20, str(copied_run(COMMENT, PAGE.text)))
check("case and punctuation don't hide a copy",
      copied_run("IT'S WILD — that the people who wrote the original code!", PAGE.text) >= MAX_COPIED_RUN)
check("a remark in its own words: short runs only",
      copied_run("Would the retired engineers have even known about that memory flaw?", PAGE.text) < MAX_COPIED_RUN)
check("the good remark a run of 5 would have dropped is kept",
      copied_run("Funny that the survey only counted the shops that wanted to be counted.",
                 "Opponents argued that the survey only counted shops that chose to respond.") < MAX_COPIED_RUN)
check("Turkish words count as words", copied_run("bugün çok güzel bir gün geçirdik bence",
                                                "dün değil bugün çok güzel bir gün geçirdik") == 6)
check("nothing to compare: 0", copied_run("", PAGE.text) == 0 and copied_run("Hello there", "") == 0)


class FakeLLM:
    def __init__(self, say):
        self.say = say

    def chat(self, messages, images=None, tools=None, stream=False, collect_tool_calls=None,
             temperature=None, json_schema=None):
        if messages[0].content == DESCRIBE:
            yield "watching a video about Voyager 1"
        else:
            yield json.dumps({"say": self.say, "why": "the comment says something striking about the engineers"})


def remark(say):
    orch = Orchestrator(FakeLLM(say), AttentionPolicy(cooldown_s=0, quiet_after_user_s=0, min_chars=100),
                        min_time_on_page_s=0)
    orch.observe(PAGE)
    return orch.poll()


print("\nin the orchestrator")
check("a remark that is a comment on the page is not made", remark(COMMENT) is None)
kept = remark("Would the retired engineers have even known about that specific memory flaw?")
check("a remark in its own words is", kept is not None and "memory flaw" in kept.text)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
