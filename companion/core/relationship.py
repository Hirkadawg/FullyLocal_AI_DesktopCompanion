"""Your level with the companion, grown by your votes.

The user's idea: every thumbs up or down helps shape the companion, so voting
should feel like it goes somewhere. The level grows each time enough votes have
been cast to make a noticeable difference -- in the user's example, 50 votes for
level 1, then 80 more for level 2.

How many votes really make a noticeable difference isn't known. Votes change no
model yet: a fine-tune on rated replies is F11's later half, and unmeasured. So
the steps are provisional, shown from the start as the user decided: 50, 80, then
30 more each level, since each further difference should need more examples than
the last. When training on N rated replies has been measured, the measured steps
replace STEPS, and PROVISIONAL goes False.

Every rated reply counts once, up or down: re-rating a reply replaces its rating
(core/ratings.py). A ratings reset starts the level over. Careless votes are not
guarded against, as decided -- they would teach a fine-tune carelessly, and the
level screen says so.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Votes for the first levels, in order; beyond these each step grows by STEP_GROWTH.
STEPS: tuple[int, ...] = (50, 80)
STEP_GROWTH = 30
#: The steps are a first guess until measured.
PROVISIONAL = True


@dataclass(frozen=True)
class Level:
    #: Levels reached, from 0.
    level: int
    #: Votes counted.
    votes: int
    #: Votes into the step towards the next level.
    into: int
    #: Votes that step needs.
    step: int

    @property
    def to_next(self) -> int:
        return self.step - self.into


def step_for(level: int) -> int:
    """Votes needed to go from `level` to the next."""
    if level < len(STEPS):
        return STEPS[level]
    return STEPS[-1] + STEP_GROWTH * (level - len(STEPS) + 1)


def level_for(votes: int) -> Level:
    """The level `votes` votes have reached, and the way to the next."""
    votes = max(0, int(votes))
    level = start = 0
    while votes >= start + step_for(level):
        start += step_for(level)
        level += 1
    return Level(level, votes, votes - start, step_for(level))


def count_votes(records) -> tuple[int, int]:
    """(up, down) among rating records, one per rated reply."""
    up = sum(1 for record in records if record.get("rating") == "up")
    down = sum(1 for record in records if record.get("rating") == "down")
    return up, down


def describe(level: Level, up: int, down: int) -> str:
    """What the level screen says."""
    steps = ", ".join(str(step_for(n)) for n in range(4))
    plural = "s" if level.votes != 1 else ""
    lines = [
        f"Level {level.level} — {level.votes} vote{plural} so far ({up} 👍, {down} 👎). "
        f"{level.to_next} more for level {level.level + 1}.",
        "",
        f"Every 👍 or 👎 counts, and each level needs more votes than the one before: {steps}, "
        "and so on.",
    ]
    if PROVISIONAL:
        lines.append("These steps are a first guess. They will be set to how many votes really make "
                     "a noticeable difference, once training the companion on your ratings has been "
                     "measured.")
    lines += [
        "",
        "Your votes are what that training would learn from, so vote the way you mean it: careless "
        "votes teach it carelessly.",
        "",
        "Resetting your ratings (Settings → Your data) starts the level over.",
    ]
    return "\n".join(lines)
