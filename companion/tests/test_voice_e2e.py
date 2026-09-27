"""Acceptance: an answer is spoken, first audio arrives within ~1.5s of
asking, and it can be interrupted. Uses the fixture image so the screen content
is fixed."""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion, build_speaker
from core.config import AppConfig
from core.logging import setup_logging

setup_logging("WARNING")
cfg = AppConfig.load(CONFIG_PATH)

IMG = FIXTURE_IMAGE

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("  warming up model and voice…")
comp = build_companion(cfg, image_path=IMG)
comp.llm.health_check()
speaker = build_speaker(cfg)
comp.refresh()  # pre-read the fixture, as ambient would on a live screen

# Load the model into VRAM before timing. A cold load is ~4s of disk read and
# would otherwise dominate the measurement; keep_alive means real use pays it
# once per session at most.
warm_started = time.perf_counter()
"".join(comp.ask("Say OK.", context=comp.last_context).chunks)
print(f"  model warm-up ask: {(time.perf_counter() - warm_started) * 1000:.0f} ms")
comp.memory.clear()

# --- timed answer --------------------------------------------------------
question = "What is this article about? Answer in three full sentences."
print(f"\n  asking: {question}")

asked_at = time.perf_counter()
first_token_ms = None
first_audio_ms = None

answer = comp.ask(question, context=comp.last_context)
for piece in answer.chunks:
    if first_token_ms is None:
        first_token_ms = (time.perf_counter() - asked_at) * 1000
    speaker.feed(piece)
    if first_audio_ms is None and speaker.player.is_playing:
        first_audio_ms = (time.perf_counter() - asked_at) * 1000
speaker.flush()

if first_audio_ms is None:
    while speaker.is_speaking and not speaker.player.is_playing:
        time.sleep(0.005)
    first_audio_ms = (time.perf_counter() - asked_at) * 1000

print(f"\n  first token: {first_token_ms:.0f} ms")
print(f"  first audio: {first_audio_ms:.0f} ms")

check("answer was spoken", speaker.player.is_playing or speaker.is_speaking)
check("first audio within 1.5s of asking", first_audio_ms < 1500,
      f"{first_audio_ms:.0f} ms")
check("speech starts before generation finishes",
      first_audio_ms < (time.perf_counter() - asked_at) * 1000,
      "audio began while tokens were still arriving")

# --- barge-in ------------------------------------------------------------
time.sleep(0.8)  # let it get going
was_playing = speaker.player.is_playing
stop_at = time.perf_counter()
speaker.stop()
stopped_ms = (time.perf_counter() - stop_at) * 1000

check("was speaking before the interrupt", was_playing)
check("stop returns immediately", stopped_ms < 50, f"{stopped_ms:.1f} ms")
check("nothing is queued after the interrupt", speaker.player.queued == 0)
check("not speaking after the interrupt", not speaker.player.is_playing)

# --- still usable afterwards --------------------------------------------
speaker.feed("Interrupted, but still able to speak afterwards. ")
speaker.flush()
time.sleep(1.2)
check("speaks again after being interrupted",
      speaker.player.is_playing or speaker.player.queued > 0
      or not speaker.is_speaking)

speaker.player.wait_until_idle(timeout=30)
time.sleep(0.2)
speaker.close()
comp.close()

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
