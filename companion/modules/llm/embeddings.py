"""Meaning, not shared words: which liked moments bear on a message.

Shared moments (core/moments.py) were matched by shared words, which missed a
question worded differently or asked in Turkish about an English moment, and
brought in unrelated ones on words like "musun" and "article". EmbeddingGemma
(Ollama, 621 MB, 100+ languages) turns text into numbers that are close when the
meaning is. Measured on liked moments collected in use:
- 19 messages and pages: shared words found 3 of 12 expected moments with 2
  wrong ones; meaning at a similarity of 0.30 found 11 of 12 with 1 wrong;
- 15 more written afterwards: words 7 of 9 with 3 wrong; meaning 9 of 9, 0 wrong;
- no unrelated message scored above 0.26; real matches from 0.32.

A query takes ~60 ms. Moments are embedded once and kept; the model reloads in
~4 s after Ollama unloads it, so it is warmed up at start and kept loaded as long
as the chat model. When it can't be reached, matching falls back to words.
"""

from __future__ import annotations

import math
import threading

from core.logging import get_logger

log = get_logger(__name__)

#: EmbeddingGemma's own prompts for retrieval: the question, and what is searched.
QUERY = "task: search result | query: {}"
DOCUMENT = "title: {title} | text: {text}"


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class OllamaEmbedder:
    """Similarity between a query and documents, with documents' vectors kept."""

    def __init__(self, model: str = "embeddinggemma", host: str = "http://localhost:11434",
                 keep_alive: str = "10m", timeout_s: float = 30.0) -> None:
        from ollama import Client

        self.model = model
        self.keep_alive = keep_alive
        self._client = Client(host=host, timeout=timeout_s)
        self._vectors: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._failed = False

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return self._client.embed(model=self.model, input=texts, keep_alive=self.keep_alive).embeddings

    def similarities(self, query: str, documents: list[str]) -> list[float] | None:
        """How close each document is to the query, 0-1; None when the model can't be reached."""
        try:
            with self._lock:
                missing = [d for d in dict.fromkeys(documents) if d not in self._vectors]
                if missing:
                    self._vectors.update(zip(missing, self._embed(missing)))
                vectors = [self._vectors[d] for d in documents]
            query_vector = self._embed([QUERY.format(query)])[0]
        except Exception as exc:
            if not self._failed:
                log.warning("memories matched by words: %s isn't available (%s)", self.model, exc)
                self._failed = True
            return None
        if self._failed:
            log.info("%s is available again", self.model)
            self._failed = False
        return [cosine(query_vector, vector) for vector in vectors]

    def warm_up(self, documents: list[str]) -> None:
        """Load the model and embed what is known, so the first question doesn't wait."""
        self.similarities("hello", documents)
