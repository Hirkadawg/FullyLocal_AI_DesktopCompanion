"""Audio output with barge-in.

One output stream stays open for the life of the app and a callback pulls from a
queue of pending utterances. Two reasons for that shape rather than playing each
sentence as a separate sound:

- Opening an audio device costs tens of milliseconds. Doing it per sentence
  would put a gap between every one.
- Stopping means clearing a queue, which takes effect within one audio buffer
  (a few milliseconds). Interrupting a sentence has to feel immediate; that is
  the whole point of being able to talk over it.
"""

from __future__ import annotations

import threading
import time
from collections import deque

import numpy as np

from core.logging import get_logger

log = get_logger(__name__)


class _Marks:
    """Labels over an utterance's samples, e.g. mouth shapes: [(label, samples)]."""

    def __init__(self, marks: list[tuple[str, int]]) -> None:
        self.labels = [label for label, _ in marks]
        self.ends = np.cumsum([int(samples) for _, samples in marks])

    def at(self, sample: int) -> str | None:
        index = int(np.searchsorted(self.ends, sample, side="right"))
        return self.labels[index] if index < len(self.labels) else None


class AudioPlayer:
    """Plays queued mono int16 audio, and can be silenced instantly."""

    def __init__(
        self,
        sample_rate: int = 22050,
        device: int | str | None = None,
        blocksize: int = 1024,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.blocksize = blocksize

        self._lock = threading.Lock()
        # (samples, tag) pairs. The tag is whatever the caller passed, handed
        # back through `progress` so it can tell how much was actually heard.
        self._pending: deque[tuple[np.ndarray, object]] = deque()
        self._current: np.ndarray | None = None
        self._current_tag: object = None
        self._finished_tag: object = None
        self._cursor = 0
        self._stream = None
        self._idle = threading.Event()
        self._idle.set()
        # Loudness of the block last handed to the device, 0-1: what the
        # avatar's mouth follows. A float written by the audio thread.
        self._level = 0.0
        # Marks (mouth shapes) of the utterance playing, and the last few blocks
        # handed to the device: (when it starts sounding, frames, portions),
        # each portion (marks, first sample, offset in block, count). So the
        # label sounding NOW can be found between audio callbacks.
        self._current_marks: _Marks | None = None
        self._blocks: deque = deque(maxlen=8)
        self._latency_s = 0.0
        self.clock = time.perf_counter

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Open the output device. Idempotent."""
        if self._stream is not None:
            return
        import sounddevice as sd

        self._stream = sd.OutputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.blocksize,
            device=self.device,
            callback=self._callback,
        )
        self._stream.start()
        try:
            self._latency_s = float(self._stream.latency)
        except (TypeError, ValueError):
            self._latency_s = 0.0
        log.debug("audio out: %d Hz, device=%s", self.sample_rate, self.device or "default")

    def close(self) -> None:
        self.stop()
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:  # pragma: no cover
                log.debug("closing audio stream failed", exc_info=True)
            self._stream = None

    # -- queue ----------------------------------------------------------------

    def enqueue(self, samples: np.ndarray, tag: object = None,
                marks: list[tuple[str, int]] | None = None) -> None:
        """Add an utterance to the back of the play queue.

        `tag` is reported back by `progress` while it plays and after it has
        finished, so a caller can work out how much of what it queued was heard.
        `marks` labels stretches of the samples (mouth shapes), reported by
        `shape_now` while they sound.
        """
        if samples is None or len(samples) == 0:
            return
        self.start()
        with self._lock:
            self._pending.append((np.asarray(samples, dtype=np.int16), tag,
                                  _Marks(marks) if marks else None))
            self._idle.clear()

    def stop(self) -> None:
        """Drop everything queued and cut the current utterance immediately."""
        with self._lock:
            dropped = len(self._pending) + (1 if self._current is not None else 0)
            self._pending.clear()
            self._current = None
            self._current_tag = None
            self._current_marks = None
            self._blocks.clear()
            self._cursor = 0
            self._idle.set()
        self._level = 0.0
        if dropped:
            log.debug("audio stopped, dropped %d pending utterance(s)", dropped)

    def wait_until_idle(self, timeout: float | None = None) -> bool:
        """Block until the queue drains. Returns False on timeout."""
        return self._idle.wait(timeout)

    @property
    def level(self) -> float:
        """How loud what is playing right now is (RMS, 0-1); 0 when silent."""
        return self._level

    def shape_now(self, now: float | None = None) -> str | None:
        """The mark (mouth shape) sounding at `now`, or None when there is none."""
        now = self.clock() if now is None else now
        with self._lock:
            blocks = list(self._blocks)
        for start, frames, portions in reversed(blocks):
            if start > now:
                continue
            offset = int((now - start) * self.sample_rate)
            if offset >= frames:
                return None
            for marks, first, at, count in portions:
                if at <= offset < at + count:
                    return marks.at(first + offset - at) if marks is not None else None
            return None
        return None

    @property
    def is_playing(self) -> bool:
        with self._lock:
            return self._current is not None or bool(self._pending)

    @property
    def queued(self) -> int:
        with self._lock:
            return len(self._pending) + (1 if self._current is not None else 0)

    @property
    def progress(self) -> tuple[object, object]:
        """(tag of the last utterance played to the end, tag of the one playing).

        Either is None when there isn't one. Text reaches the screen long before
        it is spoken, so when playback is cut off this is the only record of
        where the listener actually got to.
        """
        with self._lock:
            return self._finished_tag, self._current_tag

    # -- audio thread ---------------------------------------------------------

    def _callback(self, outdata, frames: int, time_info, status) -> None:
        """Fill one output buffer. Runs on the audio thread: never block here."""
        if status:  # under/overrun; not fatal, but worth knowing about
            log.debug("audio status: %s", status)

        out = outdata.reshape(-1)
        filled = 0
        portions = []
        with self._lock:
            while filled < frames:
                if self._current is None:
                    if not self._pending:
                        break
                    entry = self._pending.popleft()
                    self._current, self._current_tag = entry[0], entry[1]
                    self._current_marks = entry[2] if len(entry) > 2 else None
                    self._cursor = 0
                take = min(frames - filled, len(self._current) - self._cursor)
                out[filled : filled + take] = self._current[
                    self._cursor : self._cursor + take
                ]
                portions.append((self._current_marks, self._cursor, filled, take))
                filled += take
                self._cursor += take
                if self._cursor >= len(self._current):
                    self._current = None
                    self._current_marks = None
                    self._cursor = 0
                    self._finished_tag, self._current_tag = self._current_tag, None
            if self._current is None and not self._pending:
                self._idle.set()
            # Handed over now, heard after the device's output latency.
            self._blocks.append((self.clock() + self._latency_s, frames, portions))

        if filled:
            block = out[:filled].astype(np.float32) / 32768.0
            self._level = float(np.sqrt(np.mean(block * block)))
        else:
            self._level = 0.0
        if filled < frames:
            out[filled:] = 0  # silence rather than whatever was in the buffer
