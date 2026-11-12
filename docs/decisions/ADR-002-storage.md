# ADR-002: SQLite, FTS5 and embedded LanceDB

**Status:** accepted, 2026-11-06.

- SQLite with synchronous SQLAlchemy and Alembic: implemented on 2026-11-07.
- FTS5 and LanceDB: implemented on 2026-11-07.
- Code-symbol search added later (see "How it works").

## The question

Scrinium has to store sources, revisions, chunks, rules with versions and evidence, analyses and audit events. It also has to search them by exact words, by meaning and by function name. What storage does that for one user on one machine, without running database servers?

## What we decided

| Role | Choice |
|---|---|
| **Source of truth** | SQLite, accessed through synchronous SQLAlchemy, with Alembic migrations |
| **Exact search** | SQLite FTS5 |
| **Semantic search** | Embedded LanceDB, a regenerable vector index behind a `VectorIndex` interface (`infrastructure/vector_index/lance.py`) |

The domain layer does not depend on SQLite or LanceDB.

## How it works

**Database**

- The database file is `<data dir>/scrinium.sqlite3`, with WAL and foreign keys enabled on every connection.
- Scrinium applies migrations at startup and refuses to open a database from a newer version.
- Only portable column types are used (UUID, JSON, enums as text with `CHECK`), so the schema could move to another engine. The exception is the append-only triggers, which are SQLite syntax.

**Search**

- `content_chunks` are copied into an FTS5 table (`unicode61 remove_diacritics 2`), kept in sync by triggers.
- Vectors live in `<data dir>/vectors`, one LanceDB table per embedding model and dimension. Search is exhaustive cosine similarity, filtered by project.
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
- **Why a separate vector index, and why LanceDB?** SQLite had no mature vector search when this was decided. LanceDB runs in process, needs no server and works offline (verified with `unshare -rn`). Vectors are regenerable, so the index is a cache, not a source of truth.
- **Why the `VectorIndex` interface?** LanceDB is heavy (about 350 MB with `pyarrow` and `lance`). The interface leaves room to swap it for something lighter without touching the domain.

## Alternatives considered

| Option | Why not |
|---|---|
| **PostgreSQL with pgvector** | One engine for everything, but the user would have to run and secure a database server. |
| **Chroma, Qdrant or another vector server** | Another process to start, secure and keep alive. |
| **sqlite-vec instead of LanceDB** | Would remove ~350 MB of dependencies. It was not mature enough at the time; it is now proposed as a size improvement and would fit behind `VectorIndex`. |
| **Async SQLAlchemy** | More complexity without benefit on SQLite. |
| **No full-text index, embeddings only** | Bad recall for exact identifiers and error messages. |

## Consequences

- Data lives in the user's data directory (`~/.local/share/scrinium` on Linux), never in the project folder.
- **No application-level encryption.** Scrinium only reports whether the disk is encrypted (`core/disk_encryption.py`). No document may claim otherwise.
- Writes are serialized. That is fine for one user, and would need revisiting for concurrent writers.
- LanceDB dominates the installed size.

## Revisit when

- install size becomes a blocker (move to sqlite-vec); or
- hybrid mode needs a shared or remote store.
