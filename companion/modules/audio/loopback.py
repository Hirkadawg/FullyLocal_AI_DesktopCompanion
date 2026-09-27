"""Capture what the speakers are playing (WASAPI loopback).

This is how the companion hears a video rather than only reading its subtitles.
It taps the output device, so it hears exactly what you hear and nothing from
the microphone.

Two measured facts shaped this:

- **Opening the stream is the expensive part; holding it open is cheap.** A
  first benchmark showed 15% of a core, which turned out to be stream startup
  amortised over two seconds. A recorder held open costs ~2.6%, near enough
  regardless of block size.
- `sounddevice` cannot do this. Its `WasapiSettings` has no `loopback`
  parameter, hence `soundcard` for output capture while `sounddevice` continues
  to handle the microphone and playback.

Audio is kept in a bounded ring buffer and never written to disk.
"""

from __future__ import annotations

import threading
import time
import warnings
from collections import deque

import numpy as np

from core.errors import CompanionError
from core.logging import get_logger

log = get_logger(__name__)


class LoopbackUnavailable(CompanionError):
    """System audio could not be captured."""


class SystemAudioCapture:
    """Keeps the last N seconds of system audio in memory."""

    def __init__(
        self,
        sample_rate: int = 16000,
        buffer_seconds: float = 300.0,
        # Small blocks, not because they are cheaper -- 250 ms and 1000 ms
        # measured the same -- but because the WASAPI buffer overruns if we
        # collect too rarely while other threads are busy, and soundcard then
        # reports a discontinuity and drops words.
        block_seconds: float = 0.25,
        device: str | None = None,
        # A block at least this loud counts as "something is playing".
        sound_rms: float = 0.005,
        # A second copy of the last N seconds that draining never empties, for
        # questions about music (modules/audio/music.py). The main buffer is no
        # use for that: the transcriber drains it every chunk. 60 s at 16 kHz
        # is 3.8 MB; 0 keeps none.
        recent_seconds: float = 0.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.buffer_seconds = buffer_seconds
        self.recent_seconds = recent_seconds
        self._recent: deque[np.ndarray] = deque()
        self._recent_frames = 0
        self.block_seconds = block_seconds
        self.device = device
        self.sound_rms = sound_rms
        # When a block last carried sound. Written by the capture thread and
        # read by the worker; a single float assignment, so no lock.
        self._last_sound_at = 0.0
        # When the latest stretch of sound began. A silence longer than
        # SOUND_GAP_S starts a new stretch; a breath between sentences doesn't.
        self._sound_began_at = 0.0

        self._blocks: deque[np.ndarray] = deque()
        self._frames = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._error: str | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def error(self) -> str | None:
        return self._error

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._error = None
        self._thread = threading.Thread(
            target=self._run, name="system-audio", daemon=True
        )
        self._thread.start()
        # Surface a failure now rather than leaving a silently dead capture.
        time.sleep(0.4)
        if self._error:
            raise LoopbackUnavailable(self._error)

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            self._thread = None
        with self._lock:
            self._blocks.clear()
            self._frames = 0
            self._recent.clear()
            self._recent_frames = 0

    def recent(self, seconds: float) -> np.ndarray:
        """The last `seconds` of audio, oldest first -- untouched by `drain`."""
        wanted = int(seconds * self.sample_rate)
        with self._lock:
            if not self._recent:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(list(self._recent))
        return audio[-wanted:] if len(audio) > wanted else audio

    def _keep(self, mono: np.ndarray, max_frames: int) -> None:
        """Add a block to both buffers. Call with the lock held."""
        self._blocks.append(mono)
        self._frames += len(mono)
        while self._frames > max_frames and len(self._blocks) > 1:
            self._frames -= len(self._blocks.popleft())
        if self.recent_seconds > 0:
            self._recent.append(mono)
            self._recent_frames += len(mono)
            limit = int(self.recent_seconds * self.sample_rate)
            while self._recent_frames > limit and len(self._recent) > 1:
                self._recent_frames -= len(self._recent.popleft())

    def take(self, seconds: float) -> np.ndarray:
        """The most recent `seconds` of audio, oldest first."""
        wanted = int(seconds * self.sample_rate)
        with self._lock:
            if not self._blocks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(list(self._blocks))
        return audio[-wanted:] if len(audio) > wanted else audio

    def drain(self, max_seconds: float) -> np.ndarray:
        """Take everything buffered since the last drain, and clear it.

        Everything, not the last N seconds: the transcriber's loop can run a
        little late, and taking a fixed window would silently discard whatever
        arrived before it -- losing the start of a sentence every time timing
        slipped. `max_seconds` only caps a pathological backlog.
        """
        limit = int(max_seconds * self.sample_rate)
        with self._lock:
            if not self._blocks:
                return np.zeros(0, dtype=np.float32)
            audio = np.concatenate(list(self._blocks))
            self._blocks.clear()
            self._frames = 0
        return audio[-limit:] if len(audio) > limit else audio

    @property
    def seconds_buffered(self) -> float:
        with self._lock:
            return self._frames / self.sample_rate

    @property
    def seconds_since_sound(self) -> float:
        """How long the output has been silent, to within one block.

        What proactive remarks wait on, so they don't talk over a video. It
        cannot tell the companion's own voice from anyone else's -- loopback
        hears both -- which only ever delays a remark briefly after it speaks.
        """
        return time.time() - self._last_sound_at

    #: Silence longer than this ends a stretch of sound: a video
    #: pausing, not a speaker drawing breath.
    SOUND_GAP_S = 1.5

    @property
    def seconds_of_sound(self) -> float:
        """How long the latest stretch of sound lasted, or has lasted so far.

        With `seconds_since_sound`, this tells a video that played for a while
        and just stopped -- a natural moment to say something -- from a
        notification beep.
        """
        if not self._last_sound_at:
            return 0.0
        return self._last_sound_at - self._sound_began_at

    def _note_level(self, block: np.ndarray) -> None:
        """Remember when sound was last playing. Microseconds per block."""
        if block.size and float(np.sqrt(np.mean(np.square(block)))) >= self.sound_rms:
            now = time.time()
            if now - self._last_sound_at > self.SOUND_GAP_S:
                self._sound_began_at = now
            self._last_sound_at = now

    def _run(self) -> None:
        try:
            import soundcard
        except ImportError as exc:  # pragma: no cover
            self._error = f"soundcard is not installed ({exc})"
            return

        try:
            name = self.device or soundcard.default_speaker().name
            microphone = soundcard.get_microphone(name, include_loopback=True)
        except Exception as exc:
            self._error = (
                f"could not open loopback for {self.device or 'the default speaker'}: "
                f"{exc}"
            )
            log.warning("%s", self._error)
            return

        frames = int(self.sample_rate * self.block_seconds)
        max_frames = int(self.sample_rate * self.buffer_seconds)

        # soundcard warns on every dropped WASAPI period. Under load a few are
        # unavoidable and cost a word or two of a gist transcript; a warning
        # per occurrence would drown the log without telling us anything new.
        warnings.filterwarnings(
            "ignore", category=soundcard.SoundcardRuntimeWarning
        )

        try:
            # One long-lived recorder: opening it is what costs, not running it.
            with microphone.recorder(
                samplerate=self.sample_rate, channels=1
            ) as recorder:
                log.info("capturing system audio from %s", name)
                while not self._stop.is_set():
                    block = recorder.record(numframes=frames)
                    mono = np.asarray(block, dtype=np.float32).reshape(-1)
                    self._note_level(mono)
                    with self._lock:
                        self._keep(mono, max_frames)
        except Exception as exc:  # pragma: no cover -- device unplugged, etc.
            self._error = f"system audio capture stopped: {exc}"
            log.warning("%s", self._error, exc_info=True)
