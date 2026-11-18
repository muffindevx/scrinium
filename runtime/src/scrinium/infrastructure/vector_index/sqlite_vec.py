"""The vector index inside Scrinium's SQLite database, with sqlite-vec (phase 9.1, P1 + P3).

One store instead of two: deleting, backing up and keeping things consistent are the
database's own, and LanceDB (pyarrow and lance, ~400 MB installed) is gone.

Per embedding model and dimension, two tables:

- `vec_<model>_<dim>`: a `vec0` virtual table with each chunk's vector quantized to **int8**,
  partitioned by project. A search reads it to find candidates, four times as many as asked;
- `vecf_<model>_<dim>`: the full float32 vector of each chunk, with its project and source.
  The candidates are **reordered exactly** by cosine distance with these.

Vectors are L2-normalized before storing: the int8 quantization (`'unit'`) expects values in
[-1, 1], and on unit vectors the L2 order is the cosine order. The tables are not in the
models or the migrations: they are derived, created on the first vector of a model, dropped by
`reset`, and left out of exports like the other indexes.
"""

# Table names are built from a SHA-256 hex digest and an integer dimension, and columns are
# fixed: nothing from outside reaches the SQL text, only bound parameters.
# ruff: noqa: S608

import hashlib
import math
import shutil
import struct
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Connection

from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.vector_index.base import LEGACY_VECTOR_DIRNAME, VectorRow

TABLE_PREFIX: Final = "vec"
# Candidates read from the int8 index per result asked for, before the exact reorder.
_OVERSAMPLE: Final = 4


def _names(model_id: str, dimension: int) -> tuple[str, str]:
    digest = hashlib.sha256(model_id.encode()).hexdigest()[:12]
    return f"vec_{digest}_{dimension}", f"vecf_{digest}_{dimension}"


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _blob(vector: list[float]) -> bytes:
    return struct.pack(f"{len(vector)}f", *vector)


def _floats(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"{len(blob) // 4}f", blob)


class SqliteVecIndex:
    def __init__(self, database: Database) -> None:
        self._database = database
        # Writes take it; SQLite already serializes them, this keeps the two tables in step.
        self._lock = threading.Lock()

    @contextmanager
    def _connection(self) -> Iterator[Connection]:
        with self._database.engine.begin() as connection:
            yield connection

    def _tables(self, connection: Connection) -> list[str]:
        rows = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name LIKE 'vecf\\_%' ESCAPE '\\'"
        )
        return [str(row[0]) for row in rows]

    def _ensure(self, connection: Connection, model_id: str, dimension: int) -> tuple[str, str]:
        vec, floats = _names(model_id, dimension)
        connection.exec_driver_sql(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {vec} USING vec0("
            f"chunk_id text primary key, project_id text partition key, "
            f"embedding int8[{dimension}])"
        )
        connection.exec_driver_sql(
            f"CREATE TABLE IF NOT EXISTS {floats} (chunk_id TEXT PRIMARY KEY, "
            f"project_id TEXT NOT NULL, source_id TEXT NOT NULL, vector BLOB NOT NULL)"
        )
        connection.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS ix_{floats}_source ON {floats} (source_id)"
        )
        connection.exec_driver_sql(
            f"CREATE INDEX IF NOT EXISTS ix_{floats}_project ON {floats} (project_id)"
        )
        return vec, floats

    def upsert(self, model_id: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        dimension = len(rows[0].vector)
        with self._lock, self._connection() as connection:
            vec, floats = self._ensure(connection, model_id, dimension)
            for row in rows:
                chunk = str(row.chunk_id)
                blob = _blob(_unit(row.vector))
                connection.exec_driver_sql(f"DELETE FROM {vec} WHERE chunk_id = ?", (chunk,))
                connection.exec_driver_sql(
                    f"INSERT INTO {vec} (chunk_id, project_id, embedding) "
                    f"VALUES (?, ?, vec_quantize_int8(?, 'unit'))",
                    (chunk, str(row.project_id), blob),
                )
                connection.exec_driver_sql(
                    f"INSERT OR REPLACE INTO {floats} (chunk_id, project_id, source_id, vector) "
                    f"VALUES (?, ?, ?, ?)",
                    (chunk, str(row.project_id), str(row.source_id), blob),
                )

    def search(
        self, model_id: str, vector: list[float], project_id: uuid.UUID, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        vec, floats = _names(model_id, len(vector))
        query = _unit(vector)
        with self._connection() as connection:
            if floats not in self._tables(connection):
                return []
            candidates = [
                str(row[0])
                for row in connection.exec_driver_sql(
                    f"SELECT chunk_id FROM {vec} WHERE embedding MATCH "
                    f"vec_quantize_int8(?, 'unit') AND k = ? AND project_id = ?",
                    (_blob(query), limit * _OVERSAMPLE, str(project_id)),
                )
            ]
            if not candidates:
                return []
            marks = ", ".join("?" for _ in candidates)
            stored = connection.exec_driver_sql(
                f"SELECT chunk_id, vector FROM {floats} WHERE chunk_id IN ({marks})",
                tuple(candidates),
            ).all()
        # Exact cosine distance on the full vectors (both are unit vectors: 1 - dot).
        scored = [
            (uuid.UUID(chunk), 1.0 - sum(a * b for a, b in zip(query, _floats(blob), strict=True)))
            for chunk, blob in stored
        ]
        scored.sort(key=lambda pair: pair[1])
        return scored[:limit]

    def _delete_where(self, column: str, value: uuid.UUID) -> None:
        with self._lock, self._connection() as connection:
            for floats in self._tables(connection):
                vec = "vec_" + floats.removeprefix("vecf_")
                connection.exec_driver_sql(
                    f"DELETE FROM {vec} WHERE chunk_id IN "
                    f"(SELECT chunk_id FROM {floats} WHERE {column} = ?)",
                    (str(value),),
                )
                connection.exec_driver_sql(
                    f"DELETE FROM {floats} WHERE {column} = ?", (str(value),)
                )

    def delete_source(self, source_id: uuid.UUID) -> None:
        self._delete_where("source_id", source_id)

    def delete_project(self, project_id: uuid.UUID) -> None:
        self._delete_where("project_id", project_id)

    def reset(self) -> None:
        with self._lock, self._connection() as connection:
            for floats in self._tables(connection):
                connection.exec_driver_sql(
                    f"DROP TABLE IF EXISTS vec_{floats.removeprefix('vecf_')}"
                )
                connection.exec_driver_sql(f"DROP TABLE IF EXISTS {floats}")

    def purge(self) -> None:
        """Deleted rows are zeroed by `secure_delete`; the WAL is the database's purge."""
        self._database.purge()

    def is_ready(self) -> bool:
        try:
            with self._connection() as connection:
                connection.exec_driver_sql("SELECT vec_version()").scalar()
        except Exception:
            return False
        return True

    def count(self, model_id: str, dimension: int) -> int:
        _vec, floats = _names(model_id, dimension)
        with self._connection() as connection:
            if floats not in self._tables(connection):
                return 0
            value: Any = connection.exec_driver_sql(f"SELECT count(*) FROM {floats}").scalar()
            return int(value or 0)


def retire_legacy_vectors(database: Database, data_dir: Path) -> bool:
    """LanceDB's folder, from before phase 9.1: deleted from disk, and every chunk marked for
    a new vector, which the next embedding pass computes into SQLite. True if there was one."""
    legacy = data_dir / LEGACY_VECTOR_DIRNAME
    if not legacy.is_dir():
        return False
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE content_chunks SET embedding_model = NULL, embedding_dimension = NULL"
        )
    shutil.rmtree(legacy, ignore_errors=True)
    return True
