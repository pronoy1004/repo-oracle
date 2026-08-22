"""Ingestion: clone a repository, chunk it, embed it, summarise it, index it.

Ingest takes minutes, so it runs on a background thread and reports progress as events.
Events are buffered on the job and replayed from the beginning on every connection: SSE has
no replay of its own, and a reviewer who opens the page late should still see what happened.

The checkout is deleted when ingest finishes. Everything the service needs afterwards —
including the source shown in the citation panel — is in the SQLite index, which keeps the
disk footprint bounded and means no user-supplied repository sits on the host longer than
one ingest.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import checkout, llm, mapper
from .chunk import Chunk, chunk_repo
from .index import Index, open_index

DATA_DIR = Path(os.environ.get("ORACLE_DATA_DIR", "data"))
REGISTRY = DATA_DIR / "repos.json"

MAX_FILES = int(os.environ.get("ORACLE_MAX_FILES", "4000"))
MAX_CHUNKS = int(os.environ.get("ORACLE_MAX_CHUNKS", "12000"))
EMBED_CHUNK_BATCH = 96
SKIP_MAP = os.environ.get("ORACLE_SKIP_MAP", "").lower() in {"1", "true", "yes"}


def repo_id(source: str, ref: str | None) -> str:
    """Stable, filesystem-safe id. Same repo and ref means the same index file."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", source.rstrip("/").split("/")[-1] or "repo").strip("-")
    digest = hashlib.sha256(f"{source}@{ref or ''}".encode()).hexdigest()[:8]
    return f"{slug.lower()[:40]}-{digest}"


@dataclass
class Job:
    id: str
    source: str
    status: str = "running"  # running | done | error
    events: list[dict] = field(default_factory=list)
    error: str | None = None
    started: float = field(default_factory=time.time)
    _waiters: list[threading.Event] = field(default_factory=list)

    def emit(self, **event) -> None:
        self.events.append({"at": round(time.time() - self.started, 1), **event})
        for waiter in self._waiters:
            waiter.set()

    def wait(self, seen: int, timeout: float = 20.0) -> None:
        if len(self.events) > seen and self.status == "running":
            return
        waiter = threading.Event()
        self._waiters.append(waiter)
        waiter.wait(timeout)
        self._waiters.remove(waiter)


JOBS: dict[str, Job] = {}


def registry() -> dict[str, dict]:
    if not REGISTRY.exists():
        return {}
    try:
        return json.loads(REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _record(rid: str, entry: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = registry()
    data[rid] = entry
    REGISTRY.write_text(json.dumps(data, indent=2))


def forget(rid: str) -> bool:
    data = registry()
    if rid not in data:
        return False
    del data[rid]
    REGISTRY.write_text(json.dumps(data, indent=2))
    (DATA_DIR / f"{rid}.db").unlink(missing_ok=True)
    JOBS.pop(rid, None)
    return True


def start(source: str, ref: str | None = None, kind: str = "git") -> Job:
    """Kick off an ingest. Returns immediately; watch the job's events for progress."""
    rid = repo_id(source, ref)
    existing = JOBS.get(rid)
    if existing and existing.status == "running":
        return existing
    job = Job(id=rid, source=source)
    JOBS[rid] = job
    threading.Thread(target=_run, args=(job, source, ref, kind), daemon=True).start()
    return job


def _run(job: Job, source: str, ref: str | None, kind: str) -> None:
    cleanup = None
    try:
        job.emit(type="clone", detail=source)
        if kind == "git":
            repo, cleanup = checkout.clone(source, ref)
        else:
            repo = checkout.resolve_local(source)
        commit = checkout.head_sha(repo)

        job.emit(type="chunk", detail="reading files")
        chunks = chunk_repo(repo, max_files=MAX_FILES)
        truncated = len(chunks) > MAX_CHUNKS
        chunks = chunks[:MAX_CHUNKS]
        files = len({c.path for c in chunks})
        job.emit(type="chunk", detail=f"{len(chunks)} chunks from {files} files"
                 + (" (truncated)" if truncated else ""))

        # Re-ingesting the same repo means "refresh", so the old index goes. Without this
        # a second run appends a second copy of every chunk and retrieval returns each hit
        # twice — which is exactly what happened the first time a run was interrupted.
        (DATA_DIR / f"{job.id}.db").unlink(missing_ok=True)
        index = open_index(DATA_DIR, job.id)
        _embed_and_add(index, chunks, job, label="code")

        map_chunks: list[Chunk] = []
        if not SKIP_MAP:
            map_chunks = mapper.build_map(repo, on_event=job.emit)
            if map_chunks:
                _embed_and_add(index, map_chunks, job, label="map")

        index.set_meta(
            source=source, ref=ref, commit=commit, files=files,
            chunks=index.count(), map_chunks=len(map_chunks),
            embed_model=llm.EMBED_MODEL, truncated=truncated,
            indexed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        total = index.count()
        _record(job.id, {"id": job.id, **index.meta()})
        index.close()

        job.status = "done"
        job.emit(type="done", detail=f"indexed {total} chunks from {files} files")
    except Exception as exc:
        job.status = "error"
        job.error = f"{type(exc).__name__}: {exc}"
        job.emit(type="error", detail=job.error)
    finally:
        if cleanup:
            cleanup()


def _embed_and_add(index: Index, chunks: list[Chunk], job: Job, label: str) -> None:
    for i in range(0, len(chunks), EMBED_CHUNK_BATCH):
        batch = chunks[i : i + EMBED_CHUNK_BATCH]
        vectors = llm.embed([c.embed_text() for c in batch])
        index.add(batch, vectors)
        job.emit(type="embed", detail=f"{label}: {min(i + len(batch), len(chunks))}/{len(chunks)}")
