"""Speaking to the companion in another language.

Generates real Turkish commands with a Turkish Piper voice, checks that
auto-detect identifies them, and that the voice used to reply follows the
language that was asked in.
"""

import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.config import AppConfig
from core.logging import setup_logging
from modules.voice.player import AudioPlayer
from modules.voice.speaker import Speaker
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


print("config is set up for more than English")

check("speech model is multilingual", not cfg.speech.model.endswith(".en"),
      f"model={cfg.speech.model}")
check("speech language auto-detects", cfg.speech.language is None,
      f"language={cfg.speech.language!r}")

print("\nrecognising spoken Turkish commands")

TURKISH = [
    "Bu makale ne hakkında?",
    "Yirmi dakikalık bir zamanlayıcı kur.",
    "Bunu daha basit anlat.",
]
ENGLISH = ["What is this article about?", "Set a timer for twenty minutes."]

stt = FasterWhisperSTT(model=cfg.speech.model, device=cfg.speech.device,
                       compute_type=cfg.speech.compute_type,
                       language=cfg.speech.language, min_seconds=0.3,
                       silence_rms=cfg.speech.silence_rms)

tr_tts = PiperTTS(voice="tr_TR-dfki-medium", voices_dir=VOICES_DIR)
detected_tr = 0
for phrase in TURKISH:
    audio = to16k(tr_tts.synthesize(phrase), tr_tts.sample_rate)
    heard = stt.transcribe(audio)
    ok = stt.last_language == "tr"
    detected_tr += ok
    print(f"    [{stt.last_language}] {heard[:52]}")
tr_tts.close()
check("Turkish is auto-detected", detected_tr == len(TURKISH),
      f"{detected_tr}/{len(TURKISH)}")

en_tts = PiperTTS(voice="en_US-lessac-medium", voices_dir=VOICES_DIR)
detected_en = 0
for phrase in ENGLISH:
    audio = to16k(en_tts.synthesize(phrase), en_tts.sample_rate)
    heard = stt.transcribe(audio)
    ok = stt.last_language == "en"
    detected_en += ok
    print(f"    [{stt.last_language}] {heard[:52]}")
en_tts.close()
check("English still auto-detects correctly", detected_en == len(ENGLISH),
      f"{detected_en}/{len(ENGLISH)}")
stt.close()

print("\nthe reply voice follows the language asked in")


class SilentPlayer(AudioPlayer):
    def start(self):
        pass

    def enqueue(self, samples):
        pass


engine = PiperTTS(voice=cfg.voice.voice, voices_dir=VOICES_DIR)
speaker = Speaker(engine, SilentPlayer(), min_sentence_chars=5,
                  voices_by_language=cfg.voice.voices_by_language)

check("starts on the configured voice", engine.voice_name == cfg.voice.voice,
      engine.voice_name)

speaker.set_language("tr")
check("switches to a Turkish voice when asked in Turkish",
      engine.voice_name == cfg.voice.voices_by_language.get("tr"),
      engine.voice_name)

speaker.set_language("en")
check("switches back for English", engine.voice_name ==
      cfg.voice.voices_by_language.get("en"), engine.voice_name)

before = engine.voice_name
speaker.set_language("xx")
check("an unmapped language leaves the voice alone", engine.voice_name == before,
      engine.voice_name)

engine.default_voice = cfg.voice.voice
ok = engine.set_voice("de_DE-thorsten-medium")
check("a voice that isn't installed is refused, not crashed", ok is False)
check("...and the previous voice is kept", engine.voice_name == before,
      engine.voice_name)

# Both voices must actually produce audio after switching around.
speaker.set_language("tr")
turkish_audio = engine.synthesize("Merhaba, bu bir testtir.")
check("the Turkish voice speaks", len(turkish_audio) > 1000,
      f"{len(turkish_audio)} samples")
speaker.set_language("en")
english_audio = engine.synthesize("Hello, this is a test.")
check("the English voice speaks", len(english_audio) > 1000,
      f"{len(english_audio)} samples")

speaker.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
