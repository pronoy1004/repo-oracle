"""Context assembly and citation handling — the parts of a turn that are not the model."""

from repo_oracle.chat import build_context, cited_paths, rewrite_query
from repo_oracle.index import Hit


def hit(path: str, text: str, score: float = 1.0, tier: str = "code") -> Hit:
    return Hit(1, path, 1, 10, "python", tier, text, score, "dense")


def test_context_stops_at_the_budget():
    hits = [hit(f"f{i}.py", "x" * 400) for i in range(50)]
    context, used = build_context(hits, budget_tokens=200)  # 800 chars
    assert len(used) < len(hits)
    assert len(context) <= 200 * 4 + 200


def test_one_huge_chunk_does_not_starve_the_rest():
    hits = [hit("huge.py", "x" * 5000), hit("small.py", "def useful(): ...")]
    _, used = build_context(hits, budget_tokens=500)
    assert [h.path for h in used] == ["small.py"]


def test_map_chunks_are_labelled_so_the_model_can_weigh_them():
    context, _ = build_context([hit("codebase-map/architecture.md", "overview", tier="map")])
    assert "[map]" in context


def test_citations_are_extracted_in_order_and_deduplicated():
    answer = "Routes register in `src/app.py:42`, dispatched at `src/app.py:99-120`, see `src/app.py:42`."
    assert cited_paths(answer) == [
        {"path": "src/app.py", "line": 42, "end": 42},
        {"path": "src/app.py", "line": 99, "end": 120},
    ]


def test_rewrite_is_skipped_when_there_is_no_history():
    # No history means nothing to resolve, so the turn should not pay for a model call.
    assert rewrite_query("where are routes registered?", []) == "where are routes registered?"


def test_rewrite_falls_back_to_the_raw_question_when_the_model_fails(monkeypatch):
    from repo_oracle import chat

    monkeypatch.setattr(chat.llm, "complete", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("429")))
    history = [{"role": "user", "content": "what is app.py"}]
    assert chat.rewrite_query("and how does it start?", history) == "and how does it start?"
