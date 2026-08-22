"""Retrieval: lexical, dense, and the fusion between them."""

import numpy as np
import pytest

from repo_oracle.chunk import Chunk
from repo_oracle.index import RRF_K, Index, fts_query


def fake_vec(seed: int, dims: int = 8) -> list[float]:
    rng = np.random.default_rng(seed)
    return list(rng.random(dims).astype(float))


@pytest.fixture
def index(tmp_path):
    ix = Index(tmp_path / "t.db")
    chunks = [
        Chunk("auth.py", 1, 20, "python", "def login(user):\n    return verify_password(user)", symbols="login"),
        Chunk("router.py", 1, 15, "python", "def register_routes(app):\n    app.add_url_rule('/chat')"),
        Chunk("README.md", 1, 5, "markdown", "A small service for asking questions."),
    ]
    ix.add(chunks, [fake_vec(i) for i in range(len(chunks))])
    yield ix
    ix.close()


def test_lexical_finds_an_identifier_an_embedding_would_blur(index):
    hits = index.search_lexical("where is register_routes defined")
    assert hits, "FTS5 should match the identifier"
    assert index.db.execute("SELECT path FROM chunks WHERE id=?", (hits[0],)).fetchone()[0] == "router.py"


def test_fts_query_drops_stopwords_and_splits_identifiers():
    query = fts_query("How does getUserById work?")
    assert "the" not in query.lower().split('"')
    assert '"getUserById"' in query and '"User"' in query


def test_fts_query_of_a_pure_stopword_question_is_empty_not_broken():
    assert fts_query("how does it do that?") == ""


def test_dense_search_ranks_the_nearest_vector_first(index):
    # Query with a chunk's own embedding: the vector store must return that chunk first.
    second = index.db.execute("SELECT id FROM chunks ORDER BY id").fetchall()[1]["id"]
    assert index.search_dense(fake_vec(1))[0] == second


def test_dense_search_is_empty_before_anything_is_indexed(tmp_path):
    ix = Index(tmp_path / "empty.db")
    assert ix.search_dense(fake_vec(0)) == []
    ix.close()


def test_drop_clears_both_stores(tmp_path):
    ix = Index(tmp_path / "gone.db")
    ix.add([Chunk("a.py", 1, 3, "python", "def a(): ...")], [fake_vec(3)])
    assert ix.count() == 1 and ix.collection.count() == 1
    ix.drop()

    # Re-opening the same id must come back empty, not half-populated. A stale Chroma
    # collection surviving a delete is how a "refreshed" repo starts citing files that no
    # longer exist.
    again = Index(tmp_path / "gone.db")
    assert again.count() == 0 and again.collection.count() == 0
    again.close()


def test_hybrid_fusion_scores_a_chunk_both_retrievers_found_above_either_alone(index):
    hits = index.search("login verify_password", fake_vec(0), k=3)
    assert hits[0].path == "auth.py"
    assert hits[0].how == "dense+lexical"
    # RRF: rank-1 in both pools beats rank-1 in one pool.
    assert hits[0].score > 1.0 / (RRF_K + 1)


def test_search_survives_a_question_with_no_searchable_words(index):
    assert index.search("how does it?", None) == []


def test_file_text_reassembles_the_original_lines(tmp_path):
    ix = Index(tmp_path / "f.db")
    text = "\n".join(f"line {i}" for i in range(1, 41))
    from repo_oracle.chunk import chunk_file

    chunks = chunk_file("a.txt", text)
    ix.add(chunks, [fake_vec(i) for i in range(len(chunks))])
    assert ix.file_text("a.txt") == text
    assert ix.file_text("missing.txt") is None
    ix.close()


def test_map_chunks_ride_alongside_k_and_never_displace_code(tmp_path):
    # Summaries orient an answer; source files are the answer. Letting summaries compete for
    # the same k measurably hurt retrieval, so they are additional (see MAP_SLOTS).
    from repo_oracle.index import MAP_SLOTS

    ix = Index(tmp_path / "m.db")
    code = [Chunk(f"f{i}.py", 1, 9, "python", f"def login{i}(): pass  # routing") for i in range(4)]
    maps = [
        Chunk(f"codebase-map/doc{i}.md", 1, 9, "markdown", f"login and routing overview {i}", tier="map")
        for i in range(5)
    ]
    ix.add(code + maps, [fake_vec(i) for i in range(len(code) + len(maps))])

    hits = ix.search("login routing overview", fake_vec(0), k=4)
    assert sum(h.tier == "code" for h in hits) == 4, "code must get its full k regardless"
    assert sum(h.tier == "map" for h in hits) == MAP_SLOTS
    ix.close()
