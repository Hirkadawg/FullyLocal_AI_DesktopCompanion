"""The mic button: record, see what was heard, send.

Push-to-talk records while a key is held and sends on release. The mic button
records without holding anything: one click opens the microphone, the input box
shows what has been heard so far, and "send" asks the full recording. Releasing
a key must not end a recording the button started, a late preview must not land
after sending, Esc throws the recording away, and the button works even when
the talk key couldn't be registered. No microphone is opened: the recorder is a
fake, and so is speech recognition.
"""

import os
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from modules.ui.app import CompanionApp
from modules.ui.talk import PushToTalk
from modules.ui.window import ChatWindow
from modules.ui.worker import CompanionWorker
from modules.voice.microphone import Recorder

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class FakeRecorder:
    def __init__(self):
        self.is_recording = False
        self.seconds_recorded = 0.0

    def start(self):
        self.is_recording = True

    def snapshot(self):
        return np.ones(8000, dtype=np.float32)

    def stop(self):
        self.is_recording = False
        return np.ones(16000, dtype=np.float32)


class FakeKey:
    def __init__(self):
        self.down = False

    def is_held(self):
        return self.down


qt = QApplication.instance() or QApplication(sys.argv)
cfg = AppConfig.load(CONFIG_PATH)
now = [100.0]


def talk_with(hotkey):
    talk = PushToTalk(cfg, hotkey)
    talk.recorder = FakeRecorder()
    talk.clock = lambda: now[0]
    events = []
    talk.started.connect(lambda: events.append("started"))
    talk.captured.connect(lambda audio: events.append(("captured", len(audio))))
    talk.preview.connect(lambda audio: events.append(("preview", len(audio))))
    return talk, events


print("click to start, click to send")

talk, events = talk_with(FakeKey())
talk.toggle()
check("a click opens the microphone", talk.is_recording and events == ["started"], str(events))
check("...in click mode, not held mode", talk.held is False)
talk._poll()
check("no key is held, and the recording carries on", talk.is_recording and len(events) == 1,
      str(events))
talk.toggle()
check("a second click closes it and sends the audio",
      not talk.is_recording and events[-1] == ("captured", 16000), str(events))

print("\nwhat has been heard is previewed while recording")

talk, events = talk_with(FakeKey())
talk.toggle()
interval = cfg.speech.preview_interval_s
now[0] += interval - 0.1
talk._poll()
check("not before the preview interval", not any(e[0] == "preview" for e in events[1:]), str(events))
now[0] += 0.2
talk._poll()
check(f"every {interval}s, the audio so far is handed over for a preview",
      events[-1] == ("preview", 8000), str(events))
talk._poll()
check("...once per interval", sum(1 for e in events[1:] if e[0] == "preview") == 1, str(events))
talk.toggle()

cfg.speech.preview_interval_s = 0
talk, events = talk_with(FakeKey())
talk.toggle()
now[0] += 60
talk._poll()
check("preview_interval_s 0: no previews", all(e == "started" for e in events), str(events))
cfg.speech.preview_interval_s = interval

rec = Recorder()
check("the real recorder's snapshot is empty before anything is heard", len(rec.snapshot()) == 0)
rec._callback(np.full((1600, 1), 0.5, dtype=np.float32), 1600, None, None)
check("...and returns what was captured, without stopping",
      len(rec.snapshot()) == 1600 and len(rec.snapshot()) == 1600)
check("...leaving it for stop() too", len(rec.stop()) == 1600)

print("\nholding the key still sends straight away, with no preview")

key = FakeKey()
talk, events = talk_with(key)
key.down = True
talk.pressed()
check("pressing the key records in held mode", talk.is_recording and talk.held)
now[0] += 10
talk._poll()
check("...no preview while it is held", talk.is_recording and len(events) == 1, str(events))
key.down = False
talk._poll()
check("...and releasing it sends", not talk.is_recording and events[-1][0] == "captured",
      str(events))

print("\nlimits")

talk, events = talk_with(FakeKey())
talk.toggle()
talk.recorder.seconds_recorded = cfg.speech.max_seconds
talk._poll()
check(f"a clicked recording still stops at max_seconds ({cfg.speech.max_seconds:.0f}s)",
      not talk.is_recording and events[-1][0] == "captured", str(events))

talk, events = talk_with(None)
talk.pressed()
check("without a registered talk key, a key press does nothing", not talk.is_recording)
talk.toggle()
check("...but the mic button still records", talk.is_recording)
talk._poll()
check("...and isn't ended by the missing key", talk.is_recording)

print("\nthe worker transcribes a preview, and never asks it")


class FakeSTT:
    def transcribe(self, audio):
        return "what is this article ab"


worker = CompanionWorker(cfg)
worker._stt = FakeSTT()
previews, asked = [], []
worker.previewed.connect(lambda session, text: previews.append((session, text)))
worker.heard.connect(asked.append)
worker._preview(7, np.ones(8000, dtype=np.float32))
check("a preview comes back with its session", previews == [(7, "what is this article ab")],
      str(previews))
check("...and nothing is asked", asked == [] and worker._queue.empty())
worker._stt = None
worker._preview(8, np.ones(10, dtype=np.float32))
check("without speech recognition it still answers, empty", previews[-1] == (8, ""))

print("\nthe window")

window = ChatWindow(cfg)
clicked = []
window.mic_clicked.connect(lambda: clicked.append(1))
check("the window has a mic button", hasattr(window, "mic_button"))
window.mic_button.click()
check("clicking it asks to toggle recording", clicked == [1])
window.set_recording(True)
check("while recording it offers to send",
      window.mic_button.isChecked() and "send" in window.mic_button.accessibleName(),
      window.mic_button.accessibleName())
window.set_recording(False)
check("...and back to mic after", window.mic_button.accessibleName() == "mic")

window.input.setText("a half-typed draft")
window.show_preview("")
check("starting a preview clears the box and says it is listening",
      window.input.text() == "" and "listening" in window.input.placeholderText())
window.show_preview("what is this")
check("the box shows what has been heard", window.input.text() == "what is this")
check("...and can't be sent with Enter meanwhile", not window.input.isEnabled())
window.end_preview()
check("afterwards the draft is back and the box works again",
      window.input.text() == "a half-typed draft" and window.input.isEnabled())

cfg.speech.mic_button = False
check("speech.mic_button off: no button", not hasattr(ChatWindow(cfg), "mic_button"))
cfg.speech.mic_button = True

print("\nthe app")

log = []
talk, events = talk_with(FakeKey())
fake = SimpleNamespace(
    config=cfg, talk=talk, _idle_status="ready", _preview_session=0, _preview_waiting=False,
    _tell_missing_microphone=lambda recorder: None,
    worker=SimpleNamespace(is_busy=lambda: True, cancel=lambda: log.append("cancel"),
                           note_user_talking=lambda: None,
                           preview=lambda audio, session: log.append(("preview", session)),
                           transcribe=lambda audio: log.append("transcribe")),
    window=SimpleNamespace(set_recording=lambda r: log.append(("rec", r)),
                           set_status=lambda s: None,
                           add_notice=lambda *a: log.append(("notice", a[0])),
                           show_preview=lambda t: log.append(("show", t)),
                           end_preview=lambda: log.append("end"),
                           isVisible=lambda: True, hide=lambda: log.append("hidden")),
)
fake._end_preview = lambda: CompanionApp._end_preview(fake)

CompanionApp._on_mic_clicked(fake)
check("clicking mic while it is answering stops the answer first",
      log[0] == "cancel" and talk.is_recording, str(log))
CompanionApp._on_talk_started(fake)
session = fake._preview_session
check("a clicked recording starts an empty preview", ("show", "") in log, str(log))

CompanionApp._on_talk_preview(fake, np.ones(8000))
CompanionApp._on_talk_preview(fake, np.ones(8000))
check("only one preview is transcribed at a time",
      log.count(("preview", session)) == 1, str(log))
CompanionApp._on_previewed(fake, session, "what is this")
check("a preview for this recording is shown", ("show", "what is this") in log, str(log))
CompanionApp._on_talk_preview(fake, np.ones(8000))
check("...after which the next one may go", log.count(("preview", session)) == 2, str(log))

CompanionApp._on_talk_captured(fake, np.ones(16000, dtype=np.float32))
check("sending ends the preview", "end" in log and fake._preview_session != session, str(log))
before = len(log)
CompanionApp._on_previewed(fake, session, "a late preview")
check("a preview that arrives after sending is not shown",
      ("show", "a late preview") not in log[before:], str(log[before:]))

talk.stop()  # _on_talk_captured was called directly above; end that recording
log.clear()
talk.toggle()
CompanionApp._on_escape(fake)
check("Esc throws a clicked recording away and ends the preview",
      not talk.is_recording and "end" in log and ("notice", "Recording discarded.") in log
      and "hidden" not in log and "cancel" not in log, str(log))

key = FakeKey()
talk, events = talk_with(key)
fake.talk = talk
key.down = True
talk.pressed()
CompanionApp._on_escape(fake)
check("Esc during hold-to-talk leaves the recording to the key", talk.is_recording)
log.clear()
CompanionApp._on_talk_preview(fake, np.ones(8000))
check("...and the key's recording is never previewed", log == [], str(log))

fake.talk = None
CompanionApp._on_mic_clicked(fake)
check("with speech unavailable, the button says so",
      any(isinstance(e, tuple) and e[0] == "notice" and "unavailable" in e[1] for e in log), str(log))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
