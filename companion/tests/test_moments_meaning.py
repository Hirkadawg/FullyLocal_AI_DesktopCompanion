"""Liked moments matched by meaning, with shared words as the fallback.

Measured on a set of liked moments collected in use, with EmbeddingGemma: shared words found 3 of
12 expected moments (2 wrong) and 7 of 9 on a set written afterwards (3 wrong);
meaning at a similarity of 0.30 found 11 of 12 (1 wrong) and 9 of 9 (0 wrong).
No unrelated message scored above 0.26. Here the model is faked: what is checked
is how scores become choices, the kept vectors, and falling back to words.
"""

import logging
import sys
from datetime import datetime
from types import SimpleNamespace

from helpers import CONFIG_PATH, FIXTURE_IMAGE, VOICES_DIR  # noqa: F401

from core.companion import build_embedder
from core.config import AppConfig, RatingsConfig
from core.logging import setup_logging
from core.moments import MIN_SIMILARITY, Moment, document, relevant_moments
from core.settings import SETTINGS
from modules.llm.embeddings import OllamaEmbedder, cosine
from modules.ui.worker import CompanionWorker

setup_logging("ERROR")
failures = 0


def check(label, condition, detail=""):
    global failures
    failures += not condition
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}{'  ' + detail if detail else ''}")


warnings = []


class Catch(logging.Handler):
    def emit(self, record):
        warnings.append(record.getMessage())


logger = logging.getLogger("modules.llm.embeddings")
logger.setLevel(logging.INFO)
logger.propagate = False
logger.addHandler(Catch())

SOURDOUGH = Moment(kind="remark", at=datetime(2026, 9, 15, 17, 31), topic="Sourdough starter", site="Wikipedia",
               reply="Does the text say how a baker actually knows the starter is ready?",
               why="The article gives feeding times but not how readiness is judged.")
SKY = Moment(kind="answer", at=datetime(2026, 9, 14, 23, 45), topic="", site="Google",
             message="why is the sky blue?", reply="Air scatters short blue wavelengths more: Rayleigh scattering.")
GAME = Moment(kind="remark", at=datetime(2026, 9, 16, 13, 31), topic="", site="Orbit Racer",
              reply="That last lap cost you the lead, buddy.", why="A direct reaction to the race result.")
MOMENTS = [GAME, SOURDOUGH, SKY]


class FakeEmbedder:
    """Scores from a table: how close each query is to each moment."""

    def __init__(self, table, reachable=True):
        self.table, self.reachable, self.queries = table, reachable, []

    def similarities(self, query, documents):
        self.queries.append(query)
        if not self.reachable:
            return None
        return [self.table.get(query, {}).get(doc, 0.0) for doc in documents]


print("choosing by meaning")
table = {"gökyüzü neden mavi?": {document(SKY): 0.36, document(SOURDOUGH): 0.12},
         "Ekşi mayanın hazır olduğunu nasıl anlarız?": {document(SOURDOUGH): 0.56, document(SKY): 0.31},
         "what's the capital of Japan?": {document(GAME): 0.09},
         "do you remember anything we talked about?": {document(SKY): 0.10, document(GAME): 0.08,
                                                      document(SOURDOUGH): 0.04}}
fake = FakeEmbedder(table)
check("a Turkish question finds the English moment it means, which words don't",
      relevant_moments(MOMENTS, "gökyüzü neden mavi?", embedder=fake) == [SKY]
      and relevant_moments(MOMENTS, "gökyüzü neden mavi?") == [])
check("the closest first, then any other above the threshold, at most two",
      relevant_moments(MOMENTS, "Ekşi mayanın hazır olduğunu nasıl anlarız?", embedder=fake) == [SOURDOUGH, SKY])
check(f"below {MIN_SIMILARITY}, nothing", relevant_moments(MOMENTS, "what's the capital of Japan?", embedder=fake) == [])
check("asked what was said before, the closest ones even below the threshold -- a Turkish "
      "\"what did we talk about\" scores 0.13-0.18 against everything, and the newest is not the one meant",
      relevant_moments(MOMENTS, "do you remember anything we talked about?", embedder=fake) == [SKY, GAME],
      str([m.reply[:12] for m in relevant_moments(MOMENTS, "do you remember anything we talked about?",
                                                  embedder=fake)]))
relevant_moments(MOMENTS, "reading about starters", title="Ekşi maya - Vikipedi - Brave", embedder=fake)
check("a remark's query is the page's title and what they are doing",
      fake.queries[-1] == "Ekşi maya - Vikipedi - Brave — reading about starters")
check("a moment is read with its page and who said what, a remark with its reason",
      document(SOURDOUGH).startswith('title: Sourdough starter | text: you said "Does the text')
      and "because The article gives feeding times" in document(SOURDOUGH)
      and document(SKY) == ('title: Google | text: they asked "why is the sky blue?" and you answered '
                            '"Air scatters short blue wavelengths more: Rayleigh scattering."'))
down = FakeEmbedder(table, reachable=False)
check("when the model can't be reached, shared words decide, as before",
      relevant_moments(MOMENTS, "who won the race?", embedder=down) == [GAME] and bool(down.queries))

print("\nthe embedder")


class FakeClient:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def embed(self, model, input, keep_alive):
        self.calls.append((model, list(input), keep_alive))
        if self.fail:
            raise ConnectionError("Ollama isn't running")
        return SimpleNamespace(embeddings=[[1.0, float(len(text) % 7)] for text in input])


embedder = OllamaEmbedder(model="embeddinggemma", keep_alive="10m")
embedder._client = FakeClient()
first = embedder.similarities("hello", ["a moment", "another moment"])
embedder.similarities("again", ["a moment", "another moment", "a third"])
calls = embedder._client.calls
check("moments are embedded once and kept; each query on its own, with EmbeddingGemma's prompt",
      [c[1] for c in calls] == [["a moment", "another moment"], ["task: search result | query: hello"],
                                ["a third"], ["task: search result | query: again"]], str(calls))
check("...kept loaded as long as the chat model", all(call[2] == "10m" for call in calls))
check("scores are cosine similarities", len(first) == 2 and abs(cosine([1, 0], [1, 0]) - 1) < 1e-9
      and abs(cosine([1, 0], [0, 1])) < 1e-9)
embedder._client = FakeClient(fail=True)
warnings.clear()
check("Ollama down: None, so words decide", embedder.similarities("q", ["a moment"]) is None)
embedder.similarities("q", ["a moment"])
check("...said once in the log, not every turn", sum("matched by words" in w for w in warnings) == 1, str(warnings))
embedder._client = FakeClient()
embedder.similarities("q", ["a moment"])
check("...and when it is back, said so", any("available again" in w for w in warnings))

print("\nin the app")
cfg = AppConfig.load(CONFIG_PATH)
check("built when moments are remembered and a model is named", isinstance(build_embedder(cfg), OllamaEmbedder))
cfg.ratings.moments_embedding_model = ""
check("...not with no model named (words)", build_embedder(cfg) is None)
cfg.ratings.moments_embedding_model, cfg.ratings.remember_moments = "embeddinggemma", False
check("...nor with moments off", build_embedder(cfg) is None)

warmed = []
worker = SimpleNamespace(_config=AppConfig.load(CONFIG_PATH),
                         _companion=SimpleNamespace(embedder=SimpleNamespace(warm_up=warmed.append)))
CompanionWorker._warm_up_embedder(worker)
check("the worker warms it up with the liked moments' documents", len(warmed) == 1 and isinstance(warmed[0], list))
CompanionWorker._warm_up_embedder(SimpleNamespace(_config=worker._config, _companion=SimpleNamespace(embedder=None)))
check("...and does nothing without one", len(warmed) == 1)

check("off in code (words), embeddinggemma in config.yaml, on the settings page",
      RatingsConfig().moments_embedding_model == ""
      and AppConfig.load(CONFIG_PATH).ratings.moments_embedding_model == "embeddinggemma"
      and any(s.key == "ratings.moments_embedding_model" for s in SETTINGS))

print(f"\n  {failures} failure(s)")
sys.exit(1 if failures else 0)
