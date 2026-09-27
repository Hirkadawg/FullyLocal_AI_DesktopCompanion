"""Music perception: key, tempo and chords of what is playing.

On synthetic songs with a known answer: an unambiguous key is named exactly, a
key that shares its chords with its relative names both, tempo is right or its
half/double is named, chords are right most of the time and are withheld when a
voice covers them; speech, noise and silence aren't called music. The capture
keeps recent audio that the transcriber's draining doesn't empty; the tool
leaves out the companion's own voice, is offered only for music questions and
goes off with its switches; and a file can be analysed from the command line.
"""

import contextlib
import io
import sys
import tempfile
import wave
from pathlib import Path
from types import SimpleNamespace

import helpers
from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

sys.path.insert(0, str(helpers.TESTS_DIR / "fixtures"))

import numpy as np
from make_music import key_name, song

from core.companion import Companion, build_tools
from core.config import AppConfig
from core.logging import setup_logging
from modules.audio import music
from modules.audio.loopback import SystemAudioCapture
from modules.tools.music import AnalyseMusic
from modules.tools.requests import offered

setup_logging("ERROR")
failures = 0
SR = 16000


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


def chord_accuracy(report, truth):
    right = total = 0
    for start, end, name in truth:
        for x in np.arange(start + 0.05, end, 0.1):
            total += 1
            right += next((c.name for c in report.chords if c.start_s <= x < c.end_s), "") == name
    return right / total


print("the key")

for tonic, mode in ((0, "major"), (7, "major"), (10, "major"), (9, "minor"), (4, "minor"), (6, "minor")):
    audio, _ = song(tonic, mode, 104, progression=f"{mode}_cadence", seed=tonic)
    report = music.analyse(audio, SR)
    check(f"{key_name(tonic, mode)}, a clear cadence: named exactly",
          report.key == key_name(tonic, mode) and not report.key_alternative,
          f"{report.key} ({report.key_score:.2f}) alt {report.key_alternative!r}")
audio, _ = song(9, "minor", 100, seed=3)  # Am F C G: the same chords as C major's loop
report = music.analyse(audio, SR)
check("A minor on the chords it shares with C major: both are named",
      {report.key, report.key_alternative} == {"A minor", "C major"},
      f"{report.key} / {report.key_alternative!r}")
check("...and the report says they share the notes",
      "share the same notes" in music.describe(report))

print("\ntempo and chords")

for i, bpm in enumerate((90, 100, 120, 128, 140)):
    audio, truth = song((i * 5) % 12, "major", bpm, progression="major_cadence", seed=20 + i)
    report = music.analyse(audio, SR)
    close = abs(report.tempo_bpm - bpm) / bpm < 0.04
    named = report.tempo_alternative and abs(report.tempo_alternative - bpm) / bpm < 0.04
    accuracy = chord_accuracy(report, truth)
    check(f"{bpm} BPM: right, or its half/double named -- and the chords right 80%+ of the time",
          (close or named) and accuracy >= 0.8,
          f"{report.tempo_bpm:.1f} (alt {report.tempo_alternative:.1f}), chords {accuracy:.0%}")

audio, truth = song(2, "minor", 120, progression="minor_cadence", seed=40)
text = music.describe(music.analyse(audio, SR))
check("the report gives key, tempo, the chords in order and the most used",
      "Key: D minor" in text and "BPM" in text and "Chords, in the order played: Dm - Gm - A - Dm" in text
      and "Most used: Dm" in text, text)

print("\nnot music, and music under a voice")

from modules.voice.tts.piper_tts import PiperTTS  # noqa: E402

tts = PiperTTS(voice="en_US-lessac-medium", voices_dir=VOICES_DIR)
pcm = np.asarray(tts.synthesize("Today we are talking about photosynthesis. Plants capture light "
                                "using chlorophyll inside structures called chloroplasts. " * 4))
pcm = pcm.astype(np.float32) / 32768 if pcm.dtype == np.int16 else pcm.astype(np.float32)
x = np.arange(0, len(pcm) / tts.sample_rate, 1 / SR)
voice = np.interp(x, np.arange(len(pcm)) / tts.sample_rate, pcm).astype(np.float32)
voice = np.pad(voice, (0, max(0, 30 * SR - len(voice))))[: 30 * SR]
voice /= np.abs(voice).max()

speech = music.analyse(0.3 * voice, SR)
noise = music.analyse(0.05 * np.random.default_rng(1).standard_normal(30 * SR).astype(np.float32), SR)
print(f"    measured: speech key {speech.key_score:.2f} beat {speech.beat_strength:.2f}; "
      f"noise key {noise.key_score:.2f} beat {noise.beat_strength:.2f}")
check("speech is not music", "no clear key" in music.describe(speech), music.describe(speech))
check("noise is not music", "no clear key" in music.describe(noise), music.describe(noise))
check("silence is nothing playing",
      "Nothing audible" in music.describe(music.analyse(np.zeros(30 * SR, np.float32), SR)))

audio, truth = song(7, "major", 112, progression="major_cadence", seed=50)
covered = music.analyse(audio + 0.5 * voice, SR)
text = music.describe(covered)
check("under a loud voice the key still comes through, and chords are withheld, not guessed",
      "Key: G major" in text and "Chords: unclear" in text,
      f"{text} (coverage {covered.chord_coverage:.0%})")
clear = music.describe(music.analyse(audio + 0.1 * voice, SR))
check("...under a quiet one the chords are given", "Chords, in the order played" in clear, clear)

print("\nthe capture keeps recent audio for music")

capture = SystemAudioCapture(sample_rate=SR, buffer_seconds=60, recent_seconds=2.0)
blocks = [np.full(SR // 4, i, dtype=np.float32) for i in range(16)]  # 4 s in quarter-second blocks
with capture._lock:
    for block in blocks:
        capture._keep(block, max_frames=60 * SR)
capture.drain(60)
kept = capture.recent(10)
check("draining for the transcriber doesn't empty it", len(kept) == 2 * SR, f"{len(kept) / SR:.2f} s")
check("...and it holds the latest seconds, oldest first",
      kept[0] == 8 and kept[-1] == 15, f"{kept[0]} … {kept[-1]}")
none = SystemAudioCapture(sample_rate=SR)
with none._lock:
    none._keep(blocks[0], max_frames=60 * SR)
check("with recent_seconds 0 it keeps nothing extra", len(none.recent(10)) == 0)

print("\nthe tool")

cfg = AppConfig.load(CONFIG_PATH)
cfg.music.enabled = cfg.audio.enabled = True
cfg.music.listen_seconds = 30.0
tune, _ = song(5, "major", 120, progression="major_cadence", seed=60)


class FakeCapture:
    sample_rate = SR

    def __init__(self, audio):
        self.audio = audio

    def recent(self, seconds):
        return self.audio[-int(seconds * SR):]


now = 1_000_000.0
hearing = SimpleNamespace(capture=FakeCapture(tune), last_spoke_at=0.0)
tool = AnalyseMusic(cfg, audio=hearing, clock=lambda: now)
answer = tool.run()
check("it analyses what is playing", "Key: F major" in answer and "last 30 seconds" in answer, answer)
hearing.last_spoke_at = now - 11.0
answer = tool.run()
# Only the length is checked: 10 s of this loop begins on its C chord and reads
# as C major, which is why a short excerpt says it is less certain.
check("audio from before the companion last spoke is left out, and a short excerpt says so",
      "last 10 seconds" in answer and "less certain" in answer, answer)
hearing.last_spoke_at = now - 4.0
check("with too little music since, it says so instead of analysing its own voice",
      "own voice" in tool.run(), tool.run())
hearing.last_spoke_at = 0.0
cfg.music.enabled = False
check("switched off, it says so", "switched off" in tool.run())
cfg.music.enabled = True
check("without system audio, it says so", "switched off" in AnalyseMusic(cfg).run())

registry = build_tools(cfg)
fake_audio = SimpleNamespace(capture=FakeCapture(tune), last_spoke_at=0.0)
companion = Companion(cfg, SimpleNamespace(), SimpleNamespace(), SimpleNamespace(), SimpleNamespace(),
                      tools=registry, audio=fake_audio)
check("the companion hands the tool its system audio", registry.get("analyse_music").audio is fake_audio)
check("...and offers it only while music and system audio are on",
      "analyse_music" not in companion._switched_off())
cfg.audio.enabled = False
off_without_audio = "analyse_music" in companion._switched_off()
cfg.audio.enabled, cfg.music.enabled = True, False
check("   (off without audio, off without music)",
      off_without_audio and "analyse_music" in companion._switched_off())
cfg.music.enabled = True

asked = ("What key is this song in?", "what are the chords?", "What's the tempo of this?",
         "how many bpm is this track", "Is this in major or minor?", "bu şarkı hangi tonda?",
         "akorları ne?", "what's the key of this song")
not_asked = ("What is this song about?", "Who sings this?", "What is the key point of this article?",
             "play some music", "what does this page say", "the key of success is patience")
check("offered for questions about key, chords and tempo",
      all(offered("analyse_music", q) for q in asked),
      str([q for q in asked if not offered("analyse_music", q)]))
check("not for other questions about a song, or 'key' in another sense",
      not any(offered("analyse_music", q) for q in not_asked),
      str([q for q in not_asked if offered("analyse_music", q)]))

print("\na file, from the command line")

folder = Path(tempfile.mkdtemp(prefix="companion-music-"))
path = folder / "song.wav"
stereo_rate = 44100
t = np.arange(0, len(tune) / SR, 1 / stereo_rate)
wide = np.interp(t, np.arange(len(tune)) / SR, tune)
with wave.open(str(path), "wb") as out:
    out.setnchannels(2)
    out.setsampwidth(2)
    out.setframerate(stereo_rate)
    pcm16 = (np.clip(wide, -1, 1) * 32767).astype(np.int16)
    out.writeframes(np.repeat(pcm16[:, None], 2, axis=1).tobytes())
decoded = music.decode_file(str(path), start_s=5, seconds=20)
check("a 44.1 kHz stereo file is read as 16 kHz mono, from --start for --seconds",
      abs(len(decoded) - 20 * SR) <= SR // 10, f"{len(decoded) / SR:.2f} s")
import main  # noqa: E402

printed = io.StringIO()
with contextlib.redirect_stdout(printed):
    code = main._analyse_music(str(path), 0.0, 30.0)
check("main.py --analyse-music prints the analysis", code == 0 and "Key: F major" in printed.getvalue(),
      printed.getvalue()[:200])

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
