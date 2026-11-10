"""Hybrid search: FTS5 for exact terms, vectors for meaning, and function names
from the structural map, merged with RRF.

Only chunks of the current revision of active files in the requested project are searched.
"""

import re
import uuid
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from scrinium.application.retrieval.indexing import Indexer
from scrinium.domain.enums import ItemLifecycle
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.sqlite.models import (
    CodeSymbol,
    ContentChunk,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

MAX_QUERY_LENGTH: Final = 500
MAX_TERMS: Final = 32
MAX_RESULTS: Final = 50
# The usual Reciprocal Rank Fusion constant: it damps the weight of the top positions.
_RRF_K: Final = 60
_CANDIDATES_FACTOR: Final = 4
_TERM: Final = re.compile(r"\w+", re.UNICODE)
_MIN_OR_TERM: Final = 3

# bm25 with per-column weights: chunk_id 0, path 2, body 1. Path and body, never chunk_id.
_LEXICAL_SQL: Final = text(
    """
    SELECT f.chunk_id AS chunk_id
    FROM content_chunks_fts AS f
    JOIN content_chunks AS c ON c.id = f.chunk_id
    JOIN source_items AS i ON i.current_revision_id = c.source_revision_id
    JOIN source_connections AS s ON s.id = i.source_connection_id
    WHERE content_chunks_fts MATCH :query
      AND s.project_id = :project_id
      AND i.lifecycle_status = :active
    ORDER BY bm25(content_chunks_fts, 0.0, 2.0, 1.0)
    LIMIT :limit
    """
)


def fts_query(query: str, operator: str) -> str | None:
    """Turns free text into a safe FTS5 query: only quoted words.

    The person's syntax is never passed through: operators, columns or stray quotes would
    error or search internal columns.
    """
    terms = _TERM.findall(query)[:MAX_TERMS]
    if operator == "OR":
        # With OR, "si", "me" or "el" match almost everything: they only pollute the ranking.
        terms = [term for term in terms if len(term) >= _MIN_OR_TERM] or terms
    if not terms:
        return None
    joined = f" {operator} ".join(f'"{term}"' for term in terms)
    return f"{{path body}} : ({joined})"


def lexical(session: Session, project_id: uuid.UUID, query: str, limit: int) -> list[uuid.UUID]:
    for operator in ("AND", "OR"):
        expression = fts_query(query, operator)
        if expression is None:
            return []
        rows = session.execute(
            _LEXICAL_SQL,
            {
                "query": expression,
                "project_id": project_id.hex,
                "active": ItemLifecycle.ACTIVE.value,
                "limit": limit,
            },
        ).all()
        if rows:
            return [uuid.UUID(row.chunk_id) for row in rows]
    return []


def _normalized(name: str) -> str:
    """`calculate_total`, `calculateTotal` and `CalculateTotal` are the same name."""
    return name.replace("_", "").replace("-", "").lower()


def symbols(session: Session, project_id: uuid.UUID, query: str, limit: int) -> list[uuid.UUID]:
    """Chunks where a function, method or class with a name from the query starts."""
    names = {_normalized(term) for term in _TERM.findall(query)[:MAX_TERMS] if len(term) >= 3}
    if not names:
        return []
    normalized = func.lower(func.replace(func.replace(CodeSymbol.name, "_", ""), "-", ""))
    rows = session.execute(
        select(ContentChunk.id, CodeSymbol.name)
        .join(CodeSymbol, CodeSymbol.source_revision_id == ContentChunk.source_revision_id)
        .join(SourceItem, SourceItem.current_revision_id == CodeSymbol.source_revision_id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            normalized.in_(names),
            ContentChunk.locator["start_line"].as_integer() <= CodeSymbol.start_line,
            ContentChunk.locator["end_line"].as_integer() >= CodeSymbol.start_line,
        )
        .limit(limit)
    ).all()
    exact = set(_TERM.findall(query))
    # First those that match what was typed letter by letter.
    ordered = sorted(rows, key=lambda row: row.name not in exact)
    return list(dict.fromkeys(row.id for row in ordered))


def _searchable(
    session: Session, project_id: uuid.UUID, chunk_ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Filters candidates from the vector index, which also stores old revisions."""
    if not chunk_ids:
        return set()
    rows = session.scalars(
        select(ContentChunk.id)
        .join(SourceItem, SourceItem.current_revision_id == ContentChunk.source_revision_id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            ContentChunk.id.in_(chunk_ids),
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
        )
    )
    return set(rows)


@dataclass(frozen=True)
class SearchHit:
    chunk: ContentChunk
    revision: SourceRevision
    source_id: uuid.UUID
    score: float
    matched_by: list[str]


@dataclass
class SearchResult:
    hits: list[SearchHit] = field(default_factory=list)
    semantic: str = ModelStatus.UNAVAILABLE.value


def search(
    session: Session, indexer: Indexer, project_id: uuid.UUID, query: str, limit: int
) -> SearchResult:
    candidates = limit * _CANDIDATES_FACTOR
    rankings: dict[str, list[uuid.UUID]] = {
        "symbol": symbols(session, project_id, query, candidates),
        "lexical": lexical(session, project_id, query, candidates),
    }

    result = SearchResult()
    status = indexer.provider_status()
    result.semantic = status.value
    if status is ModelStatus.READY:
        try:
            vector = indexer.provider.embed_query(query)
            hits = indexer.vector_index.search(
                indexer.provider.model_id, vector, project_id, candidates
            )
            allowed = _searchable(session, project_id, [chunk_id for chunk_id, _ in hits])
            rankings["semantic"] = [chunk_id for chunk_id, _ in hits if chunk_id in allowed]
        except ModelError:
            result.semantic = ModelStatus.UNAVAILABLE.value

    scores: dict[uuid.UUID, float] = {}
    matched: dict[uuid.UUID, list[str]] = {}
    for name, ranked in rankings.items():
        for position, chunk_id in enumerate(ranked):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (_RRF_K + position + 1)
            matched.setdefault(chunk_id, []).append(name)
    ordered = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)[:limit]
    if not ordered:
        return result

    rows = session.execute(
        select(ContentChunk, SourceRevision, SourceItem.source_connection_id)
        .join(SourceRevision, SourceRevision.id == ContentChunk.source_revision_id)
        .join(SourceItem, SourceItem.id == SourceRevision.source_item_id)
        .where(ContentChunk.id.in_(ordered))
    ).all()
    by_id = {chunk.id: (chunk, revision, source_id) for chunk, revision, source_id in rows}
    for chunk_id in ordered:
        if chunk_id in by_id:
            chunk, revision, source_id = by_id[chunk_id]
            result.hits.append(
                SearchHit(chunk, revision, source_id, scores[chunk_id], matched[chunk_id])
            )
    return result
