"""Understanding video in a language other than English.

Generates genuine Turkish speech with a Turkish Piper voice, then checks both
routes: Whisper's direct translate task, and transcribe-then-let-the-model
translate.
"""

import sys
import time
from pathlib import Path

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.config import AppConfig
from core.logging import setup_logging
from modules.voice.stt.whisper_stt import FasterWhisperSTT, STTUnavailable
from modules.voice.tts.piper_tts import PiperTTS

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)

TURKISH_VOICE = "tr_TR-dfki-medium"
# "The Antikythera mechanism is an ancient Greek computer. It was used to
#  predict the positions of the stars."
TURKISH = (
    "Antikythera mekanizması eski bir Yunan bilgisayarıdır. "
    "Yıldızların konumlarını tahmin etmek için kullanılmıştır."
)

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


print("misconfiguration is refused loudly")

try:
    FasterWhisperSTT(model="base.en", task="translate")
    check("an English-only model refuses to translate", False)
except STTUnavailable as exc:
    check("an English-only model refuses to translate", True)
    check("the message says how to fix it", "drop the '.en'" in str(exc), str(exc)[:70])

try:
    FasterWhisperSTT(model="base", task="nonsense")
    check("an unknown task is refused", False)
except STTUnavailable:
    check("an unknown task is refused", True)

print("\ngenerating Turkish speech")

voice_path = Path(VOICES_DIR) / f"{TURKISH_VOICE}.onnx"
if not voice_path.is_file():
    print(f"    downloading {TURKISH_VOICE}…")
    from piper.download_voices import download_voice

    download_voice(TURKISH_VOICE, Path(VOICES_DIR))

tts = PiperTTS(voice=TURKISH_VOICE, voices_dir=VOICES_DIR)
pcm = tts.synthesize(TURKISH)
rate = tts.sample_rate
tts.close()

duration = len(pcm) / rate
x_old = np.linspace(0, duration, len(pcm), endpoint=False)
x_new = np.linspace(0, duration, int(duration * 16000), endpoint=False)
audio = (np.interp(x_new, x_old, pcm.astype(np.float32)) / 32768.0).astype(np.float32)
print(f"    {duration:.1f}s of Turkish")

print("\nroute 1: Whisper's own translate task")

# Quality is deliberately NOT asserted here. Measured on this exact audio:
#   base  translate -> returned Turkish unchanged, 0/6 key terms
#   small translate -> "an ancient Greek knowledge ... guess the subjects of
#                       the stars", 3/6
# Whisper's translate task is weak at these sizes, which is why route 2 is the
# default. What is asserted is that the mechanism works: the task is accepted
# and the language is detected.
translator = FasterWhisperSTT(model="base", language=None, task="translate",
                              min_seconds=0.3)
started = time.perf_counter()
english = translator.transcribe(audio)
elapsed = (time.perf_counter() - started) * 1000
print(f"    ({elapsed:.0f} ms) {english}")

check("the translate task runs and returns text", len(english) > 10, english[:60])
check("language was auto-detected as Turkish",
      translator.last_language == "tr", f"detected {translator.last_language!r}")
translator.close()

print("\nroute 2: transcribe in Turkish, then the local model translates")

native = FasterWhisperSTT(model="base", language=None, task="transcribe",
                          min_seconds=0.3)
original = native.transcribe(audio)
print(f"    heard: {original}")
# Checked by character set, not vocabulary: Whisper base's Turkish is rough
# enough that "mekanizması" can come back as "teram ekanizmese", so asserting
# on words tests the ASR's spelling rather than the point, which is that the
# original language was kept instead of being turned into English.
turkish_letters = set("ıİğĞşŞçÇöÖüÜ")
check("original Turkish is preserved, not translated",
      bool(turkish_letters & set(original)),
      f"Turkish characters present: {sorted(turkish_letters & set(original))}")
check("detected as Turkish", native.last_language == "tr",
      f"{native.last_language!r}")
native.close()

from core.companion import build_companion

comp = build_companion(cfg, image_path=FIXTURE_IMAGE)
comp.llm.health_check()

answer = "".join(comp.ask(
    f"This was said in a video: '{original}'. What does it mean in English?"
).chunks).strip()
print(f"    model says: {answer[:150]}")
lowered = answer.lower()
# A generous vocabulary: the point is that it produced an English rendering of
# the meaning, not that it chose particular words. Asserting on a narrow list
# makes the test fail on paraphrase rather than on regression.
english_meaning = ("greek", "computer", "ancient", "star", "position", "predict",
                   "astronomical", "device", "machine", "calculat", "antikythera")
hits = [w for w in english_meaning if w in lowered]
check("the local model translates it", len(hits) >= 2, f"matched {hits}")

# The reason this route exists at all: Whisper's translate task can only ever
# output English, so a third language is impossible through it.
#
# Attempted three times rather than once, and the success count is reported.
# An 8B model asked to go Turkish -> German is genuinely unreliable: it
# sometimes echoes the source untranslated. Measuring that is more useful than
# a single sample that passes or fails by luck. English is reliable; a third
# language is not, and the output below says so plainly.
#
# Markers that appear ONLY in German: "antik" would be a false positive, since
# it is also Turkish and occurs in the source, so an untranslated echo once
# passed this check.
german_only = ("griechisch", "sterne", "mechanismus", "wurde", "verwendet",
               "rechner", "positionen", "vorherzusagen", "antiker", "antikes",
               "alte", "eine", "analogrechen")
#
# Two ways of asking, because they behave differently. Cold ("translate this
# Turkish into German") tends to echo the source. Conversational -- translate
# to English first, then "now in German" -- gives the model an English pivot to
# work from, which is also how a person would actually ask.
attempts, successes, warm_successes = 3, 0, 0
for attempt in range(attempts):
    comp.memory.clear()
    answer = "".join(comp.ask(
        f"Translate the following Turkish sentence into German. "
        f"Reply with the German translation only.\n\n{original}"
    ).chunks).strip()
    matched = [w for w in german_only if w in answer.lower()]
    successes += bool(matched)
    print(f"    cold attempt {attempt + 1}: "
          f"{'ok' if matched else 'ECHOED SOURCE'}  {answer[:78]}")

for attempt in range(attempts):
    comp.memory.clear()
    comp.ask(f"What does this Turkish sentence mean in English? {original}").text()
    answer = "".join(comp.ask("Now say that in German.").chunks).strip()
    matched = [w for w in german_only if w in answer.lower()]
    warm_successes += bool(matched)
    print(f"    via English {attempt + 1}: "
          f"{'ok' if matched else 'ECHOED SOURCE'}  {answer[:78]}")

# Reported, NOT asserted. Measured across runs, qwen3:8b produces German from
# Turkish roughly one attempt in three, echoing the source the rest of the
# time. Asserting it would make this suite fail at random and, worse, would
# imply a reliability the model does not have. Turkish -> English is solid;
# Turkish -> German is a coin toss weighted against you.
print(f"\n    Turkish -> German, asked cold      : {successes}/{attempts}")
print(f"    Turkish -> German, via English     : {warm_successes}/{attempts}")
print("    (Turkish->English is reliable and IS asserted above. A third")
print("     language is not dependable enough at 8B to assert, so it is")
print("     measured and reported instead.)")

comp.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
