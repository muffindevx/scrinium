"""What every vector index offers (ADR-002): derived data, deleted and rebuilt at will."""

import uuid
from dataclasses import dataclass
from typing import Final, Protocol

# Where LanceDB kept its vectors until phase 9.1: only to retire it and to report its size.
LEGACY_VECTOR_DIRNAME: Final = "vectors"


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

    def is_ready(self) -> bool: ...
