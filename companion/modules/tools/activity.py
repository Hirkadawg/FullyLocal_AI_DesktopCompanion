"""Searching the activity log: "what was that article I read yesterday?"

Read-only. Offered only when a message is about something done earlier
(modules/tools/requests.py), and unavailable while the log is switched off.
"""

from __future__ import annotations

from core.activity import ActivityLog, time_range
from modules.tools.base import Tool


class SearchActivity(Tool):
    name = "search_activity"
    description = (
        "Search the log of pages the user spent time on earlier: window titles, "
        "apps, times and a short description. Use for questions like 'what was "
        "that article I read yesterday' or 'when did I watch that video'."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words the page would be about, e.g. 'mars rover' or 'pasta'",
            },
            "since": {
                "type": "string",
                "enum": ["today", "yesterday", "this week", "this month", "any"],
                "description": "How far back to look",
            },
        },
        "required": ["query"],
    }
    writes = False

    def __init__(self, config) -> None:
        # The config itself, not a path: the folder and the switch can change
        # in the settings page while the app runs.
        self.config = config

    def run(self, query: str = "", since: str = "any") -> str:
        activity = self.config.activity
        if not activity.enabled:
            return "The activity log is switched off."
        start, end = time_range(since)
        found = ActivityLog(self.config.root / activity.folder).search(query, start, end)
        if not found:
            return "Nothing in the activity log matches that."
        return "Pages they spent time on, best match first:\n" + "\n".join(
            f"- {visit.line()}" for visit in found
        )
