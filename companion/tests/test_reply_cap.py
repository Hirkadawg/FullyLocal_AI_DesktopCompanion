"""A cap on reply length, and saving exactly what is sent to the model.

Ollama's log of real use, 5,076 requests: 99% of replies were under 335 tokens,
but 16 ran past 1,000 and 4 filled the whole context. Nothing capped a reply --
"count from 1 to 5000" ran 91 s and 8,161 tokens; capped at 1,024, 10 s. The
longest real answer measured, a detailed Turkish one, was 532.

The user also couldn't see why a reply made no sense, since every message is
wrapped before it reaches the model; with `llm.log_prompts` on, each call is
written out in full.
"""

import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import core.companion as companion_module
from core.config import AppConfig, LLMConfig, OllamaConfig
from core.logging import setup_logging
from core.settings import SETTINGS
from core.types import Message, ToolCall, ToolSpec
from modules.llm import prompt_log as prompt_log_module
from modules.llm.ollama_client import OllamaLLM
from modules.llm.prompt_log import PromptLog

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


class Warnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


warnings = Warnings()
for name in ("modules.llm.ollama_client", "modules.llm.prompt_log"):
    logger = logging.getLogger(name)
    logger.setLevel(logging.WARNING)  # the suite logs at ERROR; these warnings are what's checked
    logger.propagate = False
    logger.addHandler(warnings)


class FakeClient:
    """Ollama's chat, answering with the chunks given."""

    def __init__(self, parts):
        self.parts, self.sent = parts, None

    def chat(self, **kwargs):
        self.sent = kwargs
        return iter(self.parts) if kwargs["stream"] else self.parts[-1]


def chunk(text, done_reason=None, prompt=None, reply=None):
    part = {"message": {"role": "assistant", "content": text}}
    if done_reason:
        part.update(done=True, done_reason=done_reason, prompt_eval_count=prompt, eval_count=reply)
    return part


def model(parts, cap=1024, log=None, num_ctx=8192):
    llm = OllamaLLM(max_reply_tokens=cap, prompt_log=log, num_ctx=num_ctx)
    llm._client = FakeClient(parts)
    return llm


QUESTION = [Message(role="system", content="You are a desktop companion."), Message(role="user", content="Hi")]

print("the cap")

llm = model([chunk("Hello "), chunk("there.", "stop", 900, 3)])
text = "".join(llm.chat(QUESTION, stream=True))
check("the cap goes to Ollama as num_predict, beside the other options",
      llm._client.sent["options"].get("num_predict") == 1024 and llm._client.sent["options"].get("num_ctx") == 8192,
      str(llm._client.sent["options"]))
check("a reply that ends by itself is untouched, and warns of nothing", text == "Hello there." and not warnings.messages)
uncapped = model([chunk("Hi.", "stop", 10, 2)], cap=0)
"".join(uncapped.chat(QUESTION, stream=True))
check("with 0, no cap is sent", "num_predict" not in uncapped._client.sent["options"])

llm = model([chunk("1\n2\n"), chunk("3\n4", "length", 30, 1024)])
parts = list(llm.chat(QUESTION, stream=True))
check("a reply cut by the cap ends in '…', so it reads as cut", "".join(parts).endswith("4 …"), repr(parts))
check("...and says so in the log", any("1024-token cap" in m for m in warnings.messages), str(warnings.messages))
warnings.messages.clear()
remark = model([chunk('{"say": "Wild gears', "length", 900, 1024)])
raw = "".join(remark.chat(QUESTION, stream=False, json_schema={"type": "object"}))
check("JSON cut by the cap gets no '…' (code parses it, and drops what doesn't parse)", raw == '{"say": "Wild gears')
warnings.messages.clear()
"".join(model([chunk("Ok.", "stop", 7500, 2)]).chat(QUESTION, stream=True))
check("a prompt using 90% of the context is warned about (past it, Ollama drops the front)",
      any("7500 of the 8192" in m for m in warnings.messages), str(warnings.messages))
warnings.messages.clear()

print("\nsaving what is sent")

folder = Path(tempfile.mkdtemp(prefix="companion-prompts-"))
cfg = SimpleNamespace(root=folder, llm=SimpleNamespace(log_prompts=False, prompt_log_folder="prompts"))
log = PromptLog(cfg)
llm = model([chunk("Hello.", "stop", 900, 3)], log=log)
"".join(llm.chat(QUESTION, stream=True))
check("off: nothing is written", not (folder / "prompts").exists())

cfg.llm.log_prompts = True
IMAGE = b"\x89PNG-not-really-an-image-but-bytes" * 10
messages = [Message(role="system", content="You are a desktop companion. RULES."),
            Message(role="user", content="earlier question"),
            Message(role="assistant", content="earlier answer"),
            Message(role="user", content="[SCREEN TEXT]\nAntikythera\n[/SCREEN TEXT]\n\nQuestion: set a timer",
                    images=(IMAGE,))]
llm = model([chunk("", None), {"message": {"role": "assistant", "content": "",
                                           "tool_calls": [{"function": {"name": "set_timer",
                                                                        "arguments": {"minutes": 5}}}]}},
             chunk("Done.", "stop", 1234, 17)], log=log)
spec = ToolSpec(name="set_timer", description="Set a timer", parameters={"type": "object", "properties": {}})
calls = []
"".join(llm.chat(messages, stream=True, tools=[spec], collect_tool_calls=calls, temperature=0.3))
today = folder / "prompts" / f"prompts-{datetime.now():%Y-%m-%d}.txt"
written = today.read_text(encoding="utf-8") if today.is_file() else ""
check("switched on while running, it writes today's file", today.is_file(), str(list(folder.rglob("*"))))
check("...with the settings, the token counts and how it ended",
      "temperature 0.3" in written and "num_predict 1024" in written and "prompt 1234, reply 17" in written
      and "the reply ended" in written, written[:300])
check("...every message in order: system, history, the wrapped question, and the reply",
      all(part in written for part in ("--- system ---", "RULES.", "--- user ---", "earlier question",
                                       "--- assistant ---", "earlier answer", "[SCREEN TEXT]", "=== reply ===", "Done."))
      and written.index("earlier question") < written.index("[SCREEN TEXT]") < written.index("=== reply ==="))
check("...the tools offered and the calls made", "Tools offered: set_timer" in written
      and "set_timer({'minutes': 5})" in written)
check("...a screenshot noted, never saved", "[1 screenshot attached, not saved]" in written
      and "PNG-not-really" not in written)
"".join(model([chunk("Again.", "stop", 1300, 2)], log=log).chat(messages, stream=True))
again = today.read_text(encoding="utf-8")[len(written):]
check("the same system prompt again is referred to, not repeated",
      "RULES." not in again and "(the same system prompt as at" in again, again[:200])

stream = model([chunk("Part one. "), chunk("Part two.", "stop", 10, 4)], log=log).chat(QUESTION, stream=True)
next(stream)
stream.close()
last = today.read_text(encoding="utf-8").rsplit("====================", 1)[-1]
check("an answer cut off by the user is still saved, as far as it was shown, as stopped early",
      "stopped early (interrupted)" in last and last.rstrip().endswith("=== reply ===\nPart")
      and "Part two" not in last, last[-120:])

"".join(model([chunk("1 2 3", "length", 30, 1024)], log=log).chat(QUESTION, stream=True))
check("a reply cut by the cap says so in the file", "HIT THE LENGTH CAP" in today.read_text(encoding="utf-8"))

limit = prompt_log_module.MAX_FILE_BYTES
prompt_log_module.MAX_FILE_BYTES = today.stat().st_size
size = today.stat().st_size
"".join(model([chunk("More.", "stop", 10, 2)], log=log).chat(QUESTION, stream=True))
"".join(model([chunk("More.", "stop", 10, 2)], log=log).chat(QUESTION, stream=True))
check("a full day's file stops growing, and says so once",
      today.stat().st_size == size and sum("full" in m for m in warnings.messages) == 1, str(warnings.messages))
prompt_log_module.MAX_FILE_BYTES = limit


class Broken:
    def record(self, *args):
        raise OSError("disk full")


check("a log that can't be written never breaks the answer",
      "".join(model([chunk("Fine.", "stop", 10, 2)], log=Broken()).chat(QUESTION, stream=True)) == "Fine.")

print("\nwired into the app")

built = []


class Recorder:
    def __init__(self, **kwargs):
        built.append(kwargs)


real = companion_module.OllamaLLM
companion_module.OllamaLLM = Recorder
app_config = AppConfig.load(CONFIG_PATH)
app_config.audio.enabled = False
try:
    companion_module.build_companion(app_config, image_path=FIXTURE_IMAGE)
except Exception as exc:  # the recorder isn't a real model; only its arguments matter
    print(f"    (stopped after the model was built: {exc.__class__.__name__})")
companion_module.OllamaLLM = real
check("the app's model gets the configured cap and a prompt log reading the live config",
      bool(built) and built[0]["max_reply_tokens"] == 1024 and isinstance(built[0]["prompt_log"], PromptLog)
      and built[0]["prompt_log"]._config is app_config, str(built[0] if built else None))

print("\nthe settings")
check("the cap: 0 in code, 1024 in config.yaml, on the settings page (after a restart)",
      OllamaConfig().max_reply_tokens == 0 and AppConfig.load(CONFIG_PATH).ollama.max_reply_tokens == 1024
      and any(s.key == "ollama.max_reply_tokens" and s.kind == "int" and not s.live for s in SETTINGS))
check("saving prompts: off in code and in config.yaml (it holds the screen's text), on the settings page, live",
      LLMConfig().log_prompts is False and AppConfig.load(CONFIG_PATH, settings=False).llm.log_prompts is False
      and any(s.key == "llm.log_prompts" and s.kind == "bool" and s.live for s in SETTINGS)
      and any(s.key == "llm.prompt_log_folder" and s.kind == "dir" for s in SETTINGS))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
