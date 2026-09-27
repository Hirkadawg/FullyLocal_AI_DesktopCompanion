"""Text-to-speech interface.

Kept narrow on purpose: text in, mono int16 PCM out. A cloned voice swaps in behind this same
interface, and nothing upstream needs to change.

Returning raw samples rather than playing them keeps synthesis and playback
separate, which is what makes barge-in possible -- audio already generated can
be thrown away without the engine knowing or caring.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np


class TTSEngine(ABC):
    """Turns text into speech samples."""

    name: str = "base"

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        """Output rate in Hz."""

    @abstractmethod
    def synthesize(self, text: str) -> np.ndarray:
        """Render `text` as mono int16 samples. Empty array if nothing to say."""

    def synthesize_marked(self, text: str) -> tuple[np.ndarray, list[tuple[str, int]] | None]:
        """The samples, and the mouth shape of each stretch of them when the
        engine knows (modules/voice/visemes.py) -- else None."""
        return self.synthesize(text), None

    def warm_up(self) -> None:
        """Optional: pay one-off costs before the user is waiting on them."""

    def close(self) -> None:
        """Release resources. Safe to call more than once."""
