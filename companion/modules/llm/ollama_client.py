"""Local model access through Ollama."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Iterator, Sequence

from core.errors import LLMUnavailable
from core.logging import get_logger
from core.types import Message, ToolCall, ToolSpec
from modules.llm.base import LLMProvider

if TYPE_CHECKING:
    from PIL.Image import Image

log = get_logger(__name__)


class OllamaLLM(LLMProvider):
    """Chat against a model served by a local Ollama instance."""

    name = "ollama"

    def __init__(
        self,
        model: str = "qwen3.5:4b",
        host: str = "http://localhost:11434",
        keep_alive: str = "10m",
        num_ctx: int = 8192,
        temperature: float = 0.3,
        think: bool = False,
        request_timeout_s: float = 180.0,
        options: dict | None = None,
        max_reply_tokens: int = 0,
        prompt_log=None,
    ) -> None:
        from ollama import Client

        self.model = model
        self.host = host
        self.keep_alive = keep_alive
        self.num_ctx = num_ctx
        self.temperature = temperature
        self.think = think
        self.options = dict(options or {})
        # A safety stop, not a length: length is asked for in each turn
        # (core/length.py). In Ollama's log of real use, 99% of replies were
        # under 335 tokens, but 16 ran past 1,000 and 4 filled the context --
        # "count to 5000" ran 91 s and 8,161 tokens. 0 means no cap.
        self.max_reply_tokens = max_reply_tokens
        # Writes what was sent and what came back, when switched on
        # (modules/llm/prompt_log.py).
        self.prompt_log = prompt_log
        self._client = Client(host=host, timeout=request_timeout_s)
        self._think_supported = True

    def health_check(self) -> None:
        """Verify the server is up and the configured model is present."""
        import httpx
        from ollama import ResponseError

        try:
            listed = self._client.list()
        except (httpx.ConnectError, httpx.TimeoutException, ConnectionError) as exc:
            raise LLMUnavailable(
                f"Cannot reach Ollama at {self.host}.\n"
                "  Start it from the Start menu, or run:  ollama serve"
            ) from exc
        except ResponseError as exc:  # pragma: no cover
            raise LLMUnavailable(f"Ollama returned an error: {exc}") from exc

        available = {
            _normalise(getattr(m, "model", "") or "")
            for m in getattr(listed, "models", [])
        }
        if _normalise(self.model) not in available:
            have = ", ".join(sorted(available)) or "(none)"
            raise LLMUnavailable(
                f"Model '{self.model}' is not pulled.\n"
                f"  Fix with:  ollama pull {self.model}\n"
                f"  Currently installed: {have}"
            )
        log.debug("ollama ready: model=%s host=%s", self.model, self.host)

    def warm_up(self) -> None:
        """Load the model into VRAM ahead of the first real question.

        A cold load is ~4 s of disk read, which lands squarely on the first
        thing the user asks -- and with speech that becomes four seconds of
        silence before the companion says anything. Paying it at startup moves
        the cost to where nobody is waiting. `keep_alive` then holds the model
        resident, so it is paid once per session at most.
        """
        try:
            for _ in self.chat(
                [Message(role="user", content="Say OK.")], stream=False
            ):
                pass
            log.debug("model %s warmed up", self.model)
        except Exception:  # never fatal: the first question would just be slow
            log.debug("warm-up failed", exc_info=True)

    def chat(
        self,
        messages: Sequence[Message],
        images: Sequence["Image"] | None = None,
        tools: Sequence[ToolSpec] | None = None,
        stream: bool = False,
        collect_tool_calls: list[ToolCall] | None = None,
        temperature: float | None = None,
        json_schema: dict | None = None,
    ) -> Iterator[str]:
        payload = [_to_payload(m) for m in messages]
        if images:
            # The interface's `images` belong to the latest user message.
            # Callers building messages attach them there directly instead.
            from core.vision import encode

            for item in reversed(payload):
                if item["role"] == "user":
                    item["images"] = [*item.get("images", ()), *(
                        image if isinstance(image, (bytes, bytearray)) else encode(image)
                        for image in images
                    )]
                    break

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": payload,
            "stream": stream,
            "keep_alive": self.keep_alive,
            "options": {
                # Configured sampling options first, so the per-call temperature
                # and the context size always win.
                **self.options,
                "temperature": (
                    self.temperature if temperature is None else temperature
                ),
                "num_ctx": self.num_ctx,
            },
        }
        if self.max_reply_tokens:
            kwargs["options"]["num_predict"] = self.max_reply_tokens
        if tools:
            kwargs["tools"] = [spec.to_ollama() for spec in tools]
        if json_schema is not None:
            # Ollama's structured output: decoding is constrained to the schema,
            # so the reply parses, rather than the model being asked to comply.
            kwargs["format"] = json_schema
        if self._think_supported:
            kwargs["think"] = self.think

        response = self._call(kwargs)
        stripper = _ThinkStripper()
        calls = collect_tool_calls if collect_tool_calls is not None else []
        first_call = len(calls)
        stats: dict[str, Any] = {}
        shown: list[str] = []
        # A reply cut by the cap ends in "…", so it reads as cut rather than as
        # a finished thought -- except JSON, which code parses.
        cut_mark = "" if json_schema is not None else " …"

        try:
            if not stream:
                _collect_calls(response, calls)
                _note_stats(response, stats)
                text = (stripper.feed(_extract(response)) + stripper.flush()).strip()
                if stats.get("done_reason") == "length" and text:
                    text += cut_mark
                shown.append(text)
                yield text
                return

            for part in response:
                _collect_calls(part, calls)
                _note_stats(part, stats)
                chunk = _extract(part)
                if chunk:
                    visible = stripper.feed(chunk)
                    if visible:
                        shown.append(visible)
                        yield visible
            tail = stripper.flush()
            if stats.get("done_reason") == "length":
                tail += cut_mark
            if tail:
                shown.append(tail)
                yield tail
        finally:
            # Also when the caller stops early -- the user cut the answer off.
            self._finished(kwargs, "".join(shown), calls[first_call:], stats)

    def _finished(self, kwargs: dict[str, Any], reply: str, calls: list[ToolCall],
                  stats: dict[str, Any]) -> None:
        """Say how the call went in the log, and save it when that is switched on."""
        prompt, generated = stats.get("prompt_eval_count"), stats.get("eval_count")
        if stats.get("done_reason") == "length":
            log.warning("a reply hit the %d-token cap and was cut: ...%s", self.max_reply_tokens,
                        " ".join(reply.split())[-80:])
        if prompt and prompt >= 0.9 * self.num_ctx:
            # Past num_ctx, Ollama drops the front of the prompt without a word.
            log.warning("a prompt used %d of the %d-token context", prompt, self.num_ctx)
        log.debug("model call: prompt %s tokens, reply %s tokens, %s", prompt, generated,
                  stats.get("done_reason") or "stopped early")
        if self.prompt_log is not None:
            try:
                self.prompt_log.record(kwargs, reply, calls, stats)
            except Exception:  # never let a log break an answer
                log.warning("could not save the prompt log", exc_info=True)

    def _call(self, kwargs: dict[str, Any]) -> Any:
        """Invoke chat, degrading gracefully on older ollama-python releases."""
        import httpx
        from ollama import ResponseError

        try:
            return self._client.chat(**kwargs)
        except TypeError as exc:
            if "think" not in str(exc) or "think" not in kwargs:
                raise
            # Older client without a `think` parameter: fall back permanently.
            log.debug("ollama client lacks `think`; disabling that option")
            self._think_supported = False
            kwargs.pop("think", None)
            return self._client.chat(**kwargs)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise LLMUnavailable(
                f"Lost contact with Ollama at {self.host} while generating."
            ) from exc
        except ResponseError as exc:
            raise LLMUnavailable(f"Ollama error: {exc}") from exc


def _to_payload(message: Message) -> dict:
    """Convert a Message to Ollama's wire format."""
    payload: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {"function": {"name": call.name, "arguments": call.arguments}}
            for call in message.tool_calls
        ]
    if message.tool_name:
        payload["tool_name"] = message.tool_name
    if message.images:
        payload["images"] = list(message.images)
    return payload


def _collect_calls(part: Any, sink: list[ToolCall] | None) -> None:
    """Pull any tool calls out of a response chunk into `sink`.

    An out-parameter rather than a second return value, because `chat` yields
    text and a generator cannot also return something useful to a `for` loop.
    Tool calls arrive on their own chunks with empty content, so text streaming
    is unaffected.
    """
    if sink is None:
        return
    message = getattr(part, "message", None)
    if message is None and isinstance(part, dict):
        message = part.get("message")
    if message is None:
        return
    calls = (
        message.get("tool_calls")
        if isinstance(message, dict)
        else getattr(message, "tool_calls", None)
    )
    for call in calls or []:
        function = call["function"] if isinstance(call, dict) else call.function
        name = function["name"] if isinstance(function, dict) else function.name
        raw_args = (
            function["arguments"] if isinstance(function, dict) else function.arguments
        )
        if isinstance(raw_args, str):
            try:
                raw_args = json.loads(raw_args)
            except (ValueError, TypeError):
                raw_args = {}
        sink.append(ToolCall(name=str(name), arguments=dict(raw_args or {})))


def _note_stats(part: Any, stats: dict[str, Any]) -> None:
    """Keep how the reply ended and its token counts, from the last chunk."""
    for name in ("done_reason", "prompt_eval_count", "eval_count"):
        value = part.get(name) if isinstance(part, dict) else getattr(part, name, None)
        if value is not None:
            stats[name] = value


def _normalise(name: str) -> str:
    """`qwen3:8b` and `qwen3:8b:latest` name the same model."""
    return name.strip().removesuffix(":latest")


def _extract(part: Any) -> str:
    """Pull assistant text out of a response object or plain dict."""
    message = getattr(part, "message", None)
    if message is not None:
        return getattr(message, "content", "") or ""
    if isinstance(part, dict):
        return (part.get("message") or {}).get("content", "") or ""
    return ""


class _ThinkStripper:
    """Removes <think>...</think> spans from a token stream.

    `think=False` should prevent these entirely, but the tag leaks through on
    some model/server combinations, and a stray reasoning monologue in the
    middle of an answer is a bad first impression. Tags may straddle chunk
    boundaries, hence the retained tail.
    """

    OPEN = "<think>"
    CLOSE = "</think>"

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False

    def feed(self, chunk: str) -> str:
        self._buffer += chunk
        out: list[str] = []
        while True:
            if self._inside:
                index = self._buffer.find(self.CLOSE)
                if index == -1:
                    keep = len(self.CLOSE) - 1
                    self._buffer = self._buffer[-keep:] if keep else ""
                    break
                self._buffer = self._buffer[index + len(self.CLOSE) :]
                self._inside = False
            else:
                index = self._buffer.find(self.OPEN)
                if index == -1:
                    keep = len(self.OPEN) - 1
                    if len(self._buffer) > keep:
                        out.append(self._buffer[:-keep])
                        self._buffer = self._buffer[-keep:]
                    break
                out.append(self._buffer[:index])
                self._buffer = self._buffer[index + len(self.OPEN) :]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        if self._inside:
            self._buffer = ""
            return ""
        remainder, self._buffer = self._buffer, ""
        return remainder
