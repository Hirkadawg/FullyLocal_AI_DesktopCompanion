"""Microphone capture for push-to-talk.

The stream is opened when you press the key and closed when you let go. That is
a deliberate privacy property, not just tidiness: between utterances the
companion holds no microphone handle at all, so "is it listening right now" has
an answer you can verify in Windows' own microphone indicator rather than having
to trust this code. Hands-free listening is the one exception, and only while it
is switched on: then the stream stays open until it is switched off.

Recording is at the rate the speech engine wants (16 kHz), so nothing is ever
resampled.
"""

from __future__ import annotations

import threading

import numpy as np

from core.errors import CompanionError
from core.logging import get_logger

log = get_logger(__name__)


class MicrophoneUnavailable(CompanionError):
    """No usable input device."""


class Recorder:
    """Captures mono audio while held open."""

    def __init__(
        self,
        sample_rate: int = 16000,
        device: int | str | None = None,
        max_seconds: float = 30.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.device = device
        self.max_seconds = max_seconds
        #: The chosen microphone when it wasn't connected at the last start, so
        #: the system default listened instead; None when it was found.
        self.missing: str | None = None

        self._lock = threading.Lock()
        self._blocks: list[np.ndarray] = []
        self._stream = None
        self._frames = 0

    @property
    def is_recording(self) -> bool:
        return self._stream is not None

    @property
    def seconds_recorded(self) -> float:
        with self._lock:
            return self._frames / self.sample_rate

    def start(self) -> None:
        """Open the microphone and begin capturing. Idempotent."""
        if self._stream is not None:
            return
        import sounddevice as sd

        from modules.voice.devices import input_devices, resolve

        # Resolved at every start, not once: a microphone plugged in or chosen
        # since the last recording is used, and one unplugged falls back.
        device, self.missing = None, None
        if self.device not in (None, ""):
            try:
                device = resolve(self.device, input_devices())
            except Exception:
                log.debug("could not list input devices", exc_info=True)
            if device is None:
                self.missing = str(self.device)
                log.warning("microphone %r is not connected; using the system default",
                            self.device)

        with self._lock:
            self._blocks = []
            self._frames = 0

        try:
            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="float32",
                device=device,
                callback=self._callback,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise MicrophoneUnavailable(
                f"Could not open the microphone: {exc}\n"
                f"  Check Windows sound settings, or choose another microphone "
                f"in Settings > Voice."
            ) from exc
        log.debug("recording at %d Hz from %s", self.sample_rate,
                  "default input" if device is None else f"device {device}")

    def drain(self) -> np.ndarray:
        """Everything captured since the last drain, keeping the stream open.

        For hands-free listening, which consumes audio as it arrives rather than
        collecting one utterance.
        """
        with self._lock:
            blocks, self._blocks = self._blocks, []
            self._frames = 0
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(blocks).astype(np.float32)

    def snapshot(self) -> np.ndarray:
        """A copy of everything captured so far, without stopping."""
        with self._lock:
            blocks = list(self._blocks)
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(blocks).astype(np.float32)

    def stop(self) -> np.ndarray:
        """Close the microphone and return everything captured."""
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:  # pragma: no cover
                log.debug("closing input stream failed", exc_info=True)

        with self._lock:
            blocks, self._blocks = self._blocks, []
            self._frames = 0
        if not blocks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(blocks).astype(np.float32)

    def _callback(self, indata, frames: int, time_info, status) -> None:
        """Runs on the audio thread: copy and return, nothing more."""
        if status:
            log.debug("input status: %s", status)
        with self._lock:
            if self._frames >= self.max_seconds * self.sample_rate:
                return  # hard cap, so a stuck key can't eat memory
            self._blocks.append(indata.copy().reshape(-1))
            self._frames += frames
