"""Hearing what the speakers play.

Plays a passage through the speakers with Piper, captures it back off the output
device, transcribes it, and asks a question that only the SPOKEN content can
answer -- the screen shows a different article entirely.
"""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np

from core.config import AppConfig
from core.logging import setup_logging
from modules.audio.loopback import SystemAudioCapture
from modules.audio.transcriber import AudioTranscriber
from modules.voice.player import AudioPlayer
from modules.voice.stt.whisper_stt import FasterWhisperSTT
from modules.voice.tts.piper_tts import PiperTTS

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


# Deliberately about something absent from the fixture article on screen, so a
# correct answer cannot have come from the screen text.
LECTURE = (
    "Today we are talking about photosynthesis. Plants capture light using "
    "chlorophyll inside structures called chloroplasts. The light reactions "
    "split water and release oxygen as a by-product."
)

print("capture")

capture = SystemAudioCapture(sample_rate=16000, buffer_seconds=60)
try:
    capture.start()
    check("loopback capture starts", capture.running, capture.error or "")
except Exception as exc:
    check("loopback capture starts", False, str(exc))
    print(f"\n  {failures} failure(s)")
    sys.exit(1)

tts = PiperTTS(voice="en_US-lessac-medium", voices_dir=VOICES_DIR)
pcm = tts.synthesize(LECTURE)
spoken_seconds = len(pcm) / tts.sample_rate
print(f"    playing {spoken_seconds:.1f}s of speech through the speakers…")

player = AudioPlayer(sample_rate=tts.sample_rate)
player.enqueue(pcm)
player.wait_until_idle(timeout=60)
time.sleep(0.5)
player.close()

buffered = capture.seconds_buffered
check("audio was captured off the speakers", buffered > spoken_seconds * 0.6,
      f"{buffered:.1f}s buffered for {spoken_seconds:.1f}s played")

audio = capture.take(spoken_seconds + 2)
rms = float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
check("captured audio is not silence", rms > 0.005, f"rms {rms:.4f}")

# What proactive remarks wait on before speaking up. Only this direction is
# checked on the live device: "silent afterwards" would depend on nothing else
# playing on the machine, which is not what is under test here -- the fake-level
# checks in test_audio_hold.py cover it.
since = capture.seconds_since_sound
check("the capture noticed that sound was playing", since < 1.5,
      f"last sound {since:.2f}s ago, checked ~0.5s after playback ended")

print("\ntranscription")

stt = FasterWhisperSTT(model="tiny.en", device="cpu", compute_type="int8",
                       min_seconds=0.5)
started = time.perf_counter()
heard = stt.transcribe(audio)
elapsed = (time.perf_counter() - started) * 1000
print(f"    heard ({elapsed:.0f} ms): {heard[:110]}")

lowered = heard.lower()
hits = [w for w in ("photosynthesis", "chlorophyll", "oxygen", "plants") if w in lowered]
check("the spoken content was transcribed", len(hits) >= 2, f"matched {hits}")

print("\nrolling transcriber")

transcriber = AudioTranscriber(capture, stt, chunk_seconds=2.0, silence_rms=0.005)
check("transcript empty before anything is heard", transcriber.transcript() == "")

transcriber.start()
player = AudioPlayer(sample_rate=tts.sample_rate)
player.enqueue(pcm)
player.wait_until_idle(timeout=60)
player.close()
time.sleep(5)  # let a couple of chunks land
transcriber.stop()

rolling = transcriber.transcript(minutes=10)
print(f"    transcript: {rolling[:110]}")
check("rolling transcript picked up speech", len(rolling) > 20, f"{len(rolling)} chars")
check("chunks were processed", transcriber.chunks_seen > 0,
      f"{transcriber.chunks_seen} seen, {transcriber.chunks_transcribed} transcribed")

print("\nsilence is not transcribed")


class SilentCapture:
    """Always returns silence.

    A fake rather than the live one: relying on the room and the machine being
    quiet made this depend on whether anything happened to be playing, which is
    not what is being tested.
    """

    sample_rate = 16000

    def drain(self, max_seconds):
        return np.zeros(int(16000 * 1.5), dtype=np.float32)


silent = AudioTranscriber(SilentCapture(), stt, chunk_seconds=1.0, silence_rms=0.01)
silent.start()
time.sleep(3.5)
silent.stop()
check("silent chunks skip the model entirely",
      silent.chunks_transcribed == 0,
      f"{silent.chunks_seen} chunks seen, {silent.chunks_transcribed} transcribed")
check("but the chunks were still examined", silent.chunks_seen > 0,
      f"{silent.chunks_seen} seen")

capture.stop()
check("capture stops cleanly", not capture.running)

stt.close()
tts.close()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
