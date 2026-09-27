"""What is playing, as Windows itself reports it: title, artist, app, playing or paused.

Browsers and music players publish the current track to Windows' media controls
-- the same thing the volume flyout shows. Reading it is exact, where guessing a
song from screen text, a downscaled screenshot or transcribed lyrics is not: asked
"what song is playing?" with the title missing from the screen text, the model
named the song it had mentioned earlier 5 times in 5, even with the new song's
lyrics marked "just now".

No package is needed: PowerShell 5.1, part of Windows, can call the WinRT API.
Measured from Python: ~290 ms a call (PowerShell starting), Unicode titles intact
(Night Bus), so it is asked only for questions about music. Nothing leaves the
machine; the titles go only into the local model's prompt.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from core.logging import get_logger

log = get_logger(__name__)

_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
  $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
  $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
  $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime] | Out-Null
$manager = Await ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$out = @()
foreach ($s in $manager.GetSessions()) {
  $p = Await ($s.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
  $out += [pscustomobject]@{ app = $s.SourceAppUserModelId; status = [string]$s.GetPlaybackInfo().PlaybackStatus;
                             title = $p.Title; artist = $p.Artist; album = $p.AlbumTitle }
}
ConvertTo-Json -InputObject @($out) -Compress
"""


@dataclass
class MediaSession:
    app: str
    status: str  # "Playing", "Paused", "Stopped"...
    title: str
    artist: str = ""
    album: str = ""

    @property
    def playing(self) -> bool:
        return self.status.lower() == "playing"


def app_name(app_id: str) -> str:
    """"brave.exe" -> "Brave"; a Store app id "SpotifyAB.SpotifyMusic_x!Spotify" -> "Spotify"."""
    name = (app_id or "").split("!")[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name[:1].upper() + name[1:] if name else "an app"


def parse_sessions(text: str) -> list[MediaSession]:
    """Sessions from the script's JSON; anything unreadable is skipped."""
    try:
        data = json.loads(text or "[]")
    except ValueError:
        return []
    if isinstance(data, dict):  # PowerShell unwraps a one-item array
        data = [data]
    sessions = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict) or not (item.get("title") or "").strip():
            continue
        sessions.append(MediaSession(
            app=app_name(str(item.get("app") or "")), status=str(item.get("status") or ""),
            title=str(item["title"]).strip(), artist=str(item.get("artist") or "").strip(),
            album=str(item.get("album") or "").strip(),
        ))
    return sessions


def now_playing(timeout_s: float = 3.0) -> list[MediaSession]:
    """What the media controls report now; [] if nothing, or if asking failed."""
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SCRIPT],
            capture_output=True, timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired):
        log.warning("could not ask Windows what is playing", exc_info=True)
        return []
    if done.returncode != 0:
        log.warning("asking Windows what is playing failed: %s",
                    done.stderr.decode("utf-8", errors="replace")[:200])
        return []
    return parse_sessions(done.stdout.decode("utf-8", errors="replace").strip())


def describe(sessions: list[MediaSession]) -> str:
    """The NOW PLAYING block for a question, playing tracks first."""
    if not sessions:
        return ("[NOW PLAYING — no app reports a track to Windows' media controls right now. "
                "If something is audible, it is from an app that doesn't report it.]\n\n")
    rows = []
    for session in sorted(sessions, key=lambda s: not s.playing):
        by = f" by {session.artist}" if session.artist else ""
        album = f", from {session.album}" if session.album else ""
        rows.append(f'- {"Playing" if session.playing else session.status or "Paused"}: '
                    f'"{session.title}"{by}{album} ({session.app})')
    return ("[NOW PLAYING — exact, from Windows' media controls; this outranks lyrics, the screen "
            "and anything said earlier]\n" + "\n".join(rows) + "\n[/NOW PLAYING]\n\n")
