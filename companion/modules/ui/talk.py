"""Push-to-talk: hold a key, speak, release, and the question is asked.

Hold rather than toggle because it makes the boundaries unambiguous. There is no
state to lose track of, no "is it still listening?", and the microphone is only
open while your finger is physically on a key.

Echo suppression comes free from that shape. Pressing the talk key stops any
answer in progress, so the companion is never speaking while the microphone is
open and can't transcribe itself. Interrupting and asking something new are the
same gesture, which is also how it works with a person.

Release detection polls one key with GetAsyncKeyState, and only while recording.
RegisterHotKey reports the press but not the release, and a keyboard hook -- the
usual answer -- would mean watching every keystroke on the machine. Polling a
single already-registered key sees nothing else.

The window's mic button records the same way without a key held: click to
start, click again to send. The boundary is still one the user
makes, and the microphone is still closed the moment they send. The click stops
any answer in progress, like the key, and remarks stay quiet while recording;
a timer going off mid-recording is the one voice it could still hear.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QTimer, Signal

from core.config import AppConfig
from core.logging import get_logger
from modules.ui.hotkey import GlobalHotkey
from modules.voice.microphone import Recorder

log = get_logger(__name__)


class PushToTalk(QObject):
    """Records while the talk key is held, then hands over the audio."""

    #: Recording started; the UI should say so.
    started = Signal()
    #: Recording finished. Payload is mono float32 audio at the engine's rate.
    captured = Signal(object)
    #: Something went wrong (no microphone, etc).
    failed = Signal(str)
    #: Mic button only: everything recorded so far, so what has been heard can
    #: be shown before it is sent. The key sends straight away and needs none.
    preview = Signal(object)

    #: How often to check whether the key is still down. 40 ms is well under
    #: human release time and costs nothing.
    POLL_MS = 40

    def __init__(self, config: AppConfig, hotkey: GlobalHotkey | None) -> None:
        super().__init__()
        self.config = config
        #: None when the talk key couldn't be registered; the mic button still works.
        self.hotkey = hotkey
        #: True while recording because the key is held; False when the mic
        #: button started it, so releasing a key must not end it.
        self.held = False
        self.clock = time.monotonic
        self._last_preview = 0.0
        self.recorder = Recorder(
            sample_rate=16000,
            device=config.speech.input_device,
            max_seconds=config.speech.max_seconds,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self._poll)

    @property
    def is_recording(self) -> bool:
        return self.recorder.is_recording

    def pressed(self) -> None:
        """Talk key went down."""
        if self.hotkey is None or self.recorder.is_recording:
            return
        self.held = True
        self._start()

    def toggle(self) -> None:
        """Mic button: start recording, or finish and send what was recorded."""
        if self.recorder.is_recording:
            self._finish()
            return
        self.held = False
        self._start()

    def _start(self) -> None:
        try:
            self.recorder.start()
        except Exception as exc:
            log.warning("could not start recording: %s", exc)
            self.failed.emit(str(exc))
            return
        self._last_preview = self.clock()
        self._timer.start()
        self.started.emit()

    def _poll(self) -> None:
        if self.recorder.seconds_recorded >= self.config.speech.max_seconds:
            log.debug("hit max_seconds, ending capture")
            self._finish()
            return
        if self.held:
            if self.hotkey is None or not self.hotkey.is_held():
                self._finish()
            return
        interval = self.config.speech.preview_interval_s
        if interval > 0 and self.clock() - self._last_preview >= interval:
            self._last_preview = self.clock()
            self.preview.emit(self.recorder.snapshot())

    def _finish(self) -> None:
        self._timer.stop()
        audio = self.recorder.stop()
        self.captured.emit(audio)

    def stop(self) -> None:
        self._timer.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
