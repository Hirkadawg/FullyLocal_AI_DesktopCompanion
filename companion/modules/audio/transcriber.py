"""Rolling transcript of what has been playing through the speakers.

Runs on its own thread with its own Whisper model. Its own thread because a
chunk takes the better part of a second and must not stall answers or ambient
sampling; its own model because faster-whisper's `WhisperModel` is not
documented as thread-safe, and push-to-talk is using the other one.

Measured cost per 15-second chunk on this machine, CPU/int8:

    tiny.en   342 ms  = 2.3% of a core if it never stopped
    base.en   805 ms  = 5.4%

And it does stop: chunks quieter than `silence_rms` are dropped before the
model sees them, so silence costs nothing beyond the capture itself. Nothing is
written to disk -- the transcript lives in memory and is discarded on exit.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np

from core.logging import get_logger
from modules.audio.loopback import SystemAudioCapture
from modules.voice.stt.base import STTEngine

log = get_logger(__name__)


@dataclass
class TranscriptLine:
    text: str
    at: float  # unix time the chunk ended
    #: The language Whisper detected for this chunk, if it said.
    language: str | None = None

    @property
    def age(self) -> float:
        return time.time() - self.at


class AudioTranscriber:
    """Keeps a rolling transcript of recent system audio."""

    def __init__(
        self,
        capture: SystemAudioCapture,
        stt: STTEngine,
        chunk_seconds: float = 15.0,
        silence_rms: float = 0.005,
        keep_minutes: float = 10.0,
        speaking_probe: "Callable[[], bool] | None" = None,
    ) -> None:
        self.capture = capture
        self.stt = stt
        self.chunk_seconds = chunk_seconds
        self.silence_rms = silence_rms
        self.keep_minutes = keep_minutes
        # Loopback taps the OUTPUT device, so it hears the companion's own
        # speech as readily as a video's. Without this the transcript fills up
        # with the companion quoting itself back -- and worse, its own answers
        # then become "what was heard" for the next question.
        self.speaking_probe = speaking_probe
        self._last_spoke_at = 0.0
        self.chunks_dropped_to_echo = 0

        self._lines: deque[TranscriptLine] = deque()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.chunks_seen = 0
        self.chunks_transcribed = 0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="audio-transcriber", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(5.0, self.chunk_seconds))
            self._thread = None

    def transcript(self, minutes: float = 5.0, max_chars: int = 3000) -> str:
        """Recent speech, oldest first, trimmed to a character budget."""
        cutoff = time.time() - minutes * 60
        with self._lock:
            recent = [line.text for line in self._lines if line.at >= cutoff]
        if not recent:
            return ""
        joined = " ".join(recent).strip()
        if len(joined) > max_chars:
            # Keep the END: "what did they just say" is about the latest part.
            joined = "…" + joined[-max_chars:]
        return joined

    def recent(self, minutes: float = 2.0, max_chars: int = 3000) -> list[TranscriptLine]:
        """Lines heard in the last `minutes`, NEWEST FIRST, within a character budget.

        Newest first and with their times, because an undated five minutes of
        transcript, oldest first under one language label, is what let a minute-old
        Turkish video turn an English question's answer Turkish (3 times in 5).
        """
        cutoff = time.time() - minutes * 60
        with self._lock:
            lines = [line for line in self._lines if line.at >= cutoff]
        kept, used = [], 0
        for line in reversed(lines):
            if used + len(line.text) > max_chars and kept:
                break
            kept.append(line)
            used += len(line.text)
        return kept

    @property
    def last_spoke_at(self) -> float:
        """When the companion was last heard speaking (sampled every 0.25 s), or 0."""
        return self._last_spoke_at

    def _run(self) -> None:
        while not self._stop.is_set():
            # Wait a chunk's worth, checking the stop flag often so shutdown is
            # prompt rather than up to chunk_seconds late.
            deadline = time.time() + self.chunk_seconds
            while time.time() < deadline:
                if self._stop.wait(0.25):
                    return
                # Sampled during the wait rather than checked once at the end:
                # a chunk is contaminated if the companion spoke at ANY point
                # while it was being recorded, not only just now.
                if self.speaking_probe is not None:
                    try:
                        if self.speaking_probe():
                            self._last_spoke_at = time.time()
                    except Exception:
                        pass

            try:
                self._process_chunk()
            except Exception:  # pragma: no cover -- must never kill the thread
                log.warning("audio transcription chunk failed", exc_info=True)

    def _process_chunk(self) -> None:
        # Cap at twice the chunk length: enough slack for a late tick, without
        # letting a stalled thread hand the model a minute of backlog.
        audio = self.capture.drain(self.chunk_seconds * 2)
        if len(audio) < self.stt.sample_rate:  # under a second isn't worth it
            return

        self.chunks_seen += 1

        # Discard the whole chunk if the companion spoke during it. Partial
        # trimming would be guesswork -- speech and video overlap in time, not
        # in separable channels -- and a chunk of the companion's own voice is
        # worse than no chunk at all.
        spoke_within = time.time() - self._last_spoke_at < self.chunk_seconds * 1.5
        if spoke_within:
            self.chunks_dropped_to_echo += 1
            log.debug("dropping chunk: the companion was speaking during it")
            return

        rms = float(np.sqrt(np.mean(np.square(audio))))
        if rms < self.silence_rms:
            log.debug("audio chunk silent (rms %.5f), skipping", rms)
            return

        started = time.perf_counter()
        text = self.stt.transcribe(audio)
        elapsed = (time.perf_counter() - started) * 1000
        self.chunks_transcribed += 1

        if not text:
            log.debug("audio chunk had sound but no speech (%.0f ms)", elapsed)
            return

        log.debug("system audio (%.0f ms): %s", elapsed, text[:70])
        with self._lock:
            self._lines.append(TranscriptLine(text=text, at=time.time(),
                                              language=getattr(self.stt, "last_language", None)))
            cutoff = time.time() - self.keep_minutes * 60
            while self._lines and self._lines[0].at < cutoff:
                self._lines.popleft()
