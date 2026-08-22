"""The one place that talks to a model provider.

Everything goes through litellm, so `ORACLE_MODEL` and `ORACLE_EMBED_MODEL` are
`"provider/model"` strings and switching provider is an env var, not a refactor. Gemini is
the default because its free tier means a reviewer can run this with no paid key.

Both functions here are the only network calls in the system, which is what makes the test
suite offline: tests inject a fake embedder and a fake completer instead of mocking a
transport.
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Iterator

MODEL = os.environ.get("ORACLE_MODEL", "gemini/gemini-flash-latest")
EMBED_MODEL = os.environ.get("ORACLE_EMBED_MODEL", "gemini/gemini-embedding-001")
# gemini-embedding-001 returns 3072 dimensions by default. It is trained with Matryoshka
# representation learning, so a 768-slice keeps most of the quality at a quarter of the
# memory and a quarter of the matmul. 20k chunks: 60MB instead of 240MB.
EMBED_DIMS = int(os.environ.get("ORACLE_EMBED_DIMS", "768"))
# Gemini caps a batch at 100 inputs; other providers allow more but nobody minds 64.
EMBED_BATCH = int(os.environ.get("ORACLE_EMBED_BATCH", "64"))
# Gemini's free tier bills embeddings per *input*, not per batched call, and cuts you off
# at 100 per minute. Ingest is the one place that hits a rate limit at all, and it hits it
# hard, so pace it rather than discover the 429 halfway through a repo. Raise this on a
# paid key: ORACLE_EMBED_RPM=1500 makes ingest roughly fifteen times faster.
EMBED_RPM = int(os.environ.get("ORACLE_EMBED_RPM", "90"))

_pace_lock = threading.Lock()
_recent: deque[float] = deque()


def _pace(count: int) -> None:
    """Block until `count` more embeddings fit inside the per-minute allowance."""
    if EMBED_RPM <= 0:
        return
    while True:
        with _pace_lock:
            now = time.monotonic()
            while _recent and now - _recent[0] > 60:
                _recent.popleft()
            if len(_recent) + count <= EMBED_RPM or not _recent:
                _recent.extend([now] * count)
                return
            wait = 60 - (now - _recent[0]) + 0.1
        time.sleep(min(wait, 60))


def embed(texts: list[str], *, task: str = "retrieval_document") -> list[list[float]]:
    """Embed a list of texts, batched. Asymmetric: documents and queries get different hints."""
    import litellm

    out: list[list[float]] = []
    for i in range(0, len(texts), EMBED_BATCH):
        batch = texts[i : i + EMBED_BATCH]
        kwargs: dict = {}
        if EMBED_MODEL.startswith("gemini/"):
            # Asymmetric embedding: a question and the code that answers it are not the
            # same kind of text, and Gemini's task hints measurably help retrieval.
            kwargs["task_type"] = "RETRIEVAL_QUERY" if task == "retrieval_query" else "RETRIEVAL_DOCUMENT"
            kwargs["dimensions"] = EMBED_DIMS
        _pace(len(batch))
        resp = litellm.embedding(model=EMBED_MODEL, input=batch, num_retries=3, **kwargs)
        out.extend(item["embedding"] for item in resp.data)
    return out


def _kwargs(temperature: float, max_tokens: int) -> dict:
    """Shared call options.

    `reasoning_effort="disable"` matters more than it looks. Gemini Flash is a thinking
    model by default, and thinking tokens are billed against max_tokens: a 2048 budget can
    be spent entirely on reasoning, returning an empty answer. This work is grounded
    synthesis over excerpts we already retrieved, not a puzzle, so the thinking buys little
    and costs latency on every turn. Providers that ignore the parameter are unaffected.
    """
    return {
        "model": MODEL,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "reasoning_effort": "disable",
        # Not every model takes reasoning_effort — Gemini 2.0 Flash rejects the request
        # outright. drop_params lets litellm strip what a given provider does not accept,
        # which is what keeps ORACLE_MODEL a genuinely free choice rather than a list of
        # three models I happened to test.
        "drop_params": True,
        # Free-tier Gemini returns 503 under load often enough that one retry is the
        # difference between a working demo and a broken one.
        "num_retries": 2,
    }


def complete(messages: list[dict], *, temperature: float = 0.1, max_tokens: int = 2048) -> str:
    import litellm

    resp = litellm.completion(messages=messages, **_kwargs(temperature, max_tokens))
    return resp.choices[0].message.content or ""


def stream(messages: list[dict], *, temperature: float = 0.1, max_tokens: int = 2048) -> Iterator[str]:
    import litellm

    for part in litellm.completion(messages=messages, stream=True, **_kwargs(temperature, max_tokens)):
        delta = part.choices[0].delta.content
        if delta:
            yield delta
