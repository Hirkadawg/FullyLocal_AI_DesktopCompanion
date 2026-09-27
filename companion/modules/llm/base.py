"""LLM provider interface.

Deliberately a generic chat interface rather than `ask(question, screen_text)`.
`messages` carries conversation memory, `images` carries the vision path, and
`tools` carries timers, notes and web search. Any other provider fits the same
ABC. Prompt assembly -- turning a
ScreenContext plus a question into messages -- belongs to the orchestrator, not
to a provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Iterator, Sequence

from core.types import Message, ToolCall, ToolSpec

if TYPE_CHECKING:
    from PIL.Image import Image


class LLMProvider(ABC):
    """A chat-completion backend, local or remote."""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: Sequence[Message],
        images: Sequence["Image"] | None = None,
        tools: Sequence[ToolSpec] | None = None,
        stream: bool = False,
        collect_tool_calls: list[ToolCall] | None = None,
        temperature: float | None = None,
        json_schema: dict | None = None,
    ) -> Iterator[str]:
        """Generate a reply, yielding text chunks.

        Non-streaming callers get a single chunk, so consumers can always just
        iterate and stay agnostic about which mode is in use.

        `collect_tool_calls` is an out-parameter: any tools the model asks for
        are appended to it. A generator cannot both yield text and return
        something to a `for` loop, and tool calls must not be forced through
        the text channel where they would be spoken aloud.

        `temperature` overrides the provider's configured value for this call
        only. Answers and remarks want different ones: the same question should
        get the same answer, but the same page should not get the same remark
        every time.

        `json_schema` constrains the reply to JSON matching that schema, for
        callers that parse it rather than show it. The model can then be held
        to fields code can check -- a remark must come with a reason it is
        worth saying -- instead of being asked nicely.
        """

    @abstractmethod
    def health_check(self) -> None:
        """Raise `LLMUnavailable` with actionable text if unusable."""

    def warm_up(self) -> None:
        """Optional: pay start-up costs before the user is waiting on them."""
