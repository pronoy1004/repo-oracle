"""End to end over HTTP with a stubbed model: ingest a fixture repo, then ask about it.

No network. The two functions in llm.py are the only calls that leave the process, so
replacing them is enough to exercise everything else for real — real SQLite, real FTS5,
real fusion, real SSE.
"""

import hashlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ORACLE_SKIP_MAP", "1")  # the map tier is model work, stubbed elsewhere

    from repo_oracle import app as app_module
    from repo_oracle import chat, ingest, llm, mapper, trace

    ingest.DATA_DIR = tmp_path / "data"
    ingest.REGISTRY = ingest.DATA_DIR / "repos.json"
    ingest.SKIP_MAP = True
    trace.TRACE_PATH = tmp_path / "data" / "traces.jsonl"

    def fake_embed(texts, task="retrieval_document"):
        # Deterministic pseudo-embedding: stable per text, so dense retrieval is
        # reproducible without pretending to be semantic.
        out = []
        for text in texts:
            digest = hashlib.sha256(text.encode()).digest()
            out.append([b / 255 for b in digest[:16]])
        return out

    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(chat.llm, "embed", fake_embed)
    monkeypatch.setattr(ingest.llm, "embed", fake_embed)
    monkeypatch.setattr(mapper.llm, "complete", lambda *a, **k: "map text")
    monkeypatch.setattr(
        chat.llm, "stream",
        lambda messages, **k: iter(["Routes are registered in ", "`src/app.py:2`."]),
    )
    monkeypatch.setattr(chat.llm, "complete", lambda *a, **k: "rewritten query")

    with TestClient(app_module.app) as c:
        yield c


@pytest.fixture
def repo(tmp_path):
    src = tmp_path / "fixture" / "src"
    src.mkdir(parents=True)
    (src / "app.py").write_text(
        "from flask import Flask\n"
        "def register_routes(app):\n"
        "    app.add_url_rule('/chat', view_func=chat_view)\n"
        "def chat_view():\n"
        "    return 'ok'\n"
    )
    (tmp_path / "fixture" / "README.md").write_text("# fixture\nA tiny app.\n")
    return tmp_path / "fixture"


def ingest_fixture(client, repo, monkeypatch):
    monkeypatch.setenv("ALLOWED_REPO_ROOTS", str(repo.parent))
    resp = client.post("/repos", json={"url": str(repo), "kind": "path"})
    assert resp.status_code == 200
    repo_id = resp.json()["repo_id"]
    body = client.get(f"/repos/{repo_id}/events").text  # blocks until the job finishes
    assert '"type": "error"' not in body, body
    return repo_id


def test_healthz_needs_no_key(client):
    assert client.get("/healthz").json()["ok"] is True


def test_ingest_then_ask_returns_a_streamed_cited_answer(client, repo, monkeypatch):
    repo_id = ingest_fixture(client, repo, monkeypatch)

    listed = client.get("/repos").json()["repos"]
    assert any(r["id"] == repo_id and r["status"] == "done" for r in listed)

    with client.stream("POST", "/chat", json={"repo_id": repo_id, "message": "where are routes registered?"}) as r:
        body = "".join(r.iter_text())
    assert "event: sources" in body
    assert "src/app.py" in body
    assert '"citations"' in body
    assert "src/app.py:2" in body


def test_the_source_panel_can_fetch_a_cited_file(client, repo, monkeypatch):
    repo_id = ingest_fixture(client, repo, monkeypatch)
    text = client.get(f"/repos/{repo_id}/file", params={"path": "src/app.py"}).json()["text"]
    assert "def register_routes" in text
    assert client.get(f"/repos/{repo_id}/file", params={"path": "nope.py"}).status_code == 404


def test_every_turn_leaves_a_trace(client, repo, monkeypatch):
    repo_id = ingest_fixture(client, repo, monkeypatch)
    with client.stream("POST", "/chat", json={"repo_id": repo_id, "message": "what is chat_view?"}) as r:
        "".join(r.iter_text())
    traces = client.get("/traces/recent").json()["traces"]
    assert traces and traces[0]["question"] == "what is chat_view?"
    assert traces[0]["retrieved"], "a trace with no retrieval record cannot debug a bad answer"


def test_asking_an_unindexed_repo_is_a_404(client):
    assert client.post("/chat", json={"repo_id": "nope", "message": "hi"}).status_code == 404


def test_api_key_guard_rejects_a_missing_key(tmp_path, monkeypatch):
    monkeypatch.setenv("ORACLE_API_KEY", "s3cret")
    import importlib

    from repo_oracle import app as app_module

    importlib.reload(app_module)
    with TestClient(app_module.app) as c:
        assert c.get("/repos").status_code == 401
        assert c.get("/repos", headers={"X-API-Key": "s3cret"}).status_code == 200
        assert c.get("/healthz").status_code == 200
    monkeypatch.delenv("ORACLE_API_KEY")
    importlib.reload(app_module)


def test_reingesting_replaces_the_index_instead_of_duplicating_it(client, repo, monkeypatch):
    first = ingest_fixture(client, repo, monkeypatch)
    from repo_oracle import ingest
    from repo_oracle.index import open_index

    ix = open_index(ingest.DATA_DIR, first)
    before = ix.count()
    ix.close()

    ingest.JOBS.clear()
    second = ingest_fixture(client, repo, monkeypatch)
    assert second == first
    ix = open_index(ingest.DATA_DIR, second)
    assert ix.count() == before, "a refresh must replace the index, not append to it"
    ix.close()
