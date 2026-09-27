"""Every microphone on this machine opens by its name.

Opens the system default and each microphone the settings page lists for a
third of a second, through the Recorder push-to-talk uses, and checks audio
arrived. Nothing is kept: only the sample count and peak level are printed.
Needs the audio devices, so it isn't in --fast.
"""

import sys
import time

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

import sounddevice as sd

from core.logging import setup_logging
from modules.voice import devices
from modules.voice.microphone import Recorder

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


listed = devices.input_devices()
names = devices.microphone_names(listed)
print(f"  microphones listed: {names}")
check("this machine lists at least one microphone", bool(names))

for name in [None] + names:
    recorder = Recorder(device=name)
    problem = ""
    try:
        recorder.start()
        time.sleep(0.35)
        audio = recorder.stop()
    except Exception as exc:
        audio, problem = [], str(exc).splitlines()[0]
    peak = float(abs(audio).max()) if len(audio) else 0.0
    check(f"{name or 'System default'}: opens and delivers audio",
          len(audio) > 1000 and recorder.missing is None,
          problem or f"device {devices.resolve(name, listed)}, {len(audio)} samples, peak {peak:.3f}")

for name in names:
    try:
        sd._get_device_id(name, "input", raise_on_error=True)
        plain = "accepted"
    except ValueError:
        plain = "refused: several devices share the name"
    print(f"    sounddevice given only the name {name!r}: {plain}")

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
