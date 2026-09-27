"""Perception interface.

A perception source turns "whatever is on screen right now" into a
`ScreenContext`. OCR was the first implementation; a UI Automation source (real
text straight out of the application) and a vision-model source sit behind the
same interface.

`read()` takes no arguments on purpose: sources that read the accessibility tree
never need an image, so an image parameter would be wrong for half of them.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.types import ScreenContext


class PerceptionSource(ABC):
    """Produces a ScreenContext describing the current screen."""

    #: Short identifier recorded in `ScreenContext.source`.
    name: str = "base"

    @abstractmethod
    def read(self) -> ScreenContext:
        """Observe the screen and return structured context."""

    def close(self) -> None:
        """Release any held resources. Safe to call more than once."""
