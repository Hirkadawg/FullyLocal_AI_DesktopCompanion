"""Liked moments matched by meaning, with the real EmbeddingGemma.

Needs `ollama pull embeddinggemma` (621 MB). Measured on liked moments collected in use: shared words found 3 of 12 and 7 of 9, meaning at 0.30 found 11
of 12 and 9 of 9, with 1 and 0 wrong moments brought in. Here the moments are
fixed examples, so the suite doesn't depend on anyone's votes.
"""

import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_embedder
from core.config import AppConfig
from core.logging import setup_logging
from core.moments import document, liked_moments, relevant_moments

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


RECORDS = [
    {"time": "2026-09-15T17:31:59", "kind": "remark", "rating": "up", "page": "Sourdough starter - Wikipedia - Brave",
     "reply": "Does the text say how a baker actually knows the starter is ready?",
     "why": "The article gives feeding times but never how the baker judges readiness."},
    {"time": "2026-09-14T23:46:06", "kind": "answer", "rating": "up", "page": "Google - Brave",
     "message": "physics behind it.", "reply": "Rayleigh scattering happens because air molecules scatter shorter blue "
                                               "wavelengths of sunlight much more than red ones."},
    {"time": "2026-09-16T13:31:50", "kind": "remark", "rating": "up", "page": "Orbit Racer",
     "reply": "That last lap cost you the lead, buddy.", "why": "A direct reaction to the race result on screen."},
    {"time": "2026-09-16T13:27:04", "kind": "remark", "rating": "up", "page": "Orbit Racer",
     "reply": "That paint job is lovely, but the rest of the grid looks thrown together.",
     "why": "It comments on the visual oddity without giving advice."},
    {"time": "2026-09-15T16:20:04", "kind": "remark", "rating": "up",
     "page": "Piper voices - GitHub - Brave",
     "reply": "The .onnx.json file basically holds the voice's personality and settings.",
     "why": "It clarifies the difference between the model and its configuration."},
    {"time": "2026-09-14T15:43:02", "kind": "answer", "rating": "up", "page": "Notes",
     "message": "please answer much more shortly", "reply": "Done. Shorter from now on."},
]
moments = liked_moments(RECORDS)


def tag(moment):
    text = f"{moment.topic} {moment.site} {moment.reply} {moment.message}".lower()
    for name in ("sourdough", "rayleigh", "orbit", "onnx"):
        if name in text:
            return name
    return "other"


CASES = [  # (message, page title, the moments that bear on it)
    ("Ekşi mayanın hazır olduğunu nasıl anlarız?", "", {"sourdough"}),
    ("what was that starter readiness test again?", "", {"sourdough"}),
    ("gökyüzü neden mavi?", "", {"rayleigh"}),
    ("does light scattering explain red sunsets too?", "", {"rayleigh"}),
    ("did one of the cars have a funny paint job?", "", {"orbit"}),
    ("how do I make a new voice for piper?", "", {"onnx"}),
    ("driving a ranked race", "Orbit Racer", {"orbit"}),
    ("reading about sourdough starters", "Ekşi maya - Vikipedi - Brave", {"sourdough"}),
    ("what's the capital of Japan?", "", set()),
    ("bana bir film öner", "", set()),
    ("how many legs does a spider have?", "", set()),
    ("reading a recipe for lemon butter chicken", "Lemon Butter Chicken - Recipe - Brave", set()),
    ("reading about the French revolution", "French Revolution - Wikipedia - Brave", set()),
]

cfg = AppConfig.load(CONFIG_PATH)
embedder = build_embedder(cfg)
check("the configured model is embeddinggemma, and reachable",
      embedder is not None and embedder.similarities("hello", [document(m) for m in moments]) is not None)

print("\nwhich moments bear on a message")
found = wrong = expected_total = words_found = words_wrong = 0
times = []
for text, title, expected in CASES:
    started = time.perf_counter()
    chosen = relevant_moments(moments, text, title, embedder=embedder)
    times.append(time.perf_counter() - started)
    by_words = relevant_moments(moments, text, title)
    tags, word_tags = [tag(m) for m in chosen], [tag(m) for m in by_words]
    expected_total += bool(expected)
    found += bool(expected) and any(t in expected for t in tags)
    wrong += sum(t not in expected for t in tags)
    words_found += bool(expected) and any(t in expected for t in word_tags)
    words_wrong += sum(t not in expected for t in word_tags)
    print(f"    {text[:45]!r:48} meaning {tags}  words {word_tags}  expected {sorted(expected)}")
check(f"meaning finds what bears on it ({found}/{expected_total}; words {words_found}/{expected_total})",
      found >= expected_total - 1)
check(f"...and brings in hardly anything that doesn't ({wrong} wrong; words {words_wrong})", wrong <= 1)
median = statistics.median(times[1:])
check(f"...quickly, once the moments are embedded ({median * 1000:.0f} ms a message)", median < 0.5)

print("\nwithout the model")
cfg.ratings.moments_embedding_model = "a-model-that-is-not-pulled"
missing = build_embedder(cfg)
check("a model that isn't there: words decide, nothing breaks",
      [tag(m) for m in relevant_moments(moments, "who is leading the race?", embedder=missing)] == ["orbit"])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
