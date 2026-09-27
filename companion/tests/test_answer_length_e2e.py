"""Answer length with the real model, judged over batches.

Thresholds come from the measurement that chose the notes (qwen3.5:4b, test
screenshot): replies to a question-remark came within ~20 words 12 of 12 times,
questions within ~60 words 25 of 28, and requests for detail ran 96-242 words.
The short and detailed settings are held to being shorter and longer than
normal on the same questions.
"""

import re
import statistics
import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = False
cfg.llm.answer_length = "normal"
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
context = comp.refresh()
REMARK = "That looks intense — did you find anything interesting?"


def words(message, after_remark=False):
    comp.memory.clear()
    if after_remark:
        comp.memory.add_remark(REMARK, reason="curious")
    reply = comp.ask(message, context=context).text().strip()
    return len(reply.split()), reply


print("replies to a remark")
short_replies, total = 0, 0
for message in ("Yes.", "Not really.", "Evet."):
    for _ in range(4):
        n, reply = words(message, after_remark=True)
        short_replies += n <= 24
        total += 1
    print(f"    {message!r}: e.g. {n} words, {reply[:80]!r}")
check(f"a reply to its question gets a short reply ({short_replies}/{total} within ~20 words)",
      short_replies >= total - 2)

print("\nquestions")
QUESTIONS = ("Why is the sky blue?", "How does a refrigerator work?", "Bitkiler neden yeşil?",
             "What is this article about?", "Who made the Antikythera mechanism?")
normal = []
for question in QUESTIONS:
    for _ in range(3):
        normal.append(words(question)[0])
within = sum(n <= 60 for n in normal)
print(f"    words {normal}")
check(f"a question gets two or three short sentences ({within}/{len(normal)} within ~50 words)",
      within >= len(normal) - 3)

print("\nasking for more, after an answer")
# Reported 15 Sep: "more details" got its last answer back in one sentence, and
# "more detailed answer please" (on Detailed) ~450 words under Markdown headings.
# Measured before the fix: 16-21 words, 60-73% of their words from the first
# answer. After: 75-124 words, 11-18%; detail requests 106-152 words on Normal,
# 186-202 on Detailed, none with headings, lists or bold.
MARKDOWN = re.compile(r"^\s*(#{1,6}\s|[-*]\s|\d+\.\s)|\*\*", re.M)


def content_words(text):
    return set(re.findall(r"[a-zçğıöşü]{4,}", text.lower()))


def follow_up(first_question, second, setting="normal"):
    cfg.llm.answer_length = setting
    comp.memory.clear()
    first = comp.ask(first_question, context=context).text().strip()
    more = comp.ask(second, context=context).text().strip()
    new = content_words(more)
    repeated = len(new & content_words(first)) / max(1, len(new))
    return len(more.split()), repeated, bool(MARKDOWN.search(more))


more = [follow_up("What is this article about?", "more details") for _ in range(4)]
print(f"    'more details' (words, repeated share, markdown): {[(n, round(r, 2), m) for n, r, m in more]}")
check("'more details' adds to the answer instead of repeating it (60+ words, at most 40% repeated)",
      sum(n >= 60 and r <= 0.4 for n, r, _ in more) >= 3)
detail = [follow_up("What is the importance of this article?", "more detailed answer please") for _ in range(4)]
print(f"    'more detailed answer please' on Normal: {[(n, round(r, 2), m) for n, r, m in detail]}")
check("a request for detail has a size on Normal: 60-180 words, no headings, lists or bold",
      sum(60 <= n <= 180 and not m for n, _, m in detail) >= 3)
detailed = [follow_up("What is the importance of this article?", "more detailed answer please", "detailed")
            for _ in range(2)]
print(f"    ...on Detailed: {[(n, round(r, 2), m) for n, r, m in detailed]}")
check("...and on Detailed: longer, but within 300 words and plain prose",
      all(n <= 300 and not m for n, _, m in detailed) and min(n for n, _, _ in detailed) > max(n for n, _, _ in detail) * 0.8)
cfg.llm.answer_length = "normal"

print("\nthe other settings")
by_setting = {"normal": statistics.mean(normal)}
for length in ("short", "detailed"):
    cfg.llm.answer_length = length
    sample = [words(question)[0] for question in QUESTIONS[:4] for _ in range(2)]
    by_setting[length] = statistics.mean(sample)
    print(f"    {length}: words {sample}")
cfg.llm.answer_length = "normal"
check("short answers are shorter than normal, detailed ones longer",
      by_setting["short"] < by_setting["normal"] < by_setting["detailed"],
      ", ".join(f"{k} {v:.0f}" for k, v in by_setting.items()))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
