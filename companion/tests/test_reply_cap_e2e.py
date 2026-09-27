"""The reply cap and the prompt log, with the real model.

Measured before: "Count from 1 to 5000, one number per line" ran 91 s and
8,161 tokens, until the context was full. With the 1,024-token cap: 10 s. The
longest real answer measured, a detailed Turkish one on the Detailed setting,
was 532 tokens, so the cap must never cut an answer like that.
"""

import sys
import tempfile
import time
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import modules.llm.ollama_client as client_module
from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from core.types import Message

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


calls = []
finished = client_module.OllamaLLM._finished


def spy(self, kwargs, reply, tool_calls, stats):
    calls.append(stats)
    return finished(self, kwargs, reply, tool_calls, stats)


client_module.OllamaLLM._finished = spy

cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = cfg.vision.enabled = cfg.perception.now_playing = False
cfg.llm.answer_length = "detailed"
cfg.llm.log_prompts = True
cfg.llm.prompt_log_folder = tempfile.mkdtemp(prefix="companion-prompts-e2e-")
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
page = comp.refresh()

print("a reply that runs on")
started = time.perf_counter()
# Straight to the model: inside the companion's message, with an article on
# screen and a length asked for, it answered about the article instead.
reply = "".join(comp.llm.chat([Message(role="user", content="Count from 1 to 5000, one number per line, "
                                                           "no commentary.")], stream=True))
took = time.perf_counter() - started
stats = calls[-1]
check(f"stopped at the cap ({stats.get('eval_count')} tokens, {took:.0f} s; before 8,161 tokens, 91 s)",
      stats.get("done_reason") == "length" and stats.get("eval_count", 0) <= cfg.ollama.max_reply_tokens
      and took < 40)
check("...and reads as cut", reply.rstrip().endswith("…"), reply[-40:])

print("\ndetailed answers stay whole")
longest = 0
for question in ["Mekanizmanın nasıl çalıştığını adım adım, ayrıntılı açıkla.",
                 "Tell me everything about how the mechanism worked, in depth."]:
    comp.memory.clear()
    reply = comp.ask(question, context=page).text()
    stats = calls[-1]
    longest = max(longest, stats.get("eval_count") or 0)
    print(f"    {stats.get('eval_count')} tokens, {len(reply.split())} words, {stats.get('done_reason')}")
    check(f"{question[:30]!r}... ended by itself", stats.get("done_reason") == "stop" and not reply.endswith("…"))
check(f"...well under the cap (longest {longest}; measured 532)", longest < cfg.ollama.max_reply_tokens * 0.75)

print("\nwhat was sent, saved")
files = list(Path(cfg.llm.prompt_log_folder).glob("prompts-*.txt"))
saved = files[0].read_text(encoding="utf-8") if files else ""
check("a file for today, holding the system prompt, the wrapped question and the replies",
      len(files) == 1 and "You are a desktop companion" in saved and "[SCREEN TEXT]" in saved
      and "[LENGTH" in saved and "Question: Tell me everything" in saved
      and "Count from 1 to 5000" in saved and "HIT THE LENGTH CAP" in saved
      and saved.count("the reply ended") >= 2, str(files))
check("...with real token counts", "Tokens: prompt " in saved and "Tokens: prompt ?" not in saved)

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
