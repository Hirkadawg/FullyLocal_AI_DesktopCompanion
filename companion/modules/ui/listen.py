"""Hands-free listening: the microphone stays on and each thing said is asked.

The user wanted a conversation with no key and no send button: turn
listening on, talk, pause, get an answer, talk again. The talk key and the mic
button stay as they are.

The microphone is open for as long as listening is on -- that is the point --
and closed the moment it is switched off, so Windows' microphone indicator
still tells the truth. Nothing is recorded to disk; what isn't speech is thrown
away within about a second.

What it must not do is answer itself. When the microphone can hear the speakers
(`speech.mic_hears_speakers`), listening pauses while the companion talks, for
`echo_tail_s` after, and while other sound plays. When it can't -- headphones, or
as measured on this machine -- it keeps listening, and starting to speak stops
the companion, the way talking over a person does.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from core.config import AppConfig
from core.logging import get_logger
from modules.voice.microphone import Recorder
from modules.voice.vad import STARTED, UtteranceDetector, silero_segments

log = get_logger(__name__)


class HandsFreeListener(QObject):
    #: They started talking.
    started = Signal()
    #: They finished saying something. Payload: mono float32 audio at 16 kHz.
    utterance = Signal(object)
    #: The microphone couldn't be opened.
    failed = Signal(str)

    #: Silero takes 3-8 ms a call, so checking about seven times a second costs
    #: little and still notices a pause promptly.
    POLL_MS = 150

    def __init__(
        self,
        config: AppConfig,
        suppressed: Callable[[], bool] = lambda: False,
        segments: Callable = silero_segments,
        recorder=None,
    ) -> None:
        super().__init__()
        self.config = config
        #: Asked every poll: is this a moment the microphone must not be heard?
        self.suppressed = suppressed
        s = config.speech
        # The recorder is drained every poll, so its own cap never applies;
        # the detector's max_seconds is the limit that matters.
        self.recorder = recorder or Recorder(
            sample_rate=16000, device=s.input_device, max_seconds=60.0
        )
        self.detector = UtteranceDetector(
            segments,
            pause_s=s.pause_s,
            min_speech_s=max(s.min_seconds, 0.4),
            max_seconds=s.max_seconds,
        )
        self._timer = QTimer(self)
        self._timer.setInterval(self.POLL_MS)
        self._timer.timeout.connect(self.poll)

    @property
    def is_listening(self) -> bool:
        return self.recorder.is_recording

    def start(self) -> None:
        if self.is_listening:
            return
        try:
            self.recorder.start()
        except Exception as exc:
            log.warning("could not start listening: %s", exc)
            self.failed.emit(str(exc))
            return
        self.detector.reset()
        self._timer.start()
        log.info("hands-free listening on")

    def stop(self) -> None:
        self._timer.stop()
        if self.recorder.is_recording:
            self.recorder.stop()
            log.info("hands-free listening off")
        self.detector.reset()

    def poll(self) -> None:
        samples = self.recorder.drain()
        if self.suppressed():
            # Thrown away, not held: what the microphone hears now is the
            # companion, a video, or a push-to-talk recording.
            self.detector.reset()
            return
        self.detector.feed(samples)
        event = self.detector.step()
        if isinstance(event, str) and event == STARTED:
            self.started.emit()
        elif event is not None:
            log.debug("heard %.1fs hands-free", len(event) / 16000)
            self.utterance.emit(event)
