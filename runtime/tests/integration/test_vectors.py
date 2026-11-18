"""Vectors in SQLite with sqlite-vec (phase 9.1): int8 candidates, exact reorder, deletion."""

import math
import random
import uuid
from pathlib import Path

from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.vector_index.base import VectorRow
from scrinium.infrastructure.vector_index.sqlite_vec import SqliteVecIndex, retire_legacy_vectors

MODEL = "prueba:embeddings"


def _unit(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector]


def _cosine(a: list[float], b: list[float]) -> float:
    return 1 - sum(x * y for x, y in zip(_unit(a), _unit(b), strict=True))


def test_int8_with_an_exact_reorder_finds_what_an_exact_search_finds(tmp_path: Path) -> None:
    database = Database.open(tmp_path)
    index = SqliteVecIndex(database)
    rng = random.Random(7)  # noqa: S311  reproducible test data, nothing secret
    project, other, source = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    rows = [
        VectorRow(uuid.uuid4(), project if n % 5 else other, source, uuid.uuid4(),
                  [rng.gauss(0, 1) for _ in range(64)])
        for n in range(2000)
    ]  # fmt: skip
    index.upsert(MODEL, rows)
    mine = [row for row in rows if row.project_id == project]
    found = total = 0
    for _ in range(20):
        query = [rng.gauss(0, 1) for _ in range(64)]
        exact = sorted(mine, key=lambda row: _cosine(query, row.vector))[:10]
        got = index.search(MODEL, query, project, 10)
        assert all(chunk in {row.chunk_id for row in mine} for chunk, _ in got)  # its project only
        found += len({row.chunk_id for row in exact} & {chunk for chunk, _ in got})
        total += 10
        # Distances are the exact cosine ones, in order.
        assert [d for _, d in got] == sorted(d for _, d in got)
    assert found / total >= 0.95


def test_deleting_and_resetting(tmp_path: Path) -> None:
    database = Database.open(tmp_path)
    index = SqliteVecIndex(database)
    project, source = uuid.uuid4(), uuid.uuid4()
    row = VectorRow(uuid.uuid4(), project, source, uuid.uuid4(), [1.0, 0.0, 0.0])
    index.upsert(MODEL, [row])
    assert index.search(MODEL, [1.0, 0.0, 0.0], project, 5)[0][0] == row.chunk_id
    index.upsert(MODEL, [row])  # again: replaced, not doubled
    assert index.count(MODEL, 3) == 1
    index.delete_source(source)
    assert index.search(MODEL, [1.0, 0.0, 0.0], project, 5) == [] and index.count(MODEL, 3) == 0
    index.upsert(MODEL, [row])
    index.reset()
    assert index.count(MODEL, 3) == 0 and index.is_ready()


def test_the_old_lancedb_folder_is_deleted_and_vectors_rebuilt(tmp_path: Path) -> None:
    database = Database.open(tmp_path)
    (tmp_path / "vectors" / "chunks_x").mkdir(parents=True)
    (tmp_path / "vectors" / "chunks_x" / "data.lance").write_bytes(b"vectores viejos")
    assert retire_legacy_vectors(database, tmp_path)
    assert not (tmp_path / "vectors").exists()
    assert not retire_legacy_vectors(database, tmp_path)
