"""Which microphones Windows has, by name, and which device to open for one.

PortAudio lists each microphone once per Windows audio API -- MME, DirectSound,
WASAPI, WDM-KS -- so one name appears up to four times, and sounddevice refuses
a name matching more than one ("Multiple input devices found"). Measured on this
machine (15 Sep 2026): only MME and DirectSound open at the 16 kHz the speech
model wants; WASAPI and WDM-KS refuse it ("Invalid sample rate"). MME is what the
system-default microphone already uses, but it cuts names at 31 characters
("Analogue 1 + 2 (Studio USB Audi"). So names come whole from DirectSound, and a
chosen name opens through MME like the default microphone does -- through
DirectSound only when MME's shortened names can't tell two apart.

A name is saved, never an index: indexes move when a device is plugged in.
"""

from __future__ import annotations

from dataclasses import dataclass

MME = "MME"
DIRECTSOUND = "Windows DirectSound"
#: MME's limit on a device name, without its terminating null.
MME_NAME_CHARS = 31
#: "Whatever Windows is set to", listed as if they were devices. The settings
#: page's "System default" is that choice.
_MAPPERS = frozenset({"Microsoft Sound Mapper - Input", "Primary Sound Capture Driver"})


@dataclass(frozen=True)
class InputDevice:
    index: int
    name: str
    api: str


def input_devices(devices=None, hostapis=None) -> list[InputDevice]:
    """Every input device PortAudio lists, with its API's name.

    Pass `devices` and `hostapis` (as sounddevice returns them) to read a table
    other than this machine's.
    """
    if devices is None:
        import sounddevice as sd

        devices, hostapis = sd.query_devices(), sd.query_hostapis()
    return [
        InputDevice(index, device["name"], hostapis[device["hostapi"]]["name"])
        for index, device in enumerate(devices)
        if device["max_input_channels"] > 0
    ]


def microphone_names(devices: list[InputDevice]) -> list[str]:
    """Each microphone once, by its whole name, in Windows' order."""
    listed = [d for d in devices if d.api == DIRECTSOUND] or [d for d in devices if d.api == MME]
    names: list[str] = []
    for device in listed:
        if device.name not in _MAPPERS and device.name not in names:
            names.append(device.name)
    return names


def _mme_match(name: str, device: InputDevice) -> bool:
    """Whether MME's name for a device is this whole name, or it cut short."""
    return device.name == name or (
        len(device.name) >= MME_NAME_CHARS and name.startswith(device.name)
    )


def resolve(name: str | int | None, devices: list[InputDevice]) -> int | None:
    """The device index to open for a saved choice; None for the system default
    or for a microphone that isn't connected."""
    if name is None or name == "":
        return None
    if isinstance(name, int):
        return name
    if name.isdigit():  # an index typed into config.yaml by hand
        return int(name)
    mme = [d for d in devices if d.api == MME and d.name not in _MAPPERS and _mme_match(name, d)]
    if len(mme) == 1:
        return mme[0].index
    for device in devices:
        if device.api in (DIRECTSOUND, MME) and device.name == name:
            return device.index
    # Part of a name typed into config.yaml by hand ("focusrite"): fine when it
    # names one microphone.
    partial = [n for n in microphone_names(devices) if name.lower() in n.lower()]
    if len(partial) == 1 and partial[0] != name:
        return resolve(partial[0], devices)
    return None


def microphone_choices() -> list[tuple[str, str]]:
    """(saved value, label) for the settings page: "System default" first."""
    choices = [("", "System default")]
    try:
        names = microphone_names(input_devices())
    except Exception:  # no PortAudio, no devices: only the default to offer
        names = []
    return choices + [(name, name) for name in names]
