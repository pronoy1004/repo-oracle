"""The index: one SQLite file per repository, holding both halves of hybrid retrieval.

Why SQLite and not a vector database. A repository of 5,000 files produces on the order of
20,000 chunks. A brute-force cosine over 20,000 x 768 float32 is one numpy matmul, about
15 MB of RAM and single-digit milliseconds — faster than the network hop to a vector
service, and it adds no container, no client library, and no schema to keep in sync. What a
vector DB buys is approximate search above roughly a million vectors, and we are two orders
of magnitude below that.

ponytail: brute force + FTS5, honest ceiling ~100k chunks per repo (about 300ms/query).
Past that, swap `search_dense` for pgvector or an HNSW index; the fusion and the callers do
not change.

The lexical half is FTS5, which ships inside stdlib sqlite3. It is not decoration: half the
questions people ask a codebase are about an identifier (`UserSerializer`, `--no-verify`,
`REDIS_URL`), and an embedding model is *worse* than exact match at those. Dense retrieval
covers the other half, where the asker does not know the vocabulary. Reciprocal Rank Fusion
merges them without needing the two score scales to be comparable, which they are not.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .chunk import Chunk

RRF_K = 60  # standard constant; the ranking is insensitive to it between ~20 and ~100
POOL = 40   # candidates pulled from each retriever before fusion
# The map tier is a handful of LLM-written summaries competing with thousands of code
# chunks. Without a nudge it never surfaces; with too much it drowns the code. 1.15 was
# picked by running evals/run.py at 1.0, 1.15, 1.5 and keeping the best hit-rate.
TIER_WEIGHT = {"code": 1.0, "map": 1.15}

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
CREATE TABLE IF NOT EXISTS vectors (id INTEGER PRIMARY KEY, vec BLOB NOT NULL);
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


class Index:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self._matrix: np.ndarray | None = None
        self._ids: np.ndarray | None = None

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
        """Insert chunks and their embeddings together. One transaction, so a crash
        mid-ingest leaves no chunk without a vector."""
        assert len(chunks) == len(vectors), "every chunk needs exactly one vector"
        cur = self.db.cursor()
        for chunk, vec in zip(chunks, vectors):
            cur.execute(
                "INSERT INTO chunks(path,start_line,end_line,lang,tier,symbols,text)"
                " VALUES(?,?,?,?,?,?,?)",
                (chunk.path, chunk.start_line, chunk.end_line, chunk.lang, chunk.tier,
                 chunk.symbols, chunk.text),
            )
            rowid = cur.lastrowid
            cur.execute(
                "INSERT INTO chunks_fts(rowid,text,path,symbols) VALUES(?,?,?,?)",
                (rowid, chunk.text, chunk.path, chunk.symbols),
            )
            arr = np.asarray(vec, dtype=np.float32)
            norm = float(np.linalg.norm(arr))
            if norm:
                arr = arr / norm  # store normalised, so cosine is a plain dot product later
            cur.execute("INSERT INTO vectors(id,vec) VALUES(?,?)", (rowid, arr.tobytes()))
        self.db.commit()
        self._matrix = self._ids = None

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]

    # ---- reading -------------------------------------------------------------

    def _vectors(self) -> tuple[np.ndarray, np.ndarray]:
        """All vectors as one matrix, loaded once and kept. A 20k-chunk repo is ~60MB."""
        if self._matrix is None:
            rows = self.db.execute("SELECT id, vec FROM vectors ORDER BY id").fetchall()
            self._ids = np.array([r["id"] for r in rows], dtype=np.int64)
            self._matrix = (
                np.vstack([np.frombuffer(r["vec"], dtype=np.float32) for r in rows])
                if rows else np.zeros((0, 1), dtype=np.float32)
            )
        return self._matrix, self._ids

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
        matrix, ids = self._vectors()
        if not len(matrix) or matrix.shape[1] == 1:
            return []
        q = np.asarray(vector, dtype=np.float32)
        norm = float(np.linalg.norm(q))
        if not norm:
            return []
        sims = matrix @ (q / norm)
        top = np.argpartition(-sims, min(k, len(sims) - 1))[:k]
        return [int(ids[i]) for i in top[np.argsort(-sims[top])]]

    def search(self, question: str, query_vector: list[float] | None, k: int = 8) -> list[Hit]:
        """Hybrid retrieval: lexical and dense pools, fused by Reciprocal Rank Fusion.

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
                score=score * TIER_WEIGHT.get(rows[cid]["tier"], 1.0),
                how="+".join(sorted(found[cid])),
            )
            for cid, score in scores.items() if cid in rows
        ]
        hits.sort(key=lambda h: -h.score)
        return hits[:k]

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

    def close(self) -> None:
        self.db.close()


def open_index(root: Path, repo_id: str) -> Index:
    return Index(Path(root) / f"{repo_id}.db")


def stamp(index: Index, **kv) -> None:
    index.set_meta(indexed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **kv)
