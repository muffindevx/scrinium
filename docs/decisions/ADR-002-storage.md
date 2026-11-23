# ADR-002: SQLite, FTS5 and embedded vectors

**Status:** accepted, 2026-11-06.

- SQLite with synchronous SQLAlchemy and Alembic: implemented on 2026-11-07.
- FTS5 and LanceDB: implemented on 2026-11-07.
- 2026-11-17 (phase 9.1): vectors moved from LanceDB to **sqlite-vec** 0.1.9 inside the same database: int8 vectors in a `vec0` table, partitioned by project, give 4× candidates, and the float32 vectors reorder them exactly by cosine. One transactional store; LanceDB's folder is deleted on first start and its vectors rebuilt. Installed size: 482 MB → 78 MB. 20 000 vectors of 1 024 dimensions: search p95 40 ms; recall@10 against an exact search ≥ 95 % (test).
- Code-symbol search added later (see "How it works").

## The question

Scrinium has to store sources, revisions, chunks, rules with versions and evidence, analyses and audit events. It also has to search them by exact words, by meaning and by function name. What storage does that for one user on one machine, without running database servers?

## What we decided

| Role | Choice |
|---|---|
| **Source of truth** | SQLite, accessed through synchronous SQLAlchemy, with Alembic migrations |
| **Exact search** | SQLite FTS5 |
| **Semantic search** | sqlite-vec inside the same SQLite database, a regenerable vector index behind a `VectorIndex` interface (`infrastructure/vector_index/sqlite_vec.py`). LanceDB until 2026-11-17 |

The domain layer does not depend on SQLite or sqlite-vec.

## How it works

**Database**

- The database file is `<data dir>/scrinium.sqlite3`, with WAL and foreign keys enabled on every connection.
- Scrinium applies migrations at startup and refuses to open a database from a newer version.
- Only portable column types are used (UUID, JSON, enums as text with `CHECK`), so the schema could move to another engine. The exception is the append-only triggers, which are SQLite syntax.

**Search**

- `content_chunks` are copied into an FTS5 table (`unicode61 remove_diacritics 2`), kept in sync by triggers.
- Vectors live in the same database file, in two tables per embedding model and dimension: int8 vectors in a sqlite-vec `vec0` table partitioned by project, and float32 vectors beside them. A search takes 4× the candidates from the int8 table and reorders them by exact cosine similarity. These tables are left out of the models, the migrations and the privacy export.
- Search merges **three** rankings with Reciprocal Rank Fusion (`k = 60`):
  - code symbols, matched by function name;
  - FTS5 (lexical);
  - vectors (semantic), when the embedding model is available.

  See `application/retrieval/search.py`.

**What can be rebuilt**

Chunks, code symbols, vectors and the extraction queue are derived data. They can be deleted and rebuilt from the stored revisions, and they are left out of the privacy export.

## Why

- **Does one user need a database server?** No. PostgreSQL, Redis or S3 would mean installing, starting, upgrading and securing services that a single process does not need. SQLite is a file, ships with Python and handles this load easily.
- **Why synchronous SQLAlchemy?** SQLite gains nothing from async: it serializes writes anyway. Sync code is simpler to test and reason about. (Owner's decision.)
- **Why FTS5 inside SQLite?** Exact matches (identifiers, error codes, ticket keys) are where embeddings fail. FTS5 is already in SQLite: SQLite 3.44.1 on the reference machine includes it.
- **Why vectors inside SQLite, with sqlite-vec?** One transactional store: deleting a project, exporting and keeping data consistent need no second store to clean up. It runs in process and offline, and it cut the installed size from 482 MB to 78 MB. Vectors are regenerable, so the index is a cache, not a source of truth.
- **Why int8 candidates and a float32 reorder?** int8 is a quarter of the size and fast to scan; reordering the candidates by exact cosine keeps recall@10 at 95 % or more against an exact search.
- **Why the `VectorIndex` interface?** It let the move from LanceDB to sqlite-vec touch one module and not the domain.

## Alternatives considered

| Option | Why not |
|---|---|
| **PostgreSQL with pgvector** | One engine for everything, but the user would have to run and secure a database server. |
| **Chroma, Qdrant or another vector server** | Another process to start, secure and keep alive. |
| **Embedded LanceDB** | Used from 2026-11-07 to 2026-11-17, when sqlite-vec was not mature enough. A second store beside SQLite and about 400 MB of dependencies (`pyarrow` and `lance`). |
| **Async SQLAlchemy** | More complexity without benefit on SQLite. |
| **No full-text index, embeddings only** | Bad recall for exact identifiers and error messages. |

## Consequences

- Data lives in the user's data directory (`~/.local/share/scrinium` on Linux), never in the project folder.
- **No application-level encryption.** Scrinium only reports whether the disk is encrypted (`core/disk_encryption.py`). No document may claim otherwise.
- Writes are serialized. That is fine for one user, and would need revisiting for concurrent writers.
- Vectors share SQLite's single writer; rebuilding them goes through the durable job queue.

## Revisit when

- search needs approximate indexes beyond what an int8 scan per project gives; or
- hybrid mode needs a shared or remote store.
