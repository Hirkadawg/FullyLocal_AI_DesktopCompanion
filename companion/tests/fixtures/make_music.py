"""Synthetic music with a known key, chords and tempo, for the music suites.

Made at test time from sine partials and noise, so no recording is committed and
the right answer is known exactly. Piano-like decaying chords, a bass line, a
melody on chord tones, and optionally drums, detuning (real recordings are
rarely tuned to A440) and a noise floor.
"""

from __future__ import annotations

import numpy as np

NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

#: (semitones above the tonic, "" major / "m" minor) per bar.
#: "major"/"minor" loops use the same four chords (C G Am F / Am F C G), so their
#: key is genuinely ambiguous between relatives; the cadences are not -- the
#: minor one's major V carries the raised leading note.
PROGRESSIONS = {
    "major": ((0, ""), (7, ""), (9, "m"), (5, "")),   # I  V  vi IV
    "minor": ((0, "m"), (8, ""), (3, ""), (10, "")),  # i  VI III VII
    "major_cadence": ((0, ""), (5, ""), (7, ""), (0, "")),    # I  IV V I
    "minor_cadence": ((0, "m"), (5, "m"), (7, ""), (0, "m")),  # i  iv V i
}


def _note(freq: float, seconds: float, sr: int, amp: float, decay: float = 1.2) -> np.ndarray:
    t = np.arange(int(seconds * sr)) / sr
    wave = np.zeros_like(t)
    for k in range(1, 7):
        if freq * k < sr / 2:
            wave += np.sin(2 * np.pi * freq * k * t) / k ** 1.2
    envelope = np.exp(-t / decay) * np.minimum(1.0, t / 0.005)
    return (amp * wave * envelope).astype(np.float32)


def _midi_freq(midi: float) -> float:
    return 440.0 * 2 ** ((midi - 69) / 12)


def _add(track: np.ndarray, sound: np.ndarray, at: int) -> None:
    end = min(len(track), at + len(sound))
    if at < end:
        track[at:end] += sound[: end - at]


def song(tonic: int, mode: str, bpm: float, seconds: float = 30.0, sr: int = 16000,
         detune_cents: float = 0.0, drums: bool = True, noise_db: float = -45.0,
         seed: int = 0, progression: str | None = None,
         ) -> tuple[np.ndarray, list[tuple[float, float, str]]]:
    """(audio, [(start_s, end_s, chord name)]) for a four-bar loop in a key.

    `progression` names an entry of PROGRESSIONS; by default the mode's loop."""
    chords = PROGRESSIONS[progression or mode]
    rng = np.random.default_rng(seed)
    track = np.zeros(int(seconds * sr), dtype=np.float32)
    beat = 60.0 / bpm
    bar = 4 * beat
    shift = detune_cents / 100.0
    truth = []
    start, index = 0.0, 0
    while start < seconds:
        offset, quality = chords[index % 4]
        root = (tonic + offset) % 12
        third = 3 if quality == "m" else 4
        end = min(seconds, start + bar)
        truth.append((start, end, NOTE_NAMES[root] + quality))
        at = int(start * sr)
        for interval in (0, third, 7):  # the chord, around middle C
            _add(track, _note(_midi_freq(48 + root + interval + shift), bar, sr, 0.18), at)
        for b in range(4):  # bass on every beat
            _add(track, _note(_midi_freq(36 + root + shift), beat, sr, 0.25, decay=0.4),
                 int((start + b * beat) * sr))
        for b in range(4):  # a melody on chord tones
            pitch = 60 + root + (0, third, 7, 12)[rng.integers(4)]
            _add(track, _note(_midi_freq(pitch + shift), beat, sr, 0.12, decay=0.5),
                 int((start + b * beat) * sr))
        if drums:
            for b in range(8):  # kick on 1 and 3, snare on 2 and 4, hats on eighths
                t = start + b * beat / 2
                hat = rng.standard_normal(int(0.03 * sr)).astype(np.float32)
                _add(track, 0.05 * hat * np.linspace(1, 0, len(hat)), int(t * sr))
                if b % 2:
                    continue
                if b % 4 == 0:
                    n = int(0.15 * sr)
                    sweep = np.linspace(100, 50, n)
                    kick = np.sin(2 * np.pi * np.cumsum(sweep) / sr) * np.linspace(1, 0, n)
                    _add(track, 0.5 * kick.astype(np.float32), int(t * sr))
                else:
                    snare = rng.standard_normal(int(0.12 * sr)).astype(np.float32)
                    _add(track, 0.2 * snare * np.linspace(1, 0, len(snare)), int(t * sr))
        start += bar
        index += 1
    track += (10 ** (noise_db / 20)) * rng.standard_normal(len(track)).astype(np.float32)
    track *= 0.3 / max(1e-6, float(np.max(np.abs(track))))
    return track, truth


def key_name(tonic: int, mode: str) -> str:
    return f"{NOTE_NAMES[tonic]} {mode}"
