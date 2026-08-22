"""One JSON line per answered turn.

A RAG system fails quietly: the answer looks fluent and the retrieval was wrong. You cannot
see that from the transcript, so every turn records what was actually retrieved, with
scores, tiers, timings and token counts. When someone reports a bad answer, the trace says
whether retrieval missed or the model ignored what it was given — which are different bugs
with different fixes.

ponytail: append-only JSONL on local disk, read back by tailing the file. That is the whole
thing. It is what OpenTelemetry would give us minus the collector, and the shape below maps
onto spans one-for-one when this moves somewhere that has a collector.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

TRACE_PATH = Path(os.environ.get("ORACLE_DATA_DIR", "data")) / "traces.jsonl"
_lock = threading.Lock()


class Timer:
    """Wall-clock milliseconds per stage, so a slow turn can be blamed on the right stage."""

    def __init__(self) -> None:
        self.marks: dict[str, float] = {}
        self._t0 = time.perf_counter()

    def mark(self, stage: str) -> None:
        now = time.perf_counter()
        self.marks[stage] = round((now - self._t0) * 1000, 1)
        self._t0 = now

    def as_dict(self) -> dict:
        return dict(self.marks)


def write(record: dict) -> None:
    record = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **record}
    line = json.dumps(record, ensure_ascii=False)
    try:
        TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _lock, TRACE_PATH.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # never fail a user's turn because telemetry could not be written


def recent(limit: int = 50) -> list[dict]:
    if not TRACE_PATH.exists():
        return []
    try:
        lines = TRACE_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-limit:]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(out))
