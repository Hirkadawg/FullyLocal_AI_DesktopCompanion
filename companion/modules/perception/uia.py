"""Perception via Windows UI Automation.

Reads text out of the application's own accessibility tree instead of looking at
pixels. Where it works it is strictly better than OCR: the text is exact rather
than guessed, it arrives in a fraction of the time, and crucially it includes
content that has scrolled out of view -- so "summarise this article" sees the
whole article rather than the screenful you happen to be looking at.

It does not work everywhere. Video, games, remote desktop, canvas-rendered apps
and PDFs in some viewers expose nothing useful. That is what the OCR fallback in
`composite.py` is for; this source reports honestly rather than guessing, and
returns empty when it has nothing.

Chromium quirk: Chrome, Edge, Brave and Electron apps keep their accessibility
tree switched off until an assistive client asks for it, and switching it on is
asynchronous. So a first read can legitimately come back empty on a page that is
perfectly readable a moment later -- hence the nudge-and-retry in `read()`.
"""

from __future__ import annotations

import time
from typing import Any

from core.logging import get_logger, stage
from core.types import ScreenContext
from core.winapi import wake_accessibility
from modules.capture.screen import ScreenSource
from modules.perception.base import PerceptionSource

log = get_logger(__name__)

#: UIA_TextPatternId. Any control exposing it can hand us its text.
TEXT_PATTERN = 10014

#: Control types that never contain a document, so there is no point descending
#: into them. Skipping these keeps a full-window walk in the low hundreds of ms.
_LEAF_TYPES = frozenset(
    {
        "ButtonControl",
        "TabItemControl",
        "MenuItemControl",
        "SeparatorControl",
        "ImageControl",
        "CheckBoxControl",
        "RadioButtonControl",
        "ScrollBarControl",
        "SliderControl",
        "ThumbControl",
        "TitleBarControl",
    }
)


class UIAutomationSource(PerceptionSource):
    """Extracts text from the accessibility tree of the target window."""

    name = "uia"

    def __init__(
        self,
        screen: ScreenSource,
        max_chars: int = 20000,
        max_depth: int = 14,
        max_nodes: int = 3000,
        retry_delay_s: float = 0.6,
        min_chars: int = 40,
        monitor_index: int = 1,
    ) -> None:
        self.screen = screen
        self.max_chars = max_chars
        self.max_depth = max_depth
        self.max_nodes = max_nodes
        self.retry_delay_s = retry_delay_s
        self.min_chars = min_chars
        self.monitor_index = monitor_index
        self._auto: Any | None = None

    def _uia(self) -> Any:
        """Import uiautomation lazily -- it initialises COM and costs ~200 ms."""
        if self._auto is None:
            import uiautomation as auto

            # Default is 10 s. A blocked search must never stall a question for
            # that long when OCR could have answered it in one.
            auto.SetGlobalSearchTimeout(2.0)
            self._auto = auto
        return self._auto

    def read(self) -> ScreenContext:
        timings: dict[str, float] = {}
        window = None

        with stage("window", timings, log):
            # Asks the screen source, so pixel capture and UIA never disagree
            # about which window is being read.
            window = self.screen.target_window()

        if window is None or not window.hwnd:
            log.debug("uia: no window found on monitor %s", self.monitor_index)
            return self._empty(timings, window)

        text, scroll = "", None
        with stage("uia", timings, log):
            text, scroll = self._read_window(window.hwnd)

            if len(text.strip()) < self.min_chars:
                # Probably a Chromium app that had accessibility switched off.
                # Ask it to turn on, wait briefly, try once more.
                if wake_accessibility(window.hwnd):
                    time.sleep(self.retry_delay_s)
                    retried, retried_scroll = self._read_window(window.hwnd)
                    if len(retried) > len(text):
                        log.debug(
                            "uia: %d -> %d chars after waking accessibility",
                            len(text),
                            len(retried),
                        )
                        text, scroll = retried, retried_scroll

        if len(text) > self.max_chars:
            text = text[: self.max_chars]

        context = ScreenContext(
            text=text.strip(),
            regions=[],  # UIA gives text, not per-line boxes; OCR fills these
            image=None,  # no pixels are read, which is most of the speed win
            source=self.name,
            window_title=window.title,
            app_name=window.process,
            monitor_index=self.monitor_index,
            captured_at=time.time(),
            timings_ms=timings,
            scroll=scroll,
        )
        log.debug("perception: %s", context.summary())
        return context

    def _read_window(self, hwnd: int) -> tuple[str, float | None]:
        """The longest text any control in this window exposes, and how far
        down that control is scrolled when the app says.

        Longest wins because a browser window contains several text-bearing
        controls -- the address bar, tab strip, and the page itself. The page is
        reliably the biggest by a wide margin.
        """
        auto = self._uia()
        try:
            root = auto.ControlFromHandle(hwnd)
        except Exception:  # window closed between enumeration and now
            log.debug("uia: could not attach to hwnd %s", hwnd, exc_info=True)
            return "", None
        if root is None:
            return "", None

        best = ""
        best_control = None
        visited = 0
        deadline = time.perf_counter() + 5.0

        def walk(node: Any, depth: int) -> None:
            nonlocal best, best_control, visited
            if depth > self.max_depth or visited > self.max_nodes:
                return
            if time.perf_counter() > deadline:
                return
            try:
                children = node.GetChildren()
            except Exception:
                return
            for child in children:
                visited += 1
                try:
                    control_type = child.ControlTypeName
                except Exception:
                    continue
                text = _pattern_text(child)
                if len(text) > len(best):
                    best, best_control = text, child
                if control_type not in _LEAF_TYPES:
                    walk(child, depth + 1)

        walk(root, 0)
        log.debug("uia: visited %d nodes, best %d chars", visited, len(best))
        return best, _scroll_percent(best_control)

    def _empty(self, timings: dict[str, float], window: Any) -> ScreenContext:
        return ScreenContext(
            text="",
            source=self.name,
            window_title=getattr(window, "title", None),
            app_name=getattr(window, "process", None),
            monitor_index=self.monitor_index,
            timings_ms=timings,
        )


#: UIA_ScrollPatternId.
SCROLL_PATTERN = 10004


def _scroll_percent(control: Any, max_up: int = 4) -> float | None:
    """How far down the page is scrolled, 0-100, when the app reports it.

    Asks the text control, then a few of its ancestors, since the scrollable
    element is often the one around the document. Many apps don't report it --
    one chat app exposed no scrollable control at all -- and None then
    simply means page-end moments aren't used there.
    """
    node = control
    for _ in range(max_up + 1):
        if node is None:
            return None
        try:
            pattern = node.GetPattern(SCROLL_PATTERN)
            if pattern is not None and pattern.VerticallyScrollable:
                return float(pattern.VerticalScrollPercent)
        except Exception:
            pass
        try:
            node = node.GetParentControl()
        except Exception:
            return None
    return None


def _pattern_text(control: Any) -> str:
    """Text from a control's TextPattern, or empty if it exposes none."""
    try:
        pattern = control.GetPattern(TEXT_PATTERN)
    except Exception:
        return ""
    if pattern is None:
        return ""
    try:
        return pattern.DocumentRange.GetText(-1) or ""
    except Exception:
        return ""
