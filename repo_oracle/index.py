"""The index: two stores per repository, one for each half of hybrid retrieval.

Dense vectors live in Chroma, an embedded vector database with an HNSW index. Chunk text and
metadata live in SQLite, which also carries the lexical half through FTS5. Reciprocal Rank
Fusion merges the two rankings.

Why two stores rather than one. The dense side wants approximate nearest-neighbour search
over a growing set of vectors, which is exactly what a vector database is built for and what
hand-rolled code gets progressively worse at. The lexical side wants BM25 over an inverted
index, which FTS5 already does inside stdlib `sqlite3`. Neither store is good at the other
job, so each keeps the one it is good at, and the chunk rows stay in SQLite so a citation
can be resolved to source with a plain `SELECT`.

The lexical half is not decoration. Half the questions people ask a codebase are about an
identifier (`UserSerializer`, `--no-verify`, `REDIS_URL`), and an embedding model is *worse*
than exact match at those, because it blurs the token into a neighbourhood of related
concepts. Dense retrieval covers the other half, where the asker does not know the
vocabulary yet. RRF merges them without needing the two score scales to be comparable,
which they are not.

Chroma is embedded on purpose: it is a real vector database with a real ANN index, and it
runs in-process, so there is no service to operate for a single-node deployment. Moving to
a server (Chroma's own, Qdrant, pgvector) means changing `_collection` and `search_dense`
and nothing else, because everything above them consumes ranks.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import chromadb
from chromadb.config import Settings

from .chunk import Chunk

RRF_K = 60  # standard constant; the ranking is insensitive to it between ~20 and ~100
POOL = 40   # candidates pulled from each retriever before fusion
# Map chunks ride *alongside* the top k rather than inside it. Two earlier designs were
# both worse, and the evals said so:
#
#   score boost (x1.15):     hit@5 89%, MRR 0.43  — summaries outranked the source
#   reserved slots inside k: hit@5 89%, MRR 0.59  — summaries displaced the source
#   extra slots outside k:   hit@5 94%, MRR 0.66  — code retrieval untouched
#
# The tiers answer different questions, so making them compete for the same slots was the
# mistake in both. `search` now returns k code hits plus up to MAP_SLOTS summaries, and the
# context budget in chat.py absorbs the difference.
MAP_SLOTS = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    lang TEXT NOT NULL,
    tier TEXT NOT NULL,
    symbols TEXT NOT NULL DEFAULT '',
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, path, symbols, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
"""


@dataclass
class Hit:
    id: int
    path: str
    start_line: int
    end_line: int
    lang: str
    tier: str
    text: str
    score: float
    how: str  # which retriever(s) found it — shown in traces, not to the user

    @property
    def location(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"

    def as_dict(self) -> dict:
        return {
            "id": self.id, "path": self.path, "start_line": self.start_line,
            "end_line": self.end_line, "lang": self.lang, "tier": self.tier,
            "score": round(self.score, 5), "how": self.how, "location": self.location,
        }


# FTS5 has its own query language, and a user question is not written in it. Anything that
# is not a word becomes whitespace, then terms are OR-ed: a question that shares any
# identifier with a chunk should still rank it.
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]{1,}")
_STOP = frozenset("""
the a an of in on for to and or is are was were how what where when why does do did with
this that these those it its i you we they me my our can could should would will there
""".split())


def fts_query(question: str) -> str:
    words = [w for w in _WORD.findall(question) if w.lower() not in _STOP]
    if not words:
        return ""
    # camelCase and snake_case identifiers also get split, so "getUserById" matches a chunk
    # that only says "get_user_by_id" in passing.
    expanded: list[str] = []
    for w in words[:24]:
        expanded.append(w)
        for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", w):
            if len(part) > 2 and part.lower() != w.lower():
                expanded.append(part)
    return " OR ".join(f'"{w}"' for w in dict.fromkeys(expanded))


def _client(root: Path) -> chromadb.ClientAPI:
    """One Chroma client per data directory.

    PersistentClient is a shared instance per path inside chromadb, so calling this on every
    request is cheap; it is not a new database connection each time. Telemetry is off
    because a documentation tool has no business phoning home about someone's private repo.
    """
    root.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(
        path=str(root), settings=Settings(anonymized_telemetry=False, allow_reset=True)
    )


class Index:
    """One repository's index: SQLite for chunks and lexical search, Chroma for vectors."""

    def __init__(self, db_path: Path, name: str | None = None):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

        # Chroma collection names must be 3-512 chars of [a-zA-Z0-9._-] and start and end
        # alphanumeric, which repo ids already satisfy; the prefix covers short names.
        self.name = re.sub(r"[^a-zA-Z0-9._-]", "-", name or self.path.stem)
        self.chroma = _client(self.path.parent / "chroma")
        self.collection = self.chroma.get_or_create_collection(
            name=f"repo-{self.name}",
            # Embeddings are cosine-space; Chroma defaults to L2, which ranks differently
            # for vectors that are not unit length.
            metadata={"hnsw:space": "cosine"},
        )

    # ---- writing -------------------------------------------------------------

    def set_meta(self, **kv) -> None:
        self.db.executemany(
            "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [(k, json.dumps(v)) for k, v in kv.items()],
        )
        self.db.commit()

    def meta(self) -> dict:
        return {r["key"]: json.loads(r["value"]) for r in self.db.execute("SELECT * FROM meta")}

    def add(self, chunks: list[Chunk], vectors: list[list[float]]) -> None:
        """Write a batch of chunks to both stores.

        SQLite is committed first, so a chunk always has a row before it has a vector. The
        reverse order would leave Chroma returning ids that resolve to nothing, which is a
        silent wrong answer; this order leaves a chunk that dense search cannot reach, which
        lexical search still finds. Neither is good, and one is much less bad.
        """
        assert len(chunks) == len(vectors), "every chunk needs exactly one vector"
        cur = self.db.cursor()
        ids = []
        for chunk in chunks:
            cur.execute(
                "INSERT INTO chunks(path,start_line,end_line,lang,tier,symbols,text)"
                " VALUES(?,?,?,?,?,?,?)",
                (chunk.path, chunk.start_line, chunk.end_line, chunk.lang, chunk.tier,
                 chunk.symbols, chunk.text),
            )
            ids.append(cur.lastrowid)
            cur.execute(
                "INSERT INTO chunks_fts(rowid,text,path,symbols) VALUES(?,?,?,?)",
                (cur.lastrowid, chunk.text, chunk.path, chunk.symbols),
            )
        self.db.commit()

        # Only what retrieval filters on goes into Chroma's metadata. The chunk text stays
        # in SQLite: storing it twice doubles the disk for no gain, since a hit is resolved
        # by id anyway.
        self.collection.add(
            ids=[str(i) for i in ids],
            embeddings=vectors,
            metadatas=[{"tier": c.tier, "path": c.path} for c in chunks],
        )

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]

    # ---- reading -------------------------------------------------------------

    def search_lexical(self, question: str, k: int = POOL) -> list[int]:
        query = fts_query(question)
        if not query:
            return []
        try:
            rows = self.db.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (query, k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []  # a query FTS5 still refuses; dense retrieval carries the turn
        return [r["rowid"] for r in rows]

    def search_dense(self, vector: list[float], k: int = POOL) -> list[int]:
        """Approximate nearest neighbours from Chroma, best first."""
        n = self.collection.count()
        if not n:
            return []
        try:
            found = self.collection.query(query_embeddings=[vector], n_results=min(k, n))
        except Exception:
            return []  # a vector-store failure degrades the turn to lexical, not to an error
        return [int(i) for i in found["ids"][0]]

    def search(self, question: str, query_vector: list[float] | None, k: int = 8) -> list[Hit]:
        """Hybrid retrieval: lexical and dense pools, fused by Reciprocal Rank Fusion.

        Returns up to `k` code chunks plus up to `MAP_SLOTS` repository summaries.

        RRF is used instead of score normalisation on purpose. BM25 ranks and cosine
        similarities live on incomparable scales, and any attempt to normalise them needs a
        per-corpus constant nobody will ever retune. RRF only reads ranks.
        """
        lexical = self.search_lexical(question)
        dense = self.search_dense(query_vector) if query_vector else []

        scores: dict[int, float] = {}
        found: dict[int, set[str]] = {}
        for name, ranking in (("lexical", lexical), ("dense", dense)):
            for rank, cid in enumerate(ranking):
                scores[cid] = scores.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
                found.setdefault(cid, set()).add(name)
        if not scores:
            return []

        rows = {
            r["id"]: r for r in self.db.execute(
                f"SELECT * FROM chunks WHERE id IN ({','.join('?' * len(scores))})",
                list(scores),
            )
        }
        hits = [
            Hit(
                id=cid, path=rows[cid]["path"], start_line=rows[cid]["start_line"],
                end_line=rows[cid]["end_line"], lang=rows[cid]["lang"],
                tier=rows[cid]["tier"], text=rows[cid]["text"],
                score=score,
                how="+".join(sorted(found[cid])),
            )
            for cid, score in scores.items() if cid in rows
        ]
        hits.sort(key=lambda h: -h.score)

        code = [h for h in hits if h.tier != "map"][:k]
        maps = [h for h in hits if h.tier == "map"][:MAP_SLOTS]
        return code + maps

    def file_text(self, path: str) -> str | None:
        """Reassemble a file from its chunks, for the source panel.

        The checkout is deleted after ingest, so the index is the only copy left. Chunks
        are contiguous and ordered, so concatenating them in line order rebuilds the file.
        """
        rows = self.db.execute(
            "SELECT start_line, end_line, text FROM chunks WHERE path=? AND tier='code'"
            " ORDER BY start_line", (path,),
        ).fetchall()
        if not rows:
            return None
        lines: list[str] = []
        for row in rows:
            if row["start_line"] <= len(lines):  # overlapping window, skip what we have
                continue
            lines.extend([""] * (row["start_line"] - 1 - len(lines)))
            lines.extend(row["text"].splitlines())
        return "\n".join(lines)

    def paths(self) -> list[str]:
        return [r["path"] for r in self.db.execute(
            "SELECT DISTINCT path FROM chunks WHERE tier='code' ORDER BY path")]

    def drop(self) -> None:
        """Remove this repository from both stores. Used when a repo is deleted or refreshed."""
        try:
            self.chroma.delete_collection(self.collection.name)
        except Exception:
            pass  # already gone, which is the state we wanted
        self.close()
        self.path.unlink(missing_ok=True)

    def close(self) -> None:
        self.db.close()


def open_index(root: Path, repo_id: str) -> Index:
    return Index(Path(root) / f"{repo_id}.db", name=repo_id)

