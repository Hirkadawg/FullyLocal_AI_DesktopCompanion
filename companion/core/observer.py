"""Silent observation: a one-line impression of what the user is doing.

A person glancing over your shoulder does not absorb the page -- they form an
impression: *reading about Greek astronomy*, *fixing a Python error*, *watching a
cooking video*. That impression is what makes a later remark sound like a person
rather than a summariser.

It costs a model call, so the orchestrator asks for it lazily: once per page, and
only when a remark is actually about to be attempted. Tab-flipping costs nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.logging import get_logger
from core.types import Message, ScreenContext

log = get_logger(__name__)

DESCRIBE = (
    "In ONE short clause, say what the person is doing. Examples: "
    "'reading a Wikipedia article about the Antikythera mechanism', "
    "'watching a video about knife skills', 'editing Python in VS Code', "
    "'reading email'. No preamble, no full sentence, no quotes. "
    "Under 15 words."
)


@dataclass
class Activity:
    """What the user appears to be doing."""

    summary: str = ""
    title: str = ""
    app: str = ""

    def __bool__(self) -> bool:
        return bool(self.summary)


def describe(
    llm, context: ScreenContext, max_chars: int = 2500, image: bytes | None = None
) -> Activity:
    """One short clause about what they are doing. Never speaks.

    `image`: a screenshot, for a page whose picture says more than its text.

    An empty Activity if the model call fails -- a missing impression means no
    remark, never a crash in the idle loop.
    """
    title = (context.window_title or "").strip()
    app = (context.app_name or "").strip()
    prompt = (
        f"Window title: {title or '(none)'}\n"
        f"Application: {app or '(unknown)'}\n\n"
        f"Text visible on screen:\n{(context.text or '')[:max_chars] or '(almost none)'}"
    )
    if image is not None:
        prompt += "\n\nA screenshot of the screen is attached."
    try:
        out = "".join(
            llm.chat(
                [
                    Message(role="system", content=DESCRIBE),
                    Message(
                        role="user",
                        content=prompt,
                        images=(image,) if image is not None else (),
                    ),
                ],
                stream=False,
            )
        ).strip()
    except Exception:
        log.warning("could not describe activity", exc_info=True)
        return Activity(title=title, app=app)
    # Models like to wrap these in quotes or prefix "The user is".
    out = out.strip().strip('"').strip()
    for prefix in ("the user is ", "the person is ", "they are ", "user is "):
        if out.lower().startswith(prefix):
            out = out[len(prefix):]
    summary = out[:120]
    log.info("now: %s", summary or "(unclear)")
    return Activity(summary=summary, title=title, app=app)
