"""Data types shared between modules.

`ScreenContext` is the contract between perception and reasoning. Every
perception source produces one -- OCR, UI Automation and the vision model
alike -- and nothing downstream needs to know which source produced it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Iterator, Literal

if TYPE_CHECKING:
    from PIL.Image import Image

Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class WindowInfo:
    """A visible top-level window."""

    title: str
    process: str  # executable name, lowercased, e.g. "chrome.exe"
    rect: tuple[int, int, int, int]  # virtual-screen coords: left, top, right, bottom
    is_foreground: bool = False
    hwnd: int = 0  # needed to hand the window to UI Automation

    def __str__(self) -> str:
        return f"{self.process} -- {self.title!r}"

    @property
    def area(self) -> int:
        return max(0, self.rect[2] - self.rect[0]) * max(0, self.rect[3] - self.rect[1])


@dataclass(frozen=True)
class WindowCandidate:
    """A window considered for reading, and how it scored."""

    window: WindowInfo
    reason: str  # "ok", or why it was ruled out
    monitor_share: float  # fraction of the MONITOR this window covers
    self_share: float  # fraction of the WINDOW that is on this monitor

    @property
    def viable(self) -> bool:
        return self.reason == "ok"


@dataclass(frozen=True)
class TextRegion:
    """One detected span of text, in screen coordinates."""

    text: str
    box: tuple[int, int, int, int]  # left, top, right, bottom
    confidence: float

    @property
    def y_center(self) -> float:
        return (self.box[1] + self.box[3]) / 2

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]


@dataclass
class ScreenContext:
    """What the companion can currently see."""

    text: str = ""
    regions: list[TextRegion] = field(default_factory=list)
    image: Image | None = None  # retained only when a vision model may need it
    source: str = "none"  # "ocr" | "uia" | "vlm" | "replay"
    window_title: str | None = None
    app_name: str | None = None
    monitor_index: int = 1
    captured_at: float = field(default_factory=time.time)
    timings_ms: dict[str, float] = field(default_factory=dict)
    #: How far down the page is scrolled, 0-100, when the app reports it (UI
    #: Automation's scroll pattern). None when it doesn't -- many apps don't.
    scroll: float | None = None

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def age_seconds(self) -> float:
        return time.time() - self.captured_at

    def summary(self) -> str:
        """One-line description for logs and the CLI status line."""
        timings = " ".join(f"{k}={v:.0f}ms" for k, v in self.timings_ms.items())
        where = self.app_name or "unknown app"
        return (
            f"[{self.source}] {len(self.text)} chars from {len(self.regions)} regions "
            f"({where}) {timings}".strip()
        )


@dataclass(frozen=True)
class ToolCall:
    """A tool the model asked to run."""

    name: str
    arguments: dict

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v!r}" for k, v in self.arguments.items())
        return f"{self.name}({args})"


@dataclass(frozen=True)
class Message:
    role: Role
    content: str
    #: Set on assistant messages that requested tools.
    tool_calls: tuple[ToolCall, ...] = ()
    #: Set on `role="tool"` messages, naming the tool that produced the result.
    tool_name: str | None = None
    #: Set on remarks: why the companion said it. Never sent to the model as
    #: part of the message; the app hands it over when the user asks why.
    reason: str = ""
    #: Screenshots sent with this message, as encoded image bytes. Only ever
    #: on the current turn: memory keeps text, never images.
    images: tuple[bytes, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    """A tool offered to the model, in JSON-schema form."""

    name: str
    description: str
    parameters: dict

    def to_ollama(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class Answer:
    """A pending answer: the context it was grounded in, plus a token stream.

    Splitting these lets a caller show what the companion saw before the model
    has finished replying.
    """

    context: ScreenContext
    chunks: Iterator[str]

    def text(self) -> str:
        """Consume the stream and return the whole answer."""
        return "".join(self.chunks)


@dataclass(frozen=True)
class Delivery:
    """How much of one spoken utterance actually reached the speakers.

    Text is on screen long before it is spoken -- an answer takes ~2 s to write
    and ~12 s to say -- so what the user saw and what they heard diverge
    whenever speech is cut off. This records the second.
    """

    #: Sentences queued for speech, in order, as written rather than pronounced.
    sentences: tuple[str, ...] = ()
    #: How many of them played to the end.
    finished: int = 0
    #: Whether the one after those was cut off partway through.
    partial: bool = False
    #: Text already written but not yet complete enough to queue as a sentence.
    unqueued: str = ""

    @property
    def heard(self) -> tuple[str, ...]:
        return self.sentences[: self.finished]

    @property
    def cut_during(self) -> str | None:
        """The sentence playing when it was stopped, if any."""
        if self.partial and self.finished < len(self.sentences):
            return self.sentences[self.finished]
        return None

    @property
    def complete(self) -> bool:
        """Everything written so far was spoken to the end."""
        return (
            self.finished >= len(self.sentences)
            and not self.partial
            and not self.unqueued.strip()
        )
