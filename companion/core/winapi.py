"""Minimal Win32 access via ctypes -- no pywin32 dependency.

Two jobs: tell the model which application it is looking at, and let the privacy
guard enumerate what is visible before a screenshot is taken.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from typing import Sequence

from core.logging import get_logger
from core.types import WindowCandidate, WindowInfo

log = get_logger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DWMWA_CLOAKED = 14
WM_GETOBJECT = 0x003D
OBJID_CLIENT = 0xFFFFFFFC
SMTO_ABORTIFHUNG = 0x0002

#: Window classes belonging to the shell rather than to any application. The
#: desktop in particular spans every monitor and would otherwise always look
#: like the largest available window.
_SHELL_CLASSES = frozenset(
    {
        "Progman",              # the desktop
        "WorkerW",              # desktop wallpaper host
        "Shell_TrayWnd",        # taskbar
        "Shell_SecondaryTrayWnd",
        "Windows.UI.Core.CoreWindow",
    }
)

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.SendMessageTimeoutW.argtypes = [
    wintypes.HWND,
    ctypes.c_uint,
    wintypes.WPARAM,
    wintypes.LPARAM,
    ctypes.c_uint,
    ctypes.c_uint,
    ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = wintypes.LPARAM

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE,
    wintypes.DWORD,
    wintypes.LPWSTR,
    ctypes.POINTER(wintypes.DWORD),
]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.GetConsoleWindow.restype = wintypes.HWND

dwmapi.DwmGetWindowAttribute.argtypes = [
    wintypes.HWND,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long


def _window_title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _process_name(hwnd: int) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value:
        return ""
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if not handle:
        return ""  # protected/elevated process; we simply don't learn its name
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _is_cloaked(hwnd: int) -> bool:
    """True for UWP windows that Windows reports as visible but never draws.

    Without this check the desktop is littered with phantom windows, which would
    produce baffling privacy blocks from apps that aren't actually on screen.
    """
    cloaked = wintypes.DWORD()
    result = dwmapi.DwmGetWindowAttribute(
        hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked)
    )
    return result == 0 and cloaked.value != 0


def _window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def _intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return not (a[2] <= b[0] or a[0] >= b[2] or a[3] <= b[1] or a[1] >= b[3])


def visible_windows(
    within: tuple[int, int, int, int] | None = None,
) -> list[WindowInfo]:
    """Enumerate visible, non-minimised, titled top-level windows.

    Args:
        within: optional screen rect; only windows overlapping it are returned.
            Pass the target monitor's bounds so the privacy guard considers
            exactly what the screenshot would contain.
    """
    foreground = user32.GetForegroundWindow()
    found: list[WindowInfo] = []

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        title = _window_title(hwnd)
        if not title:
            return True
        rect = _window_rect(hwnd)
        if rect is None or rect[2] <= rect[0] or rect[3] <= rect[1]:
            return True
        if within is not None and not _intersects(rect, within):
            return True
        if _is_cloaked(hwnd):
            return True
        found.append(
            WindowInfo(
                title=title,
                process=_process_name(hwnd),
                rect=rect,
                is_foreground=(hwnd == foreground),
                hwnd=hwnd,
            )
        )
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_callback), 0)
    except OSError:  # pragma: no cover -- enumeration races with closing windows
        log.warning("window enumeration failed", exc_info=True)
    return found


def foreground_window() -> WindowInfo | None:
    """The window the user is currently interacting with, if it has a title."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    title = _window_title(hwnd)
    rect = _window_rect(hwnd) or (0, 0, 0, 0)
    return WindowInfo(
        title=title,
        process=_process_name(hwnd),
        rect=rect,
        is_foreground=True,
        hwnd=hwnd,
    )


def target_candidates(
    bounds: tuple[int, int, int, int],
    ignore_processes: Sequence[str] = (),
    min_on_monitor: float = 0.5,
) -> list[WindowCandidate]:
    """Score windows on the target monitor, recording why any were ruled out.

    Returned in Z-order, topmost first, so `--probe` can show the ranking and
    the reason beside anything skipped rather than leaving it a mystery.

    Three kinds of window have to be filtered out or selection picks nonsense:

    - **Slivers.** A window mostly on another display can overlap this one by a
      few pixels. It is "on" this monitor only in a technical sense, so require
      at least `min_on_monitor` of the window's own area to be here.
    - **Shell surfaces.** The desktop ("Program Manager") spans every monitor
      and would otherwise look like the biggest window available.
    - **Overlays.** Screen-recorder and driver overlays cover the whole display
      above everything else, and contain no readable content.
    """
    ignored = {p.strip().lower() for p in ignore_processes if p.strip()}
    console = own_console_window()
    monitor_area = max(1, (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]))
    ranked: list[WindowCandidate] = []

    for window in visible_windows(within=bounds):
        overlap = _overlap_area(window.rect, bounds)
        monitor_share = overlap / monitor_area
        self_share = overlap / window.area if window.area else 0.0

        def add(reason: str) -> None:
            ranked.append(WindowCandidate(window, reason, monitor_share, self_share))

        if console and window.hwnd == console:
            add("the companion's own console")
        elif window.process in ignored:
            add(f"{window.process} is in capture.ignore_processes")
        elif window_class(window.hwnd) in _SHELL_CLASSES:
            add(f"shell surface ({window_class(window.hwnd)})")
        elif self_share < min_on_monitor:
            add(f"only {self_share:.0%} of it is on this monitor")
        else:
            add("ok")

    return ranked


def pick_target_window(
    bounds: tuple[int, int, int, int],
    ignore_processes: Sequence[str] = (),
    min_on_monitor: float = 0.5,
) -> WindowInfo | None:
    """Choose which window on the target monitor the companion should read.

    The window covering the largest share of the monitor wins -- whatever
    dominates the screen is what you are looking at. Focus is deliberately not
    considered: you type questions into a terminal on another display, so the
    focused window is routinely not the one you are asking about.

    Ties (two maximised windows both covering the whole monitor) break to the
    front-most, since `visible_windows` returns Z-order and `max` keeps the
    first of equal values.
    """
    viable = [c for c in target_candidates(bounds, ignore_processes, min_on_monitor)
              if c.viable]
    if not viable:
        return None
    return max(viable, key=lambda c: c.monitor_share).window


def own_console_window() -> int:
    """This process's console window, so the companion never reads itself."""
    try:
        return int(kernel32.GetConsoleWindow() or 0)
    except OSError:  # pragma: no cover
        return 0


def _overlap_area(
    a: tuple[int, int, int, int], b: tuple[int, int, int, int]
) -> int:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    return max(0, right - left) * max(0, bottom - top)


def child_windows(hwnd: int) -> list[int]:
    """Immediate and nested child window handles."""
    kids: list[int] = []

    def _callback(child: int, _lparam: int) -> bool:
        kids.append(child)
        return True

    user32.EnumChildWindows(hwnd, WNDENUMPROC(_callback), 0)
    return kids


def window_class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def wake_accessibility(hwnd: int) -> int:
    """Ask Chromium-based apps to switch their accessibility tree on.

    Chrome, Edge, Brave and Electron apps keep the tree off until something asks
    for it -- that's how they avoid paying for it when no screen reader is
    running. Sending WM_GETOBJECT/OBJID_CLIENT to the render widget is the
    signal they listen for. It costs nothing on non-Chromium windows, which
    simply ignore the message.

    Returns the number of render widgets nudged. The tree populates
    asynchronously, so callers should retry shortly after.
    """
    nudged = 0
    for child in child_windows(hwnd):
        if window_class(child) != "Chrome_RenderWidgetHostHWND":
            continue
        result = ctypes.c_size_t()
        # SendMessageTimeout, not SendMessage: a hung renderer must not hang us.
        user32.SendMessageTimeoutW(
            child, WM_GETOBJECT, 0, OBJID_CLIENT,
            SMTO_ABORTIFHUNG, 1000, ctypes.byref(result),
        )
        nudged += 1
    return nudged
