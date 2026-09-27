"""Tool interface and registry.

A tool is a small, named capability the model can invoke: start a timer, write a
note, check the clock. Each declares a JSON schema so the model knows how to
call it, and returns a plain string the model reads back as an observation.

Two rules that keep this safe as tools multiply:

- **Tools never raise into the model loop.** A failing tool returns a message
  describing the failure, which the model can relay or work around. An
  exception escaping here would kill the whole answer over a mistyped argument.
- **Tools declare whether they act on the world.** `writes` is false for
  reading the clock and true for creating a timer or note. Nothing enforces
  policy on it yet, but the distinction is recorded from the start rather than
  retrofitted once there are twenty tools and one of them sends email.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from core.logging import get_logger
from core.types import ToolCall, ToolSpec

log = get_logger(__name__)


class Tool(ABC):
    """One capability the model can invoke."""

    #: Name the model calls. Keep it verb-like and unambiguous.
    name: str = "tool"
    #: Shown to the model. This is the only thing telling it when to use this,
    #: so describe the situation, not just the mechanics.
    description: str = ""
    #: JSON schema for the arguments.
    parameters: dict = {"type": "object", "properties": {}}
    #: Whether invoking this changes anything outside the process.
    writes: bool = False

    @abstractmethod
    def run(self, **kwargs) -> str:
        """Do the thing, and return what the model should see."""

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name, description=self.description, parameters=self.parameters
        )


class ToolRegistry:
    """Holds the available tools and dispatches calls to them."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.add(tool)

    def add(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"duplicate tool name: {tool.name}")
        self._tools[tool.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [tool.spec() for tool in self._tools.values()]

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)

    def run(self, call: ToolCall) -> str:
        """Execute a call, converting any failure into a readable result.

        The model sees the error text and can apologise or retry, which is a
        far better outcome than the answer dying mid-sentence.
        """
        tool = self._tools.get(call.name)
        if tool is None:
            available = ", ".join(self._tools) or "none"
            log.warning("model asked for unknown tool %r", call.name)
            return f"There is no tool called '{call.name}'. Available: {available}."

        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        try:
            result = tool.run(**arguments)
        except TypeError as exc:
            # Wrong or missing arguments -- the most common model mistake.
            log.warning("bad arguments for %s: %s", call.name, exc)
            return f"Could not run {call.name}: {exc}"
        except Exception as exc:
            log.error("tool %s failed", call.name, exc_info=True)
            return f"{call.name} failed: {exc}"

        text = str(result).strip()
        log.debug("%s -> %s", call, text[:80])
        return text or f"{call.name} completed."
