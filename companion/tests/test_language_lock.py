"""Speech input restricted to a shortlist, plus the manual override.

Free auto-detect over 99 languages mishears English as Polish or Turkish on an
ordinary microphone. Choosing between two candidates is a much easier problem,
and pinning one is easier still.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.config import AppConfig
from core.logging import setup_logging
from modules.voice.stt.whisper_stt import FasterWhisperSTT
from modules.voice.tts.piper_tts import PiperTTS

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def to16k(pcm, rate):
    d = len(pcm) / rate
    xo = np.linspace(0, d, len(pcm), endpoint=False)
    xn = np.linspace(0, d, int(d * 16000), endpoint=False)
    return (np.interp(xn, xo, pcm.astype(np.float32)) / 32768.0).astype(np.float32)


print("config restricts speech input but not video listening")

check("speech has a shortlist", cfg.speech.languages == ["en", "tr"],
      f"{cfg.speech.languages}")
check("speech is not pinned by default", cfg.speech.language is None)
check("VIDEO listening still hears any language",
      cfg.audio.language is None and not cfg.audio.model.endswith(".en"),
      f"audio.language={cfg.audio.language!r} model={cfg.audio.model}")

print("\nspoken input is recognised in both shortlisted languages")

clips = []
for voice, phrases in (
    ("en_US-lessac-medium", ["What is this article about?",
                             "Set a timer for twenty minutes."]),
    ("tr_TR-dfki-medium", ["Bu makale ne hakkında?",
                           "Bunu daha basit anlat."]),
):
    tts = PiperTTS(voice=voice, voices_dir=VOICES_DIR)
    want = "en" if voice.startswith("en") else "tr"
    for phrase in phrases:
        clips.append((want, to16k(tts.synthesize(phrase), tts.sample_rate)))
    tts.close()

stt = FasterWhisperSTT(model=cfg.speech.model, device=cfg.speech.device,
                       compute_type=cfg.speech.compute_type,
                       language=cfg.speech.language,
                       languages=cfg.speech.languages, min_seconds=0.3)

correct = 0
for want, audio in clips:
    heard = stt.transcribe(audio)
    got = stt.last_language
    correct += got == want
    print(f"    want {want}, got {got}: {heard[:46]}")
check("both languages recognised from the shortlist", correct == len(clips),
      f"{correct}/{len(clips)}")

print("\npinning a language overrides detection")

stt.set_language("en")
check("pinned language is recorded", stt.language == "en")
_, turkish_audio = clips[-1]
heard = stt.transcribe(turkish_audio)
check("pinned to English, Turkish audio is not detected as Turkish",
      stt.last_language == "en", f"detected {stt.last_language}")

stt.set_language("tr")
_, english_audio = clips[0]
stt.transcribe(english_audio)
check("pinned to Turkish likewise", stt.last_language == "tr",
      f"detected {stt.last_language}")

stt.set_language(None)
check("unpinning returns to the shortlist", stt.language is None)
stt.transcribe(english_audio)
check("...and detection works again", stt.last_language == "en",
      f"detected {stt.last_language}")
stt.close()

print("\na single-entry shortlist needs no detection at all")

one = FasterWhisperSTT(model=cfg.speech.model, languages=["en"], min_seconds=0.3)
check("one language is used directly", one._choose_language(None, None) == "en")
none = FasterWhisperSTT(model=cfg.speech.model, languages=[], min_seconds=0.3)
check("an empty shortlist means detect over everything",
      none._choose_language(None, None) is None)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
