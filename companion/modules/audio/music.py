"""Key, tempo and chords of what is playing, with numpy alone.

Deliberately no librosa: its dependencies (scipy, scikit-learn, numba) would
add a few hundred MB for what numpy's FFT does here in a few hundred ms. The
methods are the classic ones, and their limits are said out loud in the report
rather than hidden:

- **Key**: an energy-weighted pitch-class profile, correlated with the
  Krumhansl-Kessler major and minor profiles in all 24 keys. A key and its
  relative (C major / A minor) share every note, so when the two score close,
  both are named.
- **Chords**: pitch-class profiles over about a second, matched against major
  and minor triad templates that include the notes' first overtones. Triads
  only -- a Cmaj7 reads as C or Em. Dense mixes and distorted guitars do worse
  than clear piano or acoustic guitar.
- **Tempo**: autocorrelation of spectral flux (how suddenly the spectrum
  changes), leaning towards 120 BPM between equally good candidates. Half or
  double the true tempo is the classic mistake, so a strong double is named.

Only spectral peaks enter the pitch profiles, so drums and noise -- broadband
by nature -- barely move them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

NOTE_NAMES = ("C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B")

# Krumhansl & Kessler (1982): how well each scale degree fits a major/minor key.
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])

#: Semitones above a note of its 1st-5th harmonics, and how much each counts.
_HARMONICS = ((0, 1.0), (12, 0.6), (19, 0.36), (24, 0.22), (28, 0.13))

#: Seconds of recent system audio kept for music questions.
BUFFER_SECONDS = 60.0

#: Below this key score it isn't music with a key. Measured: speech 0.32, white
#: noise 0.47; synthetic music 0.77-0.94, including with a voice over it.
MIN_KEY_SCORE = 0.6
#: Chords are reported only when recognised this much of the time. With speech
#: mixed over music, coverage fell with accuracy: 94% -> 89% right, 61% -> 59%,
#: 33% -> 30%.
MIN_CHORD_COVERAGE = 0.5
#: Below this there is no steady beat to count. Measured: speech 0.08, noise
#: 0.04; synthetic music 0.53 and up.
MIN_BEAT_STRENGTH = 0.2
#: A shorter excerpt is said to be one: its key depends on where it began.
SHORT_EXCERPT_S = 15.0

CHROMA_FFT = 8192   # ~0.5 s at 16 kHz: 2 Hz bins, enough to separate low semitones
CHROMA_HOP = 2048
ONSET_FFT = 1024
ONSET_RATE = 100    # onset frames per second


@dataclass
class Chord:
    name: str
    start_s: float
    end_s: float

    @property
    def seconds(self) -> float:
        return self.end_s - self.start_s


@dataclass
class MusicReport:
    seconds: float
    #: How loud it was; under the capture's silence level, nothing was playing.
    rms: float
    key: str = ""
    #: Correlation of the best key, 0-1. Real music scores roughly 0.6-0.9.
    key_score: float = 0.0
    #: The runner-up key when it scored close to the best, else "".
    key_alternative: str = ""
    tempo_bpm: float = 0.0
    #: How regular the beat is, 0-1.
    beat_strength: float = 0.0
    #: A double (or half) tempo that fits nearly as well, else 0.
    tempo_alternative: float = 0.0
    chords: list[Chord] = field(default_factory=list)
    #: Share of the time a chord was recognised at all.
    chord_coverage: float = 0.0


# -- building blocks -----------------------------------------------------------

def _frames(audio: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    if len(audio) < n_fft:
        audio = np.pad(audio, (0, n_fft - len(audio)))
    count = 1 + (len(audio) - n_fft) // hop
    index = np.arange(n_fft)[None, :] + hop * np.arange(count)[:, None]
    return audio[index]


def _magnitudes(audio: np.ndarray, n_fft: int, hop: int) -> np.ndarray:
    frames = _frames(audio, n_fft, hop) * np.hanning(n_fft).astype(np.float32)
    return np.abs(np.fft.rfft(frames, axis=1)).astype(np.float32)


def _pitch_class_map(sample_rate: int, n_fft: int, fmin: float = 55.0,
                     fmax: float = 2000.0) -> np.ndarray:
    """(12, bins): how much each FFT bin belongs to each pitch class."""
    freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
    mapping = np.zeros((12, len(freqs)), dtype=np.float32)
    inside = np.nonzero((freqs >= fmin) & (freqs <= fmax))[0]
    midi = 69 + 12 * np.log2(freqs[inside] / 440.0)
    nearest = np.round(midi)
    # Full weight on the semitone, none halfway to the next.
    weight = np.cos(np.pi * (midi - nearest)) ** 2
    mapping[nearest.astype(int) % 12, inside] = weight
    return mapping


def _peaks_only(magnitudes: np.ndarray) -> np.ndarray:
    """Keep spectral peaks standing clear of the frame's floor: notes, not noise."""
    left = np.roll(magnitudes, 1, axis=1)
    right = np.roll(magnitudes, -1, axis=1)
    floor = np.median(magnitudes, axis=1, keepdims=True)
    keep = (magnitudes > left) & (magnitudes >= right) & (magnitudes > 4 * floor + 1e-6)
    return np.where(keep, magnitudes, 0.0)


def pitch_profiles(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """(frames, 12) pitch-class energy, one frame per CHROMA_HOP samples."""
    peaks = _peaks_only(_magnitudes(audio, CHROMA_FFT, CHROMA_HOP))
    return np.log1p(peaks) @ _pitch_class_map(sample_rate, CHROMA_FFT).T


def _templates() -> tuple[list[str], np.ndarray]:
    names, rows = [], []
    for suffix, intervals in (("", (0, 4, 7)), ("m", (0, 3, 7))):
        for root in range(12):
            row = np.zeros(12)
            for interval in intervals:
                for offset, weight in _HARMONICS:
                    row[(root + interval + offset) % 12] += weight
            names.append(NOTE_NAMES[root] + suffix)
            rows.append(row / np.linalg.norm(row))
    return names, np.array(rows)


CHORD_NAMES, CHORD_TEMPLATES = _templates()


def _unit(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=-1, keepdims=True)
    return rows / np.maximum(norms, 1e-9)


# -- the three analyses ----------------------------------------------------------

#: The relative key is named too when it scores within this of the best.
#: Measured on synthetic loops: unambiguous cadences (I-IV-V-I, i-iv-V-i) beat
#: their relative by 0.31-0.48, while loops using the same four chords in either
#: key (C G Am F / Am F C G) came within 0.002-0.22 -- and the minor loops were
#: read as their relative major 12 times in 12.
RELATIVE_MARGIN = 0.15


def find_key(profiles: np.ndarray) -> tuple[str, float, str]:
    """(key, score, the relative key when nearly as good, or "")."""
    total = profiles.sum(axis=0)
    if total.sum() <= 0:
        return "", 0.0, ""
    scored: dict[tuple[int, str], float] = {}
    for mode, profile in (("major", MAJOR_PROFILE), ("minor", MINOR_PROFILE)):
        for tonic in range(12):
            scored[(tonic, mode)] = float(np.corrcoef(total, np.roll(profile, tonic))[0, 1])
    tonic, mode = max(scored, key=scored.get)
    best = scored[(tonic, mode)]
    relative = ((tonic + 9) % 12, "minor") if mode == "major" else ((tonic + 3) % 12, "major")
    close = best - scored[relative] < RELATIVE_MARGIN
    alternative = f"{NOTE_NAMES[relative[0]]} {relative[1]}" if close else ""
    return f"{NOTE_NAMES[tonic]} {mode}", max(0.0, best), alternative


def find_chords(profiles: np.ndarray, sample_rate: int, min_match: float = 0.75,
                min_seconds: float = 0.7) -> tuple[list[Chord], float]:
    """Chords over time and the share of time a chord was recognised."""
    frame_s = CHROMA_HOP / sample_rate
    if len(profiles) == 0:
        return [], 0.0
    # About a second of context per frame: chords last longer than notes do.
    width = max(1, int(round(1.0 / frame_s)))
    kernel = np.ones(width) / width
    smooth = np.stack([np.convolve(profiles[:, i], kernel, mode="same") for i in range(12)], axis=1)
    energy = smooth.sum(axis=1)
    loud = energy > 0.15 * (np.median(energy[energy > 0]) if np.any(energy > 0) else 1)
    similarity = _unit(smooth) @ CHORD_TEMPLATES.T
    best = similarity.argmax(axis=1)
    labels = [CHORD_NAMES[b] if loud[i] and similarity[i, b] >= min_match else ""
              for i, b in enumerate(best)]

    runs: list[list] = []  # [name, first frame, last frame]
    for i, name in enumerate(labels):
        if runs and runs[-1][0] == name:
            runs[-1][2] = i
        else:
            runs.append([name, i, i])
    # A run too short to be a chord joins its neighbour rather than splitting it.
    min_frames = max(1, int(round(min_seconds / frame_s)))
    merged: list[list] = []
    for run in runs:
        if merged and (run[2] - run[1] + 1 < min_frames or run[0] == merged[-1][0]):
            merged[-1][2] = run[2]
        else:
            merged.append(run)
    chords = [Chord(name, first * frame_s, (last + 1) * frame_s)
              for name, first, last in merged if name]
    coverage = sum(1 for name in labels if name) / len(labels)
    return chords, coverage


#: Onset bands: kick, bass and snare body, snare and voice, hats and cymbals.
_ONSET_BANDS = ((0, 200), (200, 1000), (1000, 4000), (4000, 20000))


def find_tempo(audio: np.ndarray, sample_rate: int) -> tuple[float, float, float]:
    """(bpm, beat strength 0-1, a double/half tempo that fits nearly as well, or 0).

    Each band's flux is scaled to the same spread before they are summed, so a
    loud kick doesn't drown the snare: with the kick alone on beats 1 and 3,
    150 BPM read as 75. And a tempo also scores by its half and third beats (a
    comb), which favours the beat over the bar. Measured on 16 synthetic songs,
    50-175 BPM: plain flux 10 exact, band-scaled 13, both 14.
    """
    hop = sample_rate // ONSET_RATE
    if len(audio) < sample_rate * 4:
        return 0.0, 0.0, 0.0
    spectrum = np.log1p(10 * _magnitudes(audio, ONSET_FFT, hop))
    rise = np.maximum(0.0, np.diff(spectrum, axis=0))
    freqs = np.fft.rfftfreq(ONSET_FFT, 1.0 / sample_rate)
    flux = np.zeros(len(rise))
    for low, high in _ONSET_BANDS:
        band = rise[:, (freqs >= low) & (freqs < high)].sum(axis=1)
        if band.std() > 0:
            flux += band / band.std()
    width = ONSET_RATE // 2
    flux = np.maximum(0.0, flux - np.convolve(flux, np.ones(width) / width, mode="same"))
    flux -= flux.mean()
    if not np.any(flux):
        return 0.0, 0.0, 0.0
    power = np.abs(np.fft.rfft(flux, n=2 * len(flux))) ** 2
    autocorr = np.fft.irfft(power)[: len(flux)]
    if autocorr[0] <= 0:
        return 0.0, 0.0, 0.0
    autocorr = autocorr / autocorr[0]

    def at(bpm: float) -> float:
        lag = 60.0 * ONSET_RATE / bpm
        low = int(np.floor(lag))
        if low + 1 >= len(autocorr):
            return 0.0
        frac = lag - low
        return float(autocorr[low] * (1 - frac) + autocorr[low + 1] * frac)

    candidates = np.arange(50.0, 220.0, 0.5)
    raw = np.array([at(bpm) + 0.5 * at(bpm / 2) + 0.33 * at(bpm / 3)
                    for bpm in candidates]) / 1.83
    prior = np.exp(-0.5 * (np.log2(candidates / 120.0) / 0.9) ** 2)
    best = int(np.argmax(raw * prior))
    bpm = float(candidates[best])
    strength = max(0.0, at(bpm))
    # Measured, the half or double scored 0.87-1.98 of the pick when it was the
    # true tempo, and 0.45-0.51 or 1.0-1.15 when it wasn't: no threshold tells
    # them apart, so a close one is named rather than chosen.
    alternative = 0.0
    for other in (bpm * 2, bpm / 2):
        if 50 <= other < 220 and at(other) >= 0.8 * strength:
            alternative = other
            break
    return bpm, min(1.0, strength), alternative


def analyse(audio: np.ndarray, sample_rate: int = 16000) -> MusicReport:
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    seconds = len(audio) / sample_rate
    rms = float(np.sqrt(np.mean(np.square(audio)))) if len(audio) else 0.0
    report = MusicReport(seconds=seconds, rms=rms)
    if len(audio) < sample_rate * 2:
        return report
    profiles = pitch_profiles(audio, sample_rate)
    report.key, report.key_score, report.key_alternative = find_key(profiles)
    report.chords, report.chord_coverage = find_chords(profiles, sample_rate)
    report.tempo_bpm, report.beat_strength, report.tempo_alternative = find_tempo(audio, sample_rate)
    return report


def describe(report: MusicReport, silence_rms: float = 0.005) -> str:
    """The report in words, for the model to relay -- uncertainty included."""
    span = f"{report.seconds:.0f} seconds"
    if report.rms < silence_rms:
        return f"Nothing audible played in the last {span}, so there is no music to analyse."
    if report.key_score < MIN_KEY_SCORE:
        return (f"The last {span} of sound have no clear key or chords: it sounds like speech, "
                "noise or unpitched sound rather than music.")
    lines = [f"Analysis of the last {span} of what is playing (estimates from the audio):"]
    if report.seconds < SHORT_EXCERPT_S:
        # Measured: 10 s of an F major loop that began on its C chord read as C major.
        lines.append(f"Only {span} were heard, so the key is less certain than usual.")
    key = f"Key: {report.key}"
    if report.key_alternative:
        key += (f", or its relative {report.key_alternative} -- the two share the same notes "
                "and this music doesn't settle which")
    lines.append(key + ".")
    if report.tempo_bpm and report.beat_strength >= MIN_BEAT_STRENGTH:
        tempo = f"Tempo: about {report.tempo_bpm:.0f} BPM"
        if report.tempo_alternative:
            tempo += f" (it can also be counted as {report.tempo_alternative:.0f} BPM)"
        lines.append(tempo + ".")
    else:
        lines.append("Tempo: no steady beat.")
    names = [c.name for c in report.chords]
    names = [n for i, n in enumerate(names) if i == 0 or n != names[i - 1]]
    if names and report.chord_coverage >= MIN_CHORD_COVERAGE:
        lines.append("Chords, in the order played: " + " - ".join(names[-16:]) + ".")
        seconds: dict[str, float] = {}
        for chord in report.chords:
            seconds[chord.name] = seconds.get(chord.name, 0.0) + chord.seconds
        common = sorted(seconds, key=seconds.get, reverse=True)[:4]
        lines.append("Most used: " + ", ".join(common) + ".")
        lines.append("Only major and minor chords are recognised; others show as the nearest one.")
    else:
        lines.append("Chords: unclear -- a voice or a dense mix covers them.")
    return "\n".join(lines)


def decode_file(path: str, start_s: float = 0.0, seconds: float = 30.0,
                sample_rate: int = 16000) -> np.ndarray:
    """Mono float32 audio from any file FFmpeg reads (PyAV, already installed)."""
    import av

    skip, wanted = int(start_s * sample_rate), int(seconds * sample_rate)
    chunks, total = [], 0
    with av.open(path) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                block = out.to_ndarray().reshape(-1)
                chunks.append(block)
                total += len(block)
            if total >= skip + wanted:
                break
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32)[skip:skip + wanted]
