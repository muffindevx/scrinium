"""Embedded LanceDB vector index (ADR-002). Derived: it can be deleted and rebuilt.

One table per model and dimension: changing model never mixes incompatible vectors.
"""

import hashlib
import os
import threading
import uuid
import warnings
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Final, Protocol

VECTOR_DIRNAME: Final = "vectors"
# Lance's native code writes warnings with paths to stderr; errors only.
os.environ.setdefault("LANCE_LOG", "error")


@dataclass(frozen=True)
class VectorRow:
    chunk_id: uuid.UUID
    project_id: uuid.UUID
    source_id: uuid.UUID
    revision_id: uuid.UUID
    vector: list[float]


class VectorIndex(Protocol):
    def upsert(self, model_id: str, rows: list[VectorRow]) -> None: ...

    def search(
        self, model_id: str, vector: list[float], project_id: uuid.UUID, limit: int
    ) -> list[tuple[uuid.UUID, float]]: ...

    def delete_source(self, source_id: uuid.UUID) -> None: ...

    def delete_project(self, project_id: uuid.UUID) -> None: ...

    def reset(self) -> None: ...

    def purge(self) -> None: ...


def _table_name(model_id: str, dimension: int) -> str:
    digest = hashlib.sha256(model_id.encode()).hexdigest()[:12]
    return f"chunks_{digest}_{dimension}"


def _quoted_uuid(value: uuid.UUID) -> str:
    # Lance filters are SQL text: only validated UUIDs are interpolated.
    return f"'{uuid.UUID(str(value))}'"


class LanceVectorIndex:
    def __init__(self, data_dir: Path) -> None:
        import lancedb  # deferred import: pyarrow and lance are slow to load

        self._db = lancedb.connect(str(data_dir / VECTOR_DIRNAME))
        self._lock = threading.Lock()

    def _names(self) -> list[str]:
        names: list[str] = list(self._db.list_tables().tables)
        return names

    def _table(self, name: str, dimension: int | None) -> Any:
        if name in self._names():
            return self._db.open_table(name)
        if dimension is None:
            return None
        import pyarrow as pa

        schema = pa.schema(
            [
                pa.field("chunk_id", pa.string()),
                pa.field("project_id", pa.string()),
                pa.field("source_id", pa.string()),
                pa.field("revision_id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), dimension)),
            ]
        )
        return self._db.create_table(name, schema=schema)

    def upsert(self, model_id: str, rows: list[VectorRow]) -> None:
        if not rows:
            return
        dimension = len(rows[0].vector)
        records = [
            {
                "chunk_id": str(row.chunk_id),
                "project_id": str(row.project_id),
                "source_id": str(row.source_id),
                "revision_id": str(row.revision_id),
                "vector": row.vector,
            }
            for row in rows
        ]
        with self._lock:
            table = self._table(_table_name(model_id, dimension), dimension)
            ids = ", ".join(_quoted_uuid(row.chunk_id) for row in rows)
            table.delete(f"chunk_id IN ({ids})")
            table.add(records)

    def search(
        self, model_id: str, vector: list[float], project_id: uuid.UUID, limit: int
    ) -> list[tuple[uuid.UUID, float]]:
        # Under the lock: a purge running alongside would delete the versions being read.
        with self._lock:
            table = self._table(_table_name(model_id, len(vector)), None)
            if table is None:
                return []
            results = (
                table.search(vector)
                .distance_type("cosine")
                .where(f"project_id = {_quoted_uuid(project_id)}", prefilter=True)
                .limit(limit)
                .to_list()
            )
        return [(uuid.UUID(row["chunk_id"]), float(row["_distance"])) for row in results]

    def delete_source(self, source_id: uuid.UUID) -> None:
        with self._lock:
            for name in self._names():
                self._db.open_table(name).delete(f"source_id = {_quoted_uuid(source_id)}")
        self.purge()

    def delete_project(self, project_id: uuid.UUID) -> None:
        with self._lock:
            for name in self._names():
                self._db.open_table(name).delete(f"project_id = {_quoted_uuid(project_id)}")
        self.purge()

    def purge(self) -> None:
        """Lance deletes by creating a new version: the earlier ones, with the vectors, stay on
        disk until it is compacted and every old version is cleaned up."""
        with self._lock, warnings.catch_warnings():
            # It warns that it breaks concurrent reads: there are none here, all take the lock.
            warnings.filterwarnings("ignore", message="optimize\\(cleanup_older_than=0\\)")
            for name in self._names():
                self._db.open_table(name).optimize(
                    cleanup_older_than=timedelta(0), delete_unverified=True
                )

    def reset(self) -> None:
        with self._lock:
            for name in self._names():
                self._db.drop_table(name)
