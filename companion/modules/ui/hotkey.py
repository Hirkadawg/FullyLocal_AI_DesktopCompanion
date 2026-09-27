"""System-wide hotkey via Win32 RegisterHotKey.

Registered against the companion's own window so the message arrives even while
that window is hidden -- which is the entire point, since the hotkey is how you
summon it back.

RegisterHotKey rather than a keyboard hook: it asks Windows to reserve one
specific combination instead of watching every keystroke. That means no
administrator rights, no antivirus suspicion, and no possibility of the
companion observing anything you type elsewhere.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from core.logging import get_logger

log = get_logger(__name__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.RegisterHotKey.argtypes = [
    wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short

WM_HOTKEY = 0x0312
HOTKEY_TOGGLE = 0xC0DE
HOTKEY_TALK = 0xC0DF
HOTKEY_REMARK = 0xC0E0
HOTKEY_MOVE_AVATAR = 0xC0E1

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_MODIFIERS = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
}

#: Named keys that aren't a single character.
_KEYS = {
    "space": 0x20,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "esc": 0x1B,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "backquote": 0xC0,
    "`": 0xC0,
    **{f"f{n}": 0x6F + n for n in range(1, 13)},
}


class HotkeyError(Exception):
    """The combination could not be reserved."""


def parse(spec: str) -> tuple[int, int]:
    """Turn "ctrl+alt+space" into (modifier mask, virtual key code)."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise HotkeyError(f"empty hotkey specification: {spec!r}")

    modifiers = 0
    key: int | None = None
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
        elif part in _KEYS:
            key = _KEYS[part]
        elif len(part) == 1:
            key = ord(part.upper())
        else:
            raise HotkeyError(f"unrecognised key {part!r} in hotkey {spec!r}")

    if key is None:
        raise HotkeyError(f"hotkey {spec!r} has modifiers but no key")
    if not modifiers:
        # Without a modifier the combination would swallow a bare key globally.
        raise HotkeyError(f"hotkey {spec!r} needs at least one modifier")
    return modifiers | MOD_NOREPEAT, key


class GlobalHotkey:
    """Reserves one system-wide key combination for a window."""

    def __init__(self, hwnd: int, spec: str, hotkey_id: int = HOTKEY_TOGGLE) -> None:
        self.hwnd = hwnd
        self.spec = spec
        self.hotkey_id = hotkey_id
        self.registered = False
        self.modifiers, self.vk = parse(spec)
        if not user32.RegisterHotKey(hwnd, hotkey_id, self.modifiers, self.vk):
            error = ctypes.get_last_error()
            hint = (
                " -- another application already owns it"
                if error == 1409  # ERROR_HOTKEY_ALREADY_REGISTERED
                else ""
            )
            raise HotkeyError(f"could not register {spec!r} (error {error}){hint}")
        self.registered = True
        log.debug("registered global hotkey %s (id %s)", spec, hotkey_id)

    def is_held(self) -> bool:
        """Whether the combination's main key is currently down.

        RegisterHotKey only reports the press, but push-to-talk also needs the
        release. Polling this one key while recording is enough, and unlike a
        keyboard hook it cannot observe anything else being typed.
        """
        return bool(user32.GetAsyncKeyState(self.vk) & 0x8000)

    def release(self) -> None:
        if self.registered:
            user32.UnregisterHotKey(self.hwnd, self.hotkey_id)
            self.registered = False
