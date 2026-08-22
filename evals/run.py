"""Score retrieval against hand-labelled questions.

Retrieval is scored, not the generated answer, and that is deliberate. Answer quality is
expensive to judge and moves with the model; retrieval either put the right file in the
context window or it did not, and every answer failure that matters starts there. If the
right file is never retrieved, no prompt fixes it.

    python evals/run.py                      # score the default question set
    python evals/run.py --k 10 --ingest      # re-index the eval repo first

Reported: hit rate at k (did any expected file appear), precision of the top result, and
MRR. A regression in these numbers is the signal to look at chunking or fusion.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from repo_oracle import ingest, llm  # noqa: E402
from repo_oracle.index import open_index  # noqa: E402

SET = Path(__file__).parent / "questions.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--ingest", action="store_true", help="index the eval repo before scoring")
    ap.add_argument("--file", default=str(SET))
    args = ap.parse_args()

    data = json.loads(Path(args.file).read_text())
    repo_url = data["repo"]
    rid = ingest.repo_id(repo_url, None)

    if args.ingest or not (ingest.DATA_DIR / f"{rid}.db").exists():
        print(f"indexing {repo_url} …")
        job = ingest.start(repo_url)
        while job.status == "running":
            time.sleep(2)
        if job.status != "done":
            print(f"ingest failed: {job.error}")
            return 1

    index = open_index(ingest.DATA_DIR, rid)
    hits = first_hits = 0
    mrr = 0.0
    rows = []

    for case in data["questions"]:
        vector = llm.embed([case["q"]], task="retrieval_query")[0]
        found = index.search(case["q"], vector, k=args.k)
        paths = [h.path for h in found]
        expected = set(case["expect"])
        rank = next((i + 1 for i, p in enumerate(paths) if p in expected), 0)
        hits += bool(rank)
        first_hits += bool(paths and paths[0] in expected)
        mrr += 1 / rank if rank else 0
        rows.append((case["q"], rank, paths[:3]))

    n = len(data["questions"])
    print(f"\n{'rank':>5}  question")
    for q, rank, paths in rows:
        mark = str(rank) if rank else "miss"
        print(f"{mark:>5}  {q[:66]}")
        if not rank:
            print(f"         got: {', '.join(paths)}")

    print(f"\nquestions      {n}")
    print(f"hit@{args.k:<10} {hits / n:.0%}")
    print(f"top-1 hit      {first_hits / n:.0%}")
    print(f"MRR            {mrr / n:.2f}")
    index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
