"""Choosing the microphone.

The user has several microphones and wanted to pick the right one. Windows lists
each once per audio API, and sounddevice refuses a name matching more than one,
so the choice is saved by name and turned into a device index at every
recording: through MME, like the default microphone, when its 31-character names
tell the microphones apart; through DirectSound when they don't. Only those two
open at 16 kHz, measured on this machine. An unplugged choice falls back to the
default, and the window says so once.

The device table below is this machine's, as PortAudio listed it on 15 Sep 2026.
"""

import os
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import sounddevice as real_sd
import yaml
from PySide6.QtWidgets import QApplication

from core.config import AppConfig
from core.logging import setup_logging
from core.settings import SETTINGS, current_values, overlay, read, write
from modules.ui.app import CompanionApp
from modules.ui.settings_dialog import SettingsDialog
from modules.voice import devices
from modules.voice.microphone import Recorder

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


APIS = [{"name": "MME"}, {"name": "Windows DirectSound"}, {"name": "Windows WASAPI"},
        {"name": "Windows WDM-KS"}]


def table(*rows):
    return [{"name": name, "hostapi": api, "max_input_channels": inputs} for name, api, inputs in rows]


def hostapis(index=None):
    return APIS if index is None else APIS[index]


USB, LINE_IN = "Microphone (USB Audio)", "Line In (Onboard Audio)"
STUDIO, STUDIO_MME = "Analogue 1 + 2 (Studio USB Audio Interface)", "Analogue 1 + 2 (Studio USB Audi"
MACHINE = table(
    ("Microsoft Sound Mapper - Input", 0, 2), (USB, 0, 2), (LINE_IN, 0, 2), (STUDIO_MME, 0, 2),
    ("Microsoft Sound Mapper - Output", 0, 0), ("Speakers (Onboard Audio)", 0, 0),
    ("Primary Sound Capture Driver", 1, 2), (USB, 1, 2), (LINE_IN, 1, 2), (STUDIO, 1, 2),
    ("Primary Sound Driver", 1, 0),
    (LINE_IN, 2, 2), (USB, 2, 2), (STUDIO, 2, 2),
    ("Stereo Mix (Onboard HD Audio Stereo input)", 3, 2), ("Microphone (Onboard HD Audio Mic input)", 3, 2),
)


def at(name, api):
    return next(i for i, d in enumerate(MACHINE) if d["name"] == name and APIS[d["hostapi"]]["name"] == api)


listed = devices.input_devices(MACHINE, APIS)

print("the microphones, each once")

names = devices.microphone_names(listed)
check("each microphone once, by its whole name, without Windows' 'default' stand-ins",
      names == [USB, LINE_IN, STUDIO], str(names))
check("...and none only WASAPI or WDM-KS list, which refuse 16 kHz", not any("Onboard HD" in n for n in names))
check("with no DirectSound, MME's names are listed",
      devices.microphone_names(devices.input_devices(MACHINE[:6], APIS)) == [USB, LINE_IN, STUDIO_MME])

print("\na name, turned into the device to open")

real_query, real_hostapis = real_sd.query_devices, real_sd.query_hostapis
real_sd.query_devices, real_sd.query_hostapis = lambda *a, **k: MACHINE, hostapis
try:
    real_sd._get_device_id(USB, "input", raise_on_error=True)
    refused = ""
except ValueError as exc:
    refused = str(exc)
finally:
    real_sd.query_devices, real_sd.query_hostapis = real_query, real_hostapis
check("sounddevice itself refuses the plain name -- why this exists", "Multiple" in refused)
check("a microphone opens through MME, like the default", devices.resolve(USB, listed) == at(USB, "MME"))
check("...even when MME cut its name at 31 characters",
      devices.resolve(STUDIO, listed) == at(STUDIO_MME, "MME"))
twins = devices.input_devices(table(
    ("USB Audio Device Microphone Arr", 0, 1), ("USB Audio Device Microphone Arr", 0, 1),
    ("USB Audio Device Microphone Array (1)", 1, 1), ("USB Audio Device Microphone Array (2)", 1, 1)), APIS)
check("two MME can't tell apart open through DirectSound, each its own",
      [devices.resolve(n, twins) for n in devices.microphone_names(twins)] == [2, 3])
check("the system default and an unplugged microphone both give None",
      devices.resolve(None, listed) is None and devices.resolve("", listed) is None
      and devices.resolve("Desk Mic (USB)", listed) is None)
check("part of a name typed into config.yaml works when it names one microphone",
      devices.resolve("studio", listed) == at(STUDIO_MME, "MME") and devices.resolve("i", listed) is None)
check("an index typed into config.yaml is kept", devices.resolve(3, listed) == 3 and devices.resolve("3", listed) == 3)

print("\nthe recorder")

opened = []


class FakeStream:
    def __init__(self, **kwargs):
        opened.append(kwargs["device"])

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass


fake_sd = types.ModuleType("sounddevice")
fake_sd.InputStream, fake_sd.query_devices, fake_sd.query_hostapis = FakeStream, lambda: MACHINE, hostapis
sys.modules["sounddevice"] = fake_sd


def record(recorder):
    recorder.start()
    recorder.stop()
    return opened[-1]


recorder = Recorder(device=USB)
check("the chosen microphone is the one opened", record(recorder) == at(USB, "MME") and recorder.missing is None,
      str(opened))
recorder.device = STUDIO
check("a choice changed between recordings is used by the next one", record(recorder) == at(STUDIO_MME, "MME"))
recorder.device = "Desk Mic (USB)"
check("an unplugged one falls back to the default, and says which was missing",
      record(recorder) is None and recorder.missing == "Desk Mic (USB)")
recorder.device = None
check("the system default opens the default", record(recorder) is None and recorder.missing is None)
check("the page's list: System default first, then each microphone",
      devices.microphone_choices() == [("", "System default"), (USB, USB), (LINE_IN, LINE_IN),
                                       (STUDIO, STUDIO)], str(devices.microphone_choices()))


def broken():
    raise RuntimeError("PortAudio not initialised")


fake_sd.query_devices = broken
check("...only the default when devices can't be listed", devices.microphone_choices() == [("", "System default")])
recorder.device = USB
check("...and a recording still opens, on the default", record(recorder) is None and recorder.missing == USB)
sys.modules["sounddevice"] = real_sd

print("\nthe setting")

cfg = AppConfig.load(CONFIG_PATH)
entry = next((s for s in SETTINGS if s.key == "speech.input_device"), None)
check("Microphone is on the Voice tab, taking effect without a restart",
      entry is not None and entry.section == "Voice" and entry.kind == "microphone" and entry.live)
base = AppConfig.load(CONFIG_PATH)
if entry is not None:
    check("the default reads as '' on the page", cfg.speech.input_device is None and read(cfg, entry) == "")
    write(cfg, entry, USB)
    check("a name is kept as a name, and saved", cfg.speech.input_device == USB
          and overlay(cfg, base) == {"speech": {"input_device": USB}}, str(overlay(cfg, base)))
    write(cfg, entry, "")
    check("System default is saved as nothing", cfg.speech.input_device is None and overlay(cfg, base) == {})

qt = QApplication.instance() or QApplication(sys.argv)
choices = [("", "System default"), (USB, USB), (STUDIO, STUDIO)]
dialog = SettingsDialog(cfg, defaults=current_values(cfg), monitors=[(1, "Monitor 1")], microphones=choices)
box = dialog._widgets.get("speech.input_device")
check("the page lists System default and each microphone",
      box is not None and [box.itemText(i) for i in range(box.count())] == ["System default", USB, STUDIO])
if box is not None:
    check("...showing the default",
          box.currentText() == "System default" and dialog.values()["speech.input_device"] == "")
    box.setCurrentIndex(2)
    check("choosing one gives its name", dialog.values()["speech.input_device"] == STUDIO)
    dialog.set_values({"speech.input_device": "Desk Mic (USB)"})
    check("a saved microphone that is unplugged shows as not connected, and is kept",
          box.currentText() == "Desk Mic (USB) (not connected)" and dialog.values()["speech.input_device"] == "Desk Mic (USB)")

print("\nthe running app")

restarts = []
talk = SimpleNamespace(recorder=SimpleNamespace(device=None))
listener = SimpleNamespace(recorder=SimpleNamespace(device=None), is_listening=True,
                           stop=lambda: restarts.append("stop"), start=lambda: restarts.append("start"))
app_cfg = AppConfig.load(CONFIG_PATH)
app_cfg.speech.input_device = USB
CompanionApp._use_microphone(SimpleNamespace(config=app_cfg, talk=talk, listener=listener))
check("a new choice reaches push-to-talk and hands-free listening",
      talk.recorder.device == USB and listener.recorder.device == USB)
check("...and listening that is on reopens on it straight away", restarts == ["stop", "start"])
listener.is_listening, restarts[:] = False, []
CompanionApp._use_microphone(SimpleNamespace(config=app_cfg, talk=talk, listener=listener))
check("listening that is off stays off", restarts == [])

settings_dir = Path(tempfile.mkdtemp(prefix="companion-microphone-"))
app_cfg = AppConfig.load(CONFIG_PATH)
app_base = AppConfig.load(CONFIG_PATH)
app_cfg.root = app_base.root = settings_dir
switched = []
fake_app = SimpleNamespace(
    config=app_cfg, listener=None, ratings=None, _quiet_action=None,
    _use_microphone=lambda: switched.append(1),
    worker=SimpleNamespace(apply_settings=lambda: None),
    window=SimpleNamespace(add_notice=lambda text, colour="": None, apply_settings=lambda redock=False: None),
)
values = current_values(app_cfg)
values["speech.input_device"] = STUDIO
CompanionApp._apply_settings(fake_app, values, app_base)
saved = yaml.safe_load((settings_dir / app_cfg.settings_file).read_text(encoding="utf-8")) or {}
check("saving the page switches the microphone and keeps the choice",
      switched == [1] and saved.get("speech", {}).get("input_device") == STUDIO, str(saved))
values["proactive.cooldown_s"] = app_cfg.proactive.cooldown_s + 5
CompanionApp._apply_settings(fake_app, values, app_base)
check("...other changes don't reopen it", switched == [1])

notices = []
fake_app = SimpleNamespace(_missing_microphone=None,
                           window=SimpleNamespace(add_notice=lambda text, colour="": notices.append(text)))
gone = SimpleNamespace(missing="Desk Mic (USB)")
CompanionApp._tell_missing_microphone(fake_app, gone)
CompanionApp._tell_missing_microphone(fake_app, gone)
check("an unplugged microphone is told once, not at every recording",
      len(notices) == 1 and "Desk Mic (USB)" in notices[0] and "Settings" in notices[0], str(notices))
CompanionApp._tell_missing_microphone(fake_app, SimpleNamespace(missing=None))
CompanionApp._tell_missing_microphone(fake_app, gone)
check("...and told again if it goes missing after being found", len(notices) == 2)

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
