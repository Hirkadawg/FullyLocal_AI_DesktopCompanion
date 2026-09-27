"""Everything the user does has to reach the orchestrator, not just typing.

Found while checking an outside design review against the code: only a TYPED
question called `note_user_message()`. A spoken one went transcribe() ->
_answer() and never said anything, so after a voice exchange there was no quiet
period and a remark could start the moment the spoken answer ended. Holding the
talk key didn't count either -- a remark could be composed mid-sentence and land
in the recording -- and neither did pressing Esc to stop the companion talking,
which is about the clearest "not now" a user can give.
"""

import inspect
import json
import sys

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import numpy as np
from PySide6.QtCore import QCoreApplication

from core.attention import AttentionPolicy
from core.config import AppConfig
from core.logging import setup_logging
from core.orchestrator import Orchestrator
from core.types import ScreenContext
from modules.ui.app import CompanionApp
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
qt = QCoreApplication.instance() or QCoreApplication(sys.argv)
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class FakeLLM:
    def chat(self, messages, json_schema=None, **kwargs):
        if json_schema is None:
            yield "reading about gears"
        else:
            yield json.dumps({"say": "Those gears are tiny.", "why": "a detail worth a look"})

    def health_check(self):
        pass


class FakeSTT:
    last_language = "en"

    def __init__(self, text):
        self.text = text

    def transcribe(self, audio):
        return self.text


PAGE = ScreenContext(text="x " * 500, window_title="Gears - Wikipedia",
                     app_name="brave.exe", source="uia")
CHARS = len(PAGE.text)


def worker_with_orchestrator(heard="what is this article about?"):
    cfg = AppConfig.load(CONFIG_PATH)
    cfg.proactive.enabled = True
    worker = CompanionWorker(cfg)
    policy = AttentionPolicy(cooldown_s=0, quiet_after_user_s=45, min_chars=100)
    worker._attention = policy
    worker._orchestrator = Orchestrator(FakeLLM(), policy, min_time_on_page_s=0)
    worker._orchestrator.observe(PAGE)  # a page that is due for a remark
    worker._stt = FakeSTT(heard)
    answered = []
    worker._answer = answered.append  # the model is not what is under test
    return worker, policy, answered


AUDIO = np.zeros(16000, dtype=np.float32)

print("a spoken question")

worker, policy, answered = worker_with_orchestrator()
check("before it, a remark would be allowed", policy.reason_to_wait(text_chars=CHARS) is None,
      "sanity check: otherwise the next checks prove nothing")
worker._transcribe(AUDIO)
check("the question itself still gets answered",
      answered == ["what is this article about?"], str(answered))
check("afterwards a remark waits because the user just spoke",
      policy.reason_to_wait(text_chars=CHARS) == "user was just talking",
      str(policy.reason_to_wait(text_chars=CHARS)))
check("so the page on screen isn't remarked on straight after the answer",
      worker._orchestrator.poll() is None)

print("\nholding the talk key")

worker, policy, _ = worker_with_orchestrator()
worker.note_user_talking()
check("the quiet period starts as soon as they start talking",
      policy.reason_to_wait(text_chars=CHARS) == "user was just talking",
      str(policy.reason_to_wait(text_chars=CHARS)))
check("the talk key is wired to it",
      "note_user_talking" in inspect.getsource(CompanionApp._on_talk_started))

print("\npressing Esc to stop it")

worker, policy, _ = worker_with_orchestrator()
worker.cancel()
check("stopping it mid-sentence counts as the user acting",
      policy.reason_to_wait(text_chars=CHARS) == "user was just talking",
      str(policy.reason_to_wait(text_chars=CHARS)))
check("...so nothing is said straight after being stopped",
      worker._orchestrator.poll() is None)

print("\nwith proactive commentary switched off")

cfg = AppConfig.load(CONFIG_PATH)
cfg.proactive.enabled = False
plain = CompanionWorker(cfg)
try:
    plain.note_user_talking()
    plain.cancel()
    ok, detail = True, ""
except Exception as exc:
    ok, detail = False, f"{type(exc).__name__}: {exc}"
check("none of this needs an orchestrator to exist", ok, detail)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
