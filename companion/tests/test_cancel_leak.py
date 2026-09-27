"""Regression: after an interruption, later questions must still be answered.

Reproduces the reported sequence exactly -- ask, interrupt with Esc, then ask
again by voice. The cancel flag was cleared only in ask(), so the spoken path
inherited it and every subsequent answer bailed out before emitting anything.
"""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
cfg = AppConfig.load(CONFIG_PATH)
cfg.ollama.warm_up = False
cfg.voice.enabled = False      # no audio device needed for this test
cfg.speech.warm_up = False

IMG = FIXTURE_IMAGE

failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


app = QApplication.instance() or QApplication(sys.argv)
worker = CompanionWorker(cfg, image_path=IMG)

state = {"ready": None}
chunks: list[str] = []
worker.ready.connect(lambda err: state.__setitem__("ready", err))
worker.chunk.connect(chunks.append)
worker.start()

deadline = time.time() + 120
while state["ready"] is None and time.time() < deadline:
    app.processEvents()
    time.sleep(0.02)

if state["ready"]:
    print(f"  worker failed to start: {state['ready']}")
    sys.exit(1)


def ask_and_collect(kind, payload, timeout=120):
    """Submit a question, wait for it to finish, return the answer text."""
    chunks.clear()
    done = {"v": False}
    conn = worker.finished_answer.connect(lambda: done.__setitem__("v", True))
    if kind == "typed":
        worker.ask(payload)
    else:
        worker.transcribe(payload)
    end = time.time() + timeout
    while not done["v"] and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    worker.finished_answer.disconnect(conn)
    return "".join(chunks).strip()


# 1. A typed question answers normally.
first = ask_and_collect("typed", "Say the single word BANANA and nothing else.")
check("first question answers", len(first) > 0, f"got {first[:40]!r}")

# 2. Interrupt, exactly as pressing Esc does.
worker.cancel()
time.sleep(0.2)
check("cancel flag is set after interrupting", worker._cancel.is_set())

# 3. A SPOKEN question after the interruption. This is what was broken:
#    transcribe() never cleared the flag, so the answer never appeared.
#    Piper-generated audio keeps it a real end-to-end path.
from modules.voice.tts.piper_tts import PiperTTS

tts = PiperTTS(voice="en_US-lessac-medium",
               voices_dir=VOICES_DIR)
pcm = tts.synthesize("Say the single word CHERRY and nothing else.")
duration = len(pcm) / tts.sample_rate
x_old = np.linspace(0, duration, len(pcm), endpoint=False)
x_new = np.linspace(0, duration, int(duration * 16000), endpoint=False)
audio = (np.interp(x_new, x_old, pcm.astype(np.float32)) / 32768.0).astype(np.float32)
tts.close()

spoken = ask_and_collect("spoken", audio)
check("spoken question after an interruption still answers",
      len(spoken) > 0, f"got {spoken[:60]!r}")

# 4. And a typed one after that, to be sure nothing is left latched.
third = ask_and_collect("typed", "Say the single word DAMSON and nothing else.")
check("typed question after that also answers", len(third) > 0, f"got {third[:40]!r}")

# 5. Interrupting mid-answer must still genuinely stop it. Wait for real output
#    first -- cancelling before anything is generated would pass trivially.
chunks.clear()
worker.ask("Count from one to fifty, writing each number as a full sentence.")
end = time.time() + 30
while len("".join(chunks)) < 40 and time.time() < end:
    app.processEvents()
    time.sleep(0.02)

during = len("".join(chunks))
check("generation was genuinely under way before cancelling", during >= 40,
      f"{during} chars")

worker.cancel()

# chunk is a queued cross-thread signal, so tokens emitted before the cancel
# may still be undelivered. Drain the event queue first, or they get counted as
# though they arrived after it.
for _ in range(40):  # 0.8s of draining
    app.processEvents()
    time.sleep(0.02)
at_cancel = len("".join(chunks))

for _ in range(100):  # 2s more: anything arriving now is genuinely new
    app.processEvents()
    time.sleep(0.02)
after = len("".join(chunks))
check("interrupting stops generation", after == at_cancel,
      f"{at_cancel} chars at cancel, {after} after")
check("stopped well short of counting to fifty", after < 2000, f"{after} chars")

worker.shutdown()
print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
