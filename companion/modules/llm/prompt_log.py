"""Exactly what was sent to the model, and what came back.

The user noticed replies that didn't make sense, and couldn't see why: every
message is wrapped in the system prompt, memory, the screen and per-turn notes
before it reaches the model. With `llm.log_prompts` on, each call is written
out in full to a plain text file, one per day, readable in any editor.

Off unless switched on: it holds the screen's text. Nothing leaves this machine.
Screenshots are not saved -- only that one was attached. The system prompt is
written in full the first time a file sees it, then referred to. A day's file
stops growing at MAX_FILE_BYTES, so a forgotten switch can't fill the disk.
"""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from core.logging import get_logger

log = get_logger(__name__)

#: A day's file stops here: about 500 long turns.
MAX_FILE_BYTES = 20 * 1024 * 1024


class PromptLog:
    """Writes each model call to data/prompts/prompts-YYYY-MM-DD.txt when switched on."""

    def __init__(self, config) -> None:
        # The config itself, not a copy: the settings page switches it live.
        self._config = config
        self._lock = threading.Lock()
        #: Per file: the system prompts already written in full, and when.
        self._systems: dict[Path, dict[str, str]] = {}
        self._full: set[Path] = set()

    @property
    def enabled(self) -> bool:
        return bool(self._config.llm.log_prompts)

    def folder(self) -> Path:
        folder = Path(self._config.llm.prompt_log_folder)
        return folder if folder.is_absolute() else self._config.root / folder

    def record(self, request: dict[str, Any], reply: str, tool_calls: list, stats: dict[str, Any],
               now: datetime | None = None) -> Path | None:
        """Append one call. Returns the file written, or None when off or full."""
        if not self.enabled:
            return None
        now = now or datetime.now()
        path = self.folder() / f"prompts-{now:%Y-%m-%d}.txt"
        with self._lock:
            if path.is_file() and path.stat().st_size >= MAX_FILE_BYTES:
                if path not in self._full:
                    self._full.add(path)
                    log.warning("the prompt log for today is full (%d MB); not saving more today",
                                MAX_FILE_BYTES // (1024 * 1024))
                return None
            text = self._format(path, request, reply, tool_calls, stats, now)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)
        return path

    def _format(self, path: Path, request: dict, reply: str, tool_calls: list, stats: dict,
                now: datetime) -> str:
        options = request.get("options") or {}
        settings = ", ".join(f"{key} {value}" for key, value in options.items())
        stopped = {"stop": "the reply ended", "length": "HIT THE LENGTH CAP"}.get(
            stats.get("done_reason") or "", stats.get("done_reason") or "stopped early (interrupted)")
        lines = [
            f"==================== {now:%Y-%m-%d %H:%M:%S} · {request.get('model', '')} ====================",
            f"Settings: {settings}",
            f"Tokens: prompt {stats.get('prompt_eval_count', '?')}, reply {stats.get('eval_count', '?')} "
            f"— {stopped}",
        ]
        if request.get("tools"):
            names = [tool.get("function", {}).get("name", "?") for tool in request["tools"]]
            lines.append(f"Tools offered: {', '.join(names)}")
        if request.get("format") is not None:
            lines.append("Format: JSON, constrained to a schema")
        seen = self._systems.setdefault(path, {})
        for message in request.get("messages") or []:
            role = message.get("role", "?")
            content = message.get("content", "") or ""
            extra = ""
            if message.get("images"):
                count = len(message["images"])
                extra = f" [{count} screenshot{'s' if count > 1 else ''} attached, not saved]"
            if message.get("tool_calls"):
                calls = "; ".join(f"{c['function']['name']}({c['function'].get('arguments', {})})"
                                  for c in message["tool_calls"])
                extra += f" [asked for: {calls}]"
            if message.get("tool_name"):
                extra += f" [result of {message['tool_name']}]"
            if role == "system":
                if content in seen:
                    lines += [f"--- system ---", f"(the same system prompt as at {seen[content]})"]
                    continue
                seen[content] = f"{now:%H:%M:%S}"
            lines += [f"--- {role}{extra} ---", content]
        lines += ["=== reply ===", reply]
        if tool_calls:
            lines.append("Tool calls: " + "; ".join(f"{call.name}({call.arguments})" for call in tool_calls))
        return "\n".join(lines) + "\n\n"
