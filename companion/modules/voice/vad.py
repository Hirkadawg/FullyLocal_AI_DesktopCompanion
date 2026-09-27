"""Telling speech from everything else, and cutting a stream into utterances.

Hands-free listening needs to know when someone starts talking and
when they have finished, without a key. Loudness can't do it: a keyboard or a
fan is as loud as a quiet voice. Silero VAD, which faster-whisper already ships,
can. Measured on this machine (14 September 2026): it found 3.39 s of speech in
3.4 s of synthesised speech, and 0 s in five seconds of room noise, in
keyboard-like clicks, and in what the microphone picked up of a sentence played
through the speakers -- at 3-8 ms a call on the CPU.

Silero needs context. Asked about only the last 0.8 s, it missed the tail of
speech at the start of that window: a 0.4 s pause inside "What is this article
about, and how old is it?" was taken for the end, and the question was split in
two. So the end of an utterance is judged over the last `pause_s + context_s`,
as the time since the last stretch of speech ended.
"""

from __future__ import annotations

from typing import Callable

import numpy as np

SAMPLE_RATE = 16000

#: The step reports this when someone has started talking.
STARTED = "started"

Segments = list[tuple[float, float]]


def silero_segments(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> Segments:
    """The stretches of `audio` Silero VAD judges to be speech, in seconds."""
    audio = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(audio) < sample_rate // 10:
        return []
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    # No padding and a short minimum silence: the defaults pad each stretch of
    # speech by 400 ms and merge gaps under 2 s, which would hide the pause
    # that ends an utterance.
    options = VadOptions(min_silence_duration_ms=100, speech_pad_ms=0)
    return [
        (s["start"] / sample_rate, s["end"] / sample_rate)
        for s in get_speech_timestamps(audio, options)
    ]


def silero_speech_seconds(audio: np.ndarray, sample_rate: int = SAMPLE_RATE) -> float:
    """How many seconds of `audio` Silero VAD judges to be speech."""
    return _total(silero_segments(audio, sample_rate))


def _total(segments: Segments) -> float:
    return sum(end - start for start, end in segments)


class UtteranceDetector:
    """Feed it audio as it arrives; each step says whether someone started or
    finished saying something.

    Idle, it keeps only the last second or so and asks whether that holds
    speech. Once someone is talking, it keeps everything -- including a moment
    from before speech was detected, so the first word isn't clipped -- until
    `pause_s` has passed since speech last ended, then hands the utterance over.
    """

    def __init__(
        self,
        segments: Callable[[np.ndarray], Segments] = silero_segments,
        sample_rate: int = SAMPLE_RATE,
        pause_s: float = 0.8,
        start_speech_s: float = 0.25,
        min_speech_s: float = 0.4,
        max_seconds: float = 30.0,
        preroll_s: float = 0.3,
        window_s: float = 1.0,
        context_s: float = 1.0,
    ) -> None:
        self.segments = segments
        self.sample_rate = sample_rate
        self.pause_s = pause_s
        self.start_speech_s = start_speech_s
        self.min_speech_s = min_speech_s
        self.max_seconds = max_seconds
        self.preroll_s = preroll_s
        self.window_s = window_s
        self.context_s = context_s
        self.reset()

    def reset(self) -> None:
        self._buffer = np.zeros(0, dtype=np.float32)
        self.in_speech = False

    def feed(self, samples: np.ndarray) -> None:
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(samples):
            self._buffer = np.concatenate([self._buffer, samples])

    def step(self) -> str | np.ndarray | None:
        """STARTED, a finished utterance's audio, or None."""
        rate = self.sample_rate
        if not self.in_speech:
            self._buffer = self._buffer[-int((self.window_s + self.preroll_s) * rate):]
            window = self._buffer[-int(self.window_s * rate):]
            if len(window) < int(self.window_s * rate) // 2:
                return None
            if _total(self.segments(window)) >= self.start_speech_s:
                self.in_speech = True
                return STARTED
            return None

        too_long = len(self._buffer) >= self.max_seconds * rate
        paused = False
        if len(self._buffer) >= int((self.window_s + self.pause_s) * rate):
            span = self._buffer[-int((self.pause_s + self.context_s) * rate):]
            found = self.segments(span)
            last_end = found[-1][1] if found else 0.0
            paused = len(span) / rate - last_end >= self.pause_s
        if not (paused or too_long):
            return None
        audio = self._buffer
        self.reset()
        if _total(self.segments(audio)) < self.min_speech_s:
            return None  # a cough or a word fragment, not something to answer
        return audio
