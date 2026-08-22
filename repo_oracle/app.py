"""The HTTP surface. Run: uvicorn repo_oracle.app:app --port 8000

Two streaming endpoints, because both operations are slow for different reasons: ingest
takes minutes and the user needs to see it moving, and a turn takes seconds and reads much
faster arriving token by token.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import chat, ingest, trace
from .index import open_index

API_KEY = os.environ.get("ORACLE_API_KEY", "")
WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
# Server-side conversation state. ponytail: a dict, wiped on restart. Sessions are
# short-lived and cheap to restart; the day this runs on more than one process, it becomes
# Redis with the same two operations.
SESSIONS: dict[str, list[dict]] = {}
MAX_HISTORY = 20


def guard(x_api_key: str = Header(default="")) -> None:
    """No key configured means no auth, which is the right default for `docker run` on a
    laptop and the wrong one anywhere else. The README says so."""
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(401, "bad or missing X-API-Key")


app = FastAPI(title="repo-oracle", version="0.1.0")
protected = [Depends(guard)]


class IngestRequest(BaseModel):
    url: str = Field(description="An http(s) git URL, or a local path if ALLOWED_REPO_ROOTS permits it.")
    ref: str | None = Field(default=None, description="Branch or tag. Defaults to the repo default.")
    kind: str = Field(default="git", pattern="^(git|path)$")


class ChatRequest(BaseModel):
    repo_id: str
    message: str
    session_id: str = "default"
    k: int = Field(default=8, ge=1, le=20)


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True, "repos": len(ingest.registry())}


@app.post("/repos", dependencies=protected)
async def create_repo(req: IngestRequest) -> dict:
    job = ingest.start(req.url, req.ref, req.kind)
    return {"repo_id": job.id, "status": job.status}


@app.get("/repos", dependencies=protected)
async def list_repos() -> dict:
    repos = ingest.registry()
    for rid, job in ingest.JOBS.items():
        if job.status != "done":
            repos.setdefault(rid, {"id": rid, "source": job.source})
            repos[rid]["status"] = job.status
            repos[rid]["error"] = job.error
    for rid, entry in repos.items():
        entry.setdefault("status", "done")
    return {"repos": sorted(repos.values(), key=lambda r: r.get("indexed_at", ""), reverse=True)}


@app.delete("/repos/{repo_id}", dependencies=protected)
async def delete_repo(repo_id: str) -> dict:
    if not ingest.forget(repo_id):
        raise HTTPException(404, "no such repo")
    return {"deleted": repo_id}


@app.get("/repos/{repo_id}/events", dependencies=protected)
async def repo_events(repo_id: str) -> StreamingResponse:
    job = ingest.JOBS.get(repo_id)
    if job is None:
        if repo_id in ingest.registry():
            return _sse_once({"type": "done", "detail": "already indexed"})
        raise HTTPException(404, "no such ingest")

    async def gen():
        seen = 0
        while True:
            while seen < len(job.events):
                yield _sse("progress", job.events[seen])
                seen += 1
            if job.status != "running":
                yield _sse("done", {"status": job.status, "error": job.error})
                return
            await asyncio.to_thread(job.wait, seen)

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/repos/{repo_id}/file", dependencies=protected)
async def repo_file(repo_id: str, path: str) -> dict:
    """The source behind a citation. Served from the index, since the checkout is gone."""
    index = _open(repo_id)
    try:
        text = index.file_text(path)
    finally:
        index.close()
    if text is None:
        raise HTTPException(404, f"no indexed content for {path}")
    return {"path": path, "text": text}


@app.get("/repos/{repo_id}/paths", dependencies=protected)
async def repo_paths(repo_id: str) -> dict:
    index = _open(repo_id)
    try:
        return {"paths": index.paths()}
    finally:
        index.close()


@app.post("/chat", dependencies=protected)
async def post_chat(req: ChatRequest) -> StreamingResponse:
    index = _open(req.repo_id)
    history = SESSIONS.setdefault(req.session_id, [])

    async def gen():
        collected = ""
        try:
            queue: asyncio.Queue = asyncio.Queue()

            def produce():
                for name, payload in chat.answer(
                    index, req.message, list(history), k=req.k, repo_id=req.repo_id
                ):
                    queue.put_nowait((name, payload))
                queue.put_nowait(None)

            task = asyncio.create_task(asyncio.to_thread(produce))
            while (item := await queue.get()) is not None:
                name, payload = item
                if name == "token":
                    collected += payload
                    yield _sse("token", {"t": payload})
                elif name == "sources":
                    yield _sse("sources", {"sources": payload})
                elif name == "done":
                    history.append({"role": "user", "content": req.message})
                    history.append({"role": "assistant", "content": payload["answer"]})
                    del history[:-MAX_HISTORY]
                    yield _sse("done", payload)
            await task
        finally:
            index.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.delete("/chat/{session_id}", dependencies=protected)
async def reset_session(session_id: str) -> dict:
    SESSIONS.pop(session_id, None)
    return {"reset": session_id}


@app.get("/traces/recent", dependencies=protected)
async def traces(limit: int = 50) -> dict:
    return {"traces": trace.recent(limit)}


def _open(repo_id: str):
    if not (ingest.DATA_DIR / f"{repo_id}.db").exists():
        raise HTTPException(404, "repo not indexed")
    return open_index(ingest.DATA_DIR, repo_id)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_once(data: dict) -> StreamingResponse:
    async def gen():
        yield _sse("progress", data)
        yield _sse("done", {"status": "done", "error": None})

    return StreamingResponse(gen(), media_type="text/event-stream")


# The built UI, when there is one. Mounted last so it never shadows an API route.
if WEB_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")

    @app.get("/")
    async def index_html() -> FileResponse:
        return FileResponse(WEB_DIST / "index.html")
