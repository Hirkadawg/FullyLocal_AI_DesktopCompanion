"""Exception types.

These carry user-facing remediation text. A failure the user can't act on is a
failure they'll report back as "it didn't work".
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.privacy import PrivacyBlock


class CompanionError(Exception):
    """Base class for expected, explainable failures."""


class ConfigError(CompanionError):
    pass


class CaptureError(CompanionError):
    pass


class OCRError(CompanionError):
    pass


class LLMUnavailable(CompanionError):
    """The local model or its server isn't usable. Message must say how to fix it."""


class PrivacyBlocked(CompanionError):
    """A window on the ignore-list was visible, so nothing was captured."""

    def __init__(self, block: PrivacyBlock) -> None:
        self.block = block
        super().__init__(
            f"Capture blocked: {block.reason} (window: {block.window})"
        )
