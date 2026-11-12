"""Chunks and vectors derived from the current revisions.

Everything here can be deleted and rebuilt from `source_revisions`, which is the evidence.
"""

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import delete, exists, func, select, true, update
from sqlalchemy.orm import Session

from scrinium.application.retrieval.chunking import chunk_text, embedding_input
from scrinium.application.retrieval.structure import ensure_symbols
from scrinium.application.workspaces import search_includes_tests
from scrinium.domain.enums import ItemLifecycle
from scrinium.domain.paths import is_test_path
from scrinium.infrastructure.embeddings.base import EmbeddingProvider
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    ContentChunk,
    SourceConnection,
    SourceItem,
    SourceRevision,
)
from scrinium.infrastructure.vector_index.lance import VectorIndex, VectorRow

logger = logging.getLogger("scrinium.retrieval")

EMBED_BATCH: Final = 32
_STATUS_TTL_SECONDS: Final = 10.0


def chunk_revision(session: Session, revision: SourceRevision) -> int:
    path = revision.metadata_.get("path", "")
    chunks = chunk_text(path, revision.text_content or "")
    is_test = is_test_path(path)
    for chunk in chunks:
        session.add(
            ContentChunk(
                workspace_id=revision.workspace_id,
                source_revision_id=revision.id,
                chunk_index=chunk.index,
                text_content=chunk.text,
                locator=chunk.locator(),
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
                is_test=is_test,
            )
        )
    return len(chunks)


def ensure_chunks(session: Session, connection_id: uuid.UUID) -> int:
    """Chunks the current revisions of a source that have no chunks yet."""
    has_chunks = exists().where(ContentChunk.source_revision_id == SourceRevision.id)
    revisions = session.scalars(
        select(SourceRevision)
        .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
        .where(SourceItem.source_connection_id == connection_id, ~has_chunks)
    )
    return sum(chunk_revision(session, revision) for revision in revisions)


def delete_chunks_of_source(session: Session, connection_id: uuid.UUID) -> None:
    revisions = (
        select(SourceRevision.id)
        .join(SourceItem, SourceItem.id == SourceRevision.source_item_id)
        .where(SourceItem.source_connection_id == connection_id)
    )
    session.execute(delete(ContentChunk).where(ContentChunk.source_revision_id.in_(revisions)))


@dataclass(frozen=True)
class IndexStatus:
    chunks: int
    embedded: int
    provider: ModelStatus
    model_id: str


class Indexer:
    def __init__(
        self, database: Database, provider: EmbeddingProvider, vector_index: VectorIndex
    ) -> None:
        self.database = database
        self.provider = provider
        self.vector_index = vector_index
        self._status_cache: tuple[float, ModelStatus] | None = None
        self._lock = threading.Lock()

    def provider_status(self) -> ModelStatus:
        """With a short cache: the status screen must not wait for the model on every load."""
        now = time.monotonic()
        cached = self._status_cache
        if cached is not None and now - cached[0] < _STATUS_TTL_SECONDS:
            return cached[1]
        status = self.provider.status()
        self._status_cache = (now, status)
        return status

    def status(self, session: Session, project_id: uuid.UUID) -> IndexStatus:
        base = (
            select(func.count(ContentChunk.id))
            .select_from(ContentChunk)
            .join(SourceItem, SourceItem.current_revision_id == ContentChunk.source_revision_id)
            .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
            .where(
                SourceConnection.project_id == project_id,
                SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            )
        )
        if not search_includes_tests(session):
            base = base.where(ContentChunk.is_test.is_(False))
        total = session.scalar(base) or 0
        embedded = (
            session.scalar(base.where(ContentChunk.embedding_model == self.provider.model_id)) or 0
        )
        return IndexStatus(total, embedded, self.provider_status(), self.provider.model_id)

    def _pending_batch(self) -> list[tuple[ContentChunk, str, uuid.UUID, uuid.UUID]]:
        model = self.provider.model_id
        with self.database.session() as session:
            tests = search_includes_tests(session)
            rows = session.execute(
                select(ContentChunk, SourceRevision, SourceConnection)
                .join(SourceRevision, SourceRevision.id == ContentChunk.source_revision_id)
                .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
                .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
                .where(
                    SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
                    SourceConnection.project_id.is_not(None),
                    # Tests get no vector unless included: ~10 % of the code, all noise.
                    true() if tests else ContentChunk.is_test.is_(False),
                    (ContentChunk.embedding_model.is_(None))
                    | (ContentChunk.embedding_model != model),
                )
                .order_by(ContentChunk.id)
                .limit(EMBED_BATCH)
            ).all()
            return [
                (
                    chunk,
                    embedding_input(
                        revision.metadata_.get("path", ""),
                        chunk.text_content,
                        chunk.locator.get("heading"),
                    ),
                    connection.project_id,
                    connection.id,
                )
                for chunk, revision, connection in rows
                if connection.project_id is not None
            ]

    def embed_pending(self) -> int:
        """Computes the missing vectors. Stops without error if the model is unavailable."""
        with self._lock:
            done = 0
            if self.provider.status() is not ModelStatus.READY:
                return 0
            while batch := self._pending_batch():
                try:
                    vectors = self.provider.embed_documents([text for _, text, _, _ in batch])
                except ModelError:
                    logger.warning("embedding_unavailable done=%d", done)
                    return done
                self.vector_index.upsert(
                    self.provider.model_id,
                    [
                        VectorRow(chunk.id, project_id, source_id, chunk.source_revision_id, vector)
                        for (chunk, _, project_id, source_id), vector in zip(
                            batch, vectors, strict=True
                        )
                    ],
                )
                with self.database.session() as session:
                    session.execute(
                        update(ContentChunk)
                        .where(ContentChunk.id.in_([chunk.id for chunk, _, _, _ in batch]))
                        .values(
                            embedding_model=self.provider.model_id,
                            embedding_dimension=len(vectors[0]),
                        )
                    )
                done += len(batch)
            return done

    def rebuild(self) -> None:
        """Rebuilds chunks, FTS5 and vectors from the current revisions."""
        with self._lock:
            self.vector_index.reset()
            with self.database.session() as session:
                session.execute(delete(ContentChunk))
                connections = session.scalars(select(SourceConnection.id)).all()
                for connection_id in connections:
                    ensure_chunks(session, connection_id)
                    ensure_symbols(session, connection_id)
        self.embed_pending()

    def delete_source(self, source_id: uuid.UUID) -> None:
        self.vector_index.delete_source(source_id)
