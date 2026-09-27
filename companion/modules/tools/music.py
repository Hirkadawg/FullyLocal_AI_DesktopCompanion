"""Key, tempo and chords of the music playing: "what key is this song in?"

Read-only, and offered only for questions about those things
(modules/tools/requests.py). The analysis is modules/audio/music.py, run on the
last `music.listen_seconds` of system audio -- on this machine, only when asked.

Loopback hears the companion's own voice too, so audio from before it last
spoke is left out; if too little music is left, it says so rather than
analysing its own speech.
"""

from __future__ import annotations

import time

from modules.audio.music import analyse, describe
from modules.tools.base import Tool

#: Its voice can still be in the buffer this long after it was last heard.
ECHO_TAIL_S = 1.0
#: Less than this isn't enough for a key or a tempo.
MIN_SECONDS = 6.0


class AnalyseMusic(Tool):
    name = "analyse_music"
    description = (
        "Analyse the music playing through the computer's speakers right now: its key, "
        "tempo (BPM) and chords. Use for questions like 'what key is this song in', "
        "'what are the chords' or 'what's the tempo'."
    )
    parameters = {"type": "object", "properties": {}}
    writes = False

    def __init__(self, config, audio=None, clock=time.time) -> None:
        # The config itself: the switches and the length can change in the
        # settings page while the app runs.
        self.config = config
        #: The AudioTranscriber, set by Companion; its capture holds the sound.
        self.audio = audio
        self.clock = clock

    def run(self, **_ignored) -> str:
        if not self.config.music.enabled:
            return "Music analysis is switched off."
        capture = getattr(self.audio, "capture", None)
        if not self.config.audio.enabled or capture is None:
            return ("Hearing what's playing on the computer is switched off, so there is "
                    "no music to analyse.")
        rate = capture.sample_rate
        audio = capture.recent(self.config.music.listen_seconds)
        since_voice = self.clock() - getattr(self.audio, "last_spoke_at", 0.0) - ECHO_TAIL_S
        if since_voice < len(audio) / rate:
            keep = int(max(0.0, since_voice) * rate)
            audio = audio[len(audio) - keep:] if keep else audio[:0]
            if len(audio) < MIN_SECONDS * rate:
                return ("Too little of the music was heard without the companion's own voice "
                        "over it. Ask again once it has played for a few more seconds.")
        if len(audio) < MIN_SECONDS * rate:
            return "Too little has played yet to analyse. Ask again in a few seconds."
        return describe(analyse(audio, rate), silence_rms=self.config.audio.silence_rms)
