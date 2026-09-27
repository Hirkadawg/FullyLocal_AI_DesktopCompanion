"""Speech-to-text interface.

Audio in, text out. Kept narrow so the engine can be swapped -- for a different
Whisper size, or something else entirely -- without touching the code that
records or the code that asks questions.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class STTEngine(ABC):
    """Transcribes speech."""

    name: str = "base"

    #: Sample rate the engine expects. Recording opens the mic at this rate so
    #: nothing has to be resampled.
    sample_rate: int = 16000

    @abstractmethod
    def transcribe(self, audio: np.ndarray) -> str:
        """Turn mono float32 samples at `sample_rate` into text.

        Returns an empty string when nothing intelligible was said -- silence,
        a stray keypress, a cough. Callers should treat empty as "ignore this",
        not as an error.
        """

    def warm_up(self) -> None:
        """Optional: pay model load and warm-up before the user is waiting."""

    def close(self) -> None:
        """Release resources. Safe to call more than once."""
