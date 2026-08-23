# Diagrams

Generated with [`draw-diagrams`](https://github.com/pronoy1004/codebase-cartography/blob/main/skills/draw-diagrams/SKILL.md),
an Agent Skill from my [codebase-cartography](https://github.com/pronoy1004/codebase-cartography)
project, pointed at this repo. Every block renders in GitHub.

## System components

```mermaid
flowchart TD
    UI["React UI<br/>web/src"] -->|"HTTP + SSE"| API["FastAPI app<br/>repo_oracle/app.py"]
    API -->|"starts a thread"| ING["ingest.py"]
    API -->|"per turn"| CHAT["chat.py"]
    ING -->|"shallow clone"| GIT[("git host")]
    ING -->|"walk and split"| CH["chunk.py"]
    ING -->|"3 summary passes"| MAP["mapper.py"]
    CH -->|"chunks"| IDX["index.py"]
    MAP -->|"tier=map chunks"| IDX
    IDX -->|"vectors"| CHROMA[("Chroma<br/>HNSW")]
    IDX -->|"rows + FTS5"| SQL[("SQLite")]
    CHAT -->|"rank fusion"| IDX
    CHAT -->|"embed + stream"| LLM["llm.py"]
    MAP -->|"complete"| LLM
    LLM -->|"litellm"| GEM[("Gemini API")]
    CHAT -->|"one line per turn"| TR[("traces.jsonl")]
```

The two stores are the only state that outlives a request. The checkout is deleted when
ingest finishes.

## Flow: ingest a repository

```mermaid
sequenceDiagram
    participant U as Browser
    participant A as app.py
    participant I as ingest.py
    participant K as checkout.py
    participant X as index.py
    participant L as llm.py

    U->>A: POST /repos {url}
    A->>I: start() on a background thread
    A-->>U: 202 {repo_id}
    U->>A: GET /repos/{id}/events (SSE)
    I->>K: clone(url, ref)
    K-->>I: path + cleanup callable
    I->>I: chunk_repo() splits on declarations
    loop every 96 chunks, retried with backoff
        I->>L: embed(batch)
        I->>X: add(chunks, vectors)
        I-->>U: progress event
    end
    I->>L: 3 summary passes over a digest
    I->>X: add(map chunks)
    I->>K: cleanup() deletes the checkout
    I-->>U: done event
```

Each batch commits as it lands, so a rate limit costs the remaining batches, not the run.

## Flow: a chat turn

```mermaid
sequenceDiagram
    participant U as Browser
    participant A as app.py
    participant C as chat.py
    participant L as llm.py
    participant X as index.py

    U->>A: POST /chat {repo_id, message}
    A->>C: answer()
    C->>L: rewrite the follow-up into a standalone query
    C->>L: embed(query)
    C->>X: search(query, vector)
    X->>X: FTS5 ranking
    X->>X: Chroma ANN ranking
    X-->>C: hits fused by RRF
    C-->>U: sources event
    C->>C: fit hits into a 12k token budget
    C->>L: stream(system + excerpts + question)
    loop each token
        C-->>U: token event
    end
    C->>C: parse path:line citations
    C-->>U: done event with citations
```

Retrieval is reported to the browser before generation starts, so a wrong answer can be
blamed on the right stage.

## Module dependencies

```mermaid
flowchart LR
    app -->|"starts and polls jobs"| ingest
    app -->|"drives a turn"| chat
    app -->|"opens, reads, drops"| index
    app -->|"serves recent"| trace
    ingest -->|"clone or resolve path"| checkout
    ingest -->|"split the tree"| chunk
    ingest -->|"summary passes"| mapper
    ingest -->|"write both stores"| index
    ingest -->|"embed batches"| llm
    mapper -->|"chunk the summaries"| chunk
    mapper -->|"complete"| llm
    chat -->|"hybrid search"| index
    chat -->|"rewrite, embed, stream"| llm
    chat -->|"record the turn"| trace
    index -->|"Chunk dataclass"| chunk
```

No cycles. `llm.py` is the only module that reaches the network, which is what makes the
test suite offline.

## Data model

```mermaid
erDiagram
    REPO ||--|| SQLITE_DB : "one file per repo"
    REPO ||--|| CHROMA_COLLECTION : "one collection per repo"
    SQLITE_DB ||--o{ CHUNK : contains
    CHUNK ||--|| FTS_ROW : "mirrored for BM25"
    CHUNK ||--|| VECTOR : "same id in Chroma"
    SQLITE_DB ||--o{ META : "commit, counts, models"

    CHUNK {
        int id PK
        string path
        int start_line
        int end_line
        string lang
        string tier "code or map"
        string symbols
        string text
    }
    VECTOR {
        string id PK "stringified chunk id"
        float768 embedding
        string tier
        string path
    }
    META {
        string key PK
        json value
    }
```

A chunk id is the join key across both stores, which is why SQLite is written first.
