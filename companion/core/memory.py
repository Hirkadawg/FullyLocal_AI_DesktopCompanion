"""Conversation memory.

Holds the running dialogue so follow-ups like "explain that last part again"
resolve against what was actually said.

Deliberately stores only the questions and answers, never the screen text they
were grounded in. Screen text is large, it goes stale the moment the user
scrolls, and re-sending yesterday's screen alongside today's would both blow the
context budget and actively mislead the model. Only the current turn carries
what is on screen right now.
"""

from __future__ import annotations

from collections import deque
from typing import Iterable

from core.logging import get_logger
from core.types import Message

log = get_logger(__name__)


class ConversationMemory:
    """A rolling window of recent turns, bounded by both turns and characters."""

    def __init__(
        self, max_turns: int = 8, max_chars: int = 4000, enabled: bool = True
    ) -> None:
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.enabled = enabled
        self._messages: deque[Message] = deque()

    def add_turn(self, question: str, answer: str) -> None:
        """Record one exchange. Empty answers are dropped, not stored."""
        if not self.enabled or not answer.strip():
            return
        self._messages.append(Message(role="user", content=question.strip()))
        self._messages.append(Message(role="assistant", content=answer.strip()))
        self._trim()

    def add_remark(self, text: str, reason: str = "") -> None:
        """Record something the companion said without being asked.

        A remark has no question in front of it -- that is what makes it a
        remark -- so it is stored as a lone assistant message. Storing it is
        what makes the user's reply a *reply*: without it, answering "yes" to
        "did you find anything interesting?" arrives at the model as an opening
        line with no referent, and it answers something else entirely.

        Proactive commentary and timer announcements both come through here.
        `reason` is why a remark was made; it is kept beside the message rather
        than in it, so it only reaches the model when the user asks why.
        """
        if not self.enabled or not text.strip():
            return
        self._messages.append(
            Message(role="assistant", content=text.strip(), reason=reason.strip())
        )
        self._trim()

    def last_was_remark(self) -> bool:
        """Was the latest message something the companion said unprompted? Then
        what the user says now is most likely a reply to it."""
        if not self.enabled or not self._messages:
            return False
        last = self._messages[-1]
        if last.role != "assistant":
            return False
        # A remark is a lone assistant message; an answer follows a question.
        return len(self._messages) == 1 or self._messages[-2].role != "user"

    def last_reason(self) -> tuple[str, str] | None:
        """(remark, reason) if the latest thing the companion said was a remark
        with a reason; None if it was an answer, or nothing has been said."""
        if not self.enabled:
            return None
        for message in reversed(self._messages):
            if message.role == "assistant":
                return (message.content, message.reason) if message.reason else None
        return None

    def history(self) -> list[Message]:
        """Prior turns, oldest first, ready to slot in before the current one."""
        return list(self._messages) if self.enabled else []

    def clear(self) -> None:
        self._messages.clear()

    @property
    def turns(self) -> int:
        """Exchanges held.

        A question with its reply counts one, and so does a lone remark -- an
        exchange the companion started. Counting assistant messages gets both
        right; `len // 2` only worked while every message was half of a pair.
        """
        return sum(1 for m in self._messages if m.role == "assistant")

    def _trim(self) -> None:
        """Drop oldest exchanges until within budget."""
        while self.turns > self.max_turns:
            self._drop_oldest()
        while self._char_count() > self.max_chars and self.turns > 1:
            self._drop_oldest()

    def _drop_oldest(self) -> None:
        """Drop the oldest exchange, whole.

        A question leaves together with its answer -- a dangling reply with no
        question in front of it reads as the model talking to itself. An
        unprompted remark has no question by nature, so it leaves on its own.
        """
        if not self._messages:
            return
        head = self._messages.popleft()
        if (
            head.role == "user"
            and self._messages
            and self._messages[0].role == "assistant"
        ):
            self._messages.popleft()

    def _char_count(self) -> int:
        return sum(len(m.content) for m in self._messages)

    def __len__(self) -> int:
        return len(self._messages)

    def extend(self, messages: Iterable[Message]) -> None:
        """Restore a saved conversation (used by tests; persistence is later)."""
        self._messages.extend(messages)
        self._trim()
