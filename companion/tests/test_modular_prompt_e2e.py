"""Only the system prompt's sections a message needs, with the real model.

Measured on this battery, whole prompt -> by need: a greeting's prompt 1,433 ->
307 tokens, a screen question's 1,675 -> 844; what isn't on the screen said to
be missing 4 -> 6 of 6; tools called 6 and 6; replies to a remark 7 and 7 of 8;
plain prose 4 and 4; everyday chat 6 and 6. Each section's behaviour is checked
here with its section sent by need.
"""

import re
import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import core.companion as companion_module
import modules.llm.ollama_client as client_module
from core.config import AppConfig
from core.logging import setup_logging
from modules.voice.language import guess_language

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


stats, tool_runs = [], []
finished = client_module.OllamaLLM._finished
client_module.OllamaLLM._finished = lambda self, k, r, c, s: (stats.append(s), finished(self, k, r, c, s))
run_tool = companion_module.Companion._run_tool
companion_module.Companion._run_tool = lambda self, call, q: (tool_runs.append(call.name), run_tool(self, call, q))[1]

cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = cfg.perception.now_playing = False
cfg.ratings.remember_moments = False
comp = companion_module.build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
ARTICLE = comp.refresh()
REMARK = ("Those tiny bronze gears are wild — did you know they predicted eclipses?", "the gears are surprising")


def ask(question, remark=False):
    comp.memory.clear()
    if remark:
        comp.memory.add_remark(*REMARK)
    return comp.ask(question, context=ARTICLE).text()


print("smaller prompts")
ask("how are you?")
greeting = stats[-1].get("prompt_eval_count") or 0
ask("When was it found?")
screen = stats[-1].get("prompt_eval_count") or 0
check(f"a greeting's prompt is small ({greeting} tokens; whole prompt 1,433)", 0 < greeting < 700)
check(f"a screen question's too ({screen} tokens; whole prompt 1,675)", 0 < screen < 1200)

print("\neach section's behaviour, sent by need")
got = [ask(q) for q in ["Who wrote this article?", "What does the article say the mechanism cost?"] * 3]
# Read by hand, every reply was right with either prompt; "was not written by a
# specific individual" had slipped past a narrower pattern.
missing = sum(bool(re.search(r"doesn't|does not|isn't|is not|not (?:mention|say|state|include|contain|written|name)|"
                             r"no (?:information|mention|author|single|specific)|unknown|collaborative|"
                             r"contributors|community", g, re.I)) for g in got)
check(f"grounding: what isn't on the screen is said to be missing ({missing}/6)", missing >= 5)

tool_runs.clear()
called = 0
for question in ["what time is it?", "what timers are running?", "set a timer for 2 minutes called tea"] * 2:
    before = len(tool_runs)
    ask(question)
    called += len(tool_runs) > before
check(f"tools: tool questions call a tool ({called}/6)", called >= 5)

got = [ask(q, remark=True) for q in ["Yes.", "evet", "not really", "hayır"] * 2]
replies = sum(len(g.split()) <= 25 and not re.search(r"\b(article|orrery|analogue computer|shipwreck|1901|stais)\b",
                                                     g, re.I) for g in got)
check(f"after a remark: replies stay replies ({replies}/8)", replies >= 6)

got = [ask("Explain how the mechanism worked.") for _ in range(4)]
prose = sum(not re.search(r"^\s*(#|[-*] |\d+\. )|\*\*", g, re.M) for g in got)
check(f"the core: explanations in plain prose ({prose}/4)", prose >= 3)

got = [(q, ask(q)) for q in ["tell me a joke", "I'm tired today", "nasılsın?"] * 2]
chat = sum(guess_language(g) in (guess_language(q), None) and len(g.split()) <= 60 for q, g in got)
check(f"the core alone: everyday chat in the right language, short ({chat}/6)", chat >= 5)

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
