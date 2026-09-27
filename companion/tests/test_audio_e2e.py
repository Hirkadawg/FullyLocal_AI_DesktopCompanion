"""Acceptance: answer from what was HEARD, not what is displayed.

A passage about photosynthesis is played through the speakers while an article
about the Antikythera mechanism is on screen. "What did they just explain?" must
answer about photosynthesis -- there is no other way to know.
"""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_companion
from core.config import AppConfig
from core.logging import setup_logging
from modules.voice.player import AudioPlayer
from modules.voice.tts.piper_tts import PiperTTS

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)
cfg.audio.enabled = True
cfg.audio.model = "tiny.en"      # faster, and enough for gist
cfg.audio.chunk_seconds = 4.0    # shorter so the test doesn't crawl
cfg.voice.enabled = False        # the companion must not talk over the lecture

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


LECTURE = (
    "Today we are talking about photosynthesis. Plants capture light energy "
    "using a green pigment called chlorophyll, which sits inside structures "
    "called chloroplasts. The light reactions split water molecules and release "
    "oxygen as a by-product. The energy captured is then used to build sugars "
    "from carbon dioxide in a cycle named after Melvin Calvin."
)

# The screen shows the Antikythera article; the speakers play a biology lecture.
comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()
comp.refresh()
check("audio transcriber is running", comp.audio is not None)
check("screen shows the article, not the lecture",
      "antikythera" in comp.last_context.text.lower())

print("\n  playing a lecture through the speakers…")
tts = PiperTTS(voice="en_US-lessac-medium", voices_dir=VOICES_DIR)
pcm = tts.synthesize(LECTURE)
player = AudioPlayer(sample_rate=tts.sample_rate)
player.enqueue(pcm)
player.wait_until_idle(timeout=90)
player.close()
tts.close()

# Give the transcriber time to finish the trailing chunk.
deadline = time.time() + 20
while time.time() < deadline and len(comp.audio.transcript(minutes=10)) < 60:
    time.sleep(1.0)

heard = comp.audio.transcript(minutes=10)
print(f"  transcript: {heard[:120]}")
check("the lecture was heard", "photosynth" in heard.lower(), heard[:80])

print("\n  asking what was just explained…")
answer = "".join(
    comp.ask("What did they just explain in the audio?",
             context=comp.last_context).chunks
).strip()
print(f"  said: {answer[:180]}")

lowered = answer.lower()
audio_terms = [w for w in ("photosynth", "chlorophyll", "plant", "oxygen",
                           "chloroplast", "light") if w in lowered]
check("answers from the spoken content", len(audio_terms) >= 2, f"matched {audio_terms}")
first_sentence = lowered.split(". ")[0]
check("does not answer about the on-screen article instead",
      "antikythera" not in first_sentence, first_sentence[:120])
# Reported, NOT asserted. qwen3.5:4b answers from the audio every time, but in 3
# of 8 runs added an aside afterwards: "this contrasts with the Wikipedia
# article about the Antikythera mechanism currently on your screen".
at = lowered.find("antikythera")
if at != -1:
    print(f"  note: also mentioned the article: …{answer[max(0, at - 80):at + 40]}…")

print("\n  and a screen question still works while audio is running…")
answer2 = "".join(
    comp.ask("What is the article on screen about?", context=comp.last_context).chunks
).strip()
print(f"  said: {answer2[:140]}")
check("screen questions still answer from the screen",
      "antikythera" in answer2.lower() or "mechanism" in answer2.lower(),
      answer2[:80])

comp.close()
check("audio stops with the companion",
      comp.audio is None or not comp.audio.capture.running)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
