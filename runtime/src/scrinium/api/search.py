"""Search and index status routes."""

import uuid
from collections.abc import Iterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import Session as DbSession

from scrinium.application import workspaces
from scrinium.application.ingestion import sources
from scrinium.application.retrieval import palette as palette_reads
from scrinium.application.retrieval import search as retrieval
from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import AuditResult
from scrinium.infrastructure.sqlite.database import Database

SemanticStatus = Literal["ready", "model_missing", "unavailable"]


class SearchHitResponse(BaseModel):
    chunk_id: uuid.UUID
    source_id: uuid.UUID
    revision_id: uuid.UUID
    path: str
    commit: str | None
    start_line: int
    end_line: int
    heading: str | None
    text: str
    score: float
    matched_by: list[Literal["symbol", "lexical", "semantic"]]


class SearchResponse(BaseModel):
    semantic: SemanticStatus
    results: list[SearchHitResponse]


class SearchSettings(BaseModel):
    include_tests: bool


class IndexStatusResponse(BaseModel):
    chunks: int
    embedded: int
    provider: SemanticStatus
    model: str


def build_search_router(database: Database, runner: sources.IngestionRunner) -> APIRouter:
    router = APIRouter()
    indexer = runner.indexer

    def db_session() -> Iterator[DbSession]:
        with database.session() as session:
            yield session

    Db = Annotated[DbSession, Depends(db_session)]

    def project(db: DbSession, project_id: uuid.UUID) -> uuid.UUID:
        try:
            return sources.project_of(db, project_id).id
        except sources.SourceNotFoundError:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None

    @router.get("/api/v1/projects/{project_id}/search")
    def search(
        project_id: uuid.UUID,
        db: Db,
        q: Annotated[str, Query(min_length=1, max_length=retrieval.MAX_QUERY_LENGTH)],
        limit: Annotated[int, Query(ge=1, le=retrieval.MAX_RESULTS)] = 20,
    ) -> SearchResponse:
        result = retrieval.search(db, indexer, project(db, project_id), q, limit)
        return SearchResponse(
            semantic=result.semantic,  # type: ignore[arg-type]
            results=[
                SearchHitResponse(
                    chunk_id=hit.chunk.id,
                    source_id=hit.source_id,
                    revision_id=hit.revision.id,
                    path=hit.revision.metadata_.get("path", ""),
                    commit=hit.revision.metadata_.get("commit"),
                    start_line=hit.chunk.locator["start_line"],
                    end_line=hit.chunk.locator["end_line"],
                    heading=hit.chunk.locator.get("heading"),
                    text=hit.chunk.text_content,
                    score=hit.score,
                    matched_by=hit.matched_by,  # type: ignore[arg-type]
                )
                for hit in result.hits
            ],
        )

    @router.get("/api/v1/projects/{project_id}/palette")
    def palette(
        project_id: uuid.UUID,
        db: Db,
        q: Annotated[str, Query(max_length=palette_reads.MAX_QUERY)] = "",
    ) -> dict[str, Any]:
        """⌘K: rules, functions, files and tickets by name (phase 6.2)."""
        return palette_reads.palette(db, project(db, project_id), q)

    @router.get("/api/v1/projects/{project_id}/index-status")
    def index_status(project_id: uuid.UUID, db: Db) -> IndexStatusResponse:
        current = indexer.status(db, project(db, project_id))
        return IndexStatusResponse(
            chunks=current.chunks,
            embedded=current.embedded,
            provider=current.provider.value,
            model=current.model_id,
        )

    @router.get("/api/v1/search/settings")
    def read_search_settings(db: Db) -> SearchSettings:
        return SearchSettings(include_tests=workspaces.search_includes_tests(db))

    @router.put("/api/v1/search/settings")
    def update_search_settings(body: SearchSettings, request: Request) -> SearchSettings:
        request_id: str | None = getattr(request.state, "request_id", None)
        # Its own transaction, committed before the vectors start: they read this setting.
        with database.session() as session:
            workspace = local_workspace(session)
            workspaces.set_search_includes_tests(session, body.include_tests)
            record_audit(
                session, workspace.id, "search.settings", "workspace", str(workspace.id),
                AuditResult.SUCCESS, request_id, {"include_tests": body.include_tests},
            )  # fmt: skip
        if body.include_tests:
            # The tests' vectors, in the background: search uses words until they are ready.
            runner.submit_embeddings()
        return body

    @router.post("/api/v1/index/rebuild", status_code=status.HTTP_202_ACCEPTED)
    def rebuild(request: Request, db: Db) -> None:
        workspace = local_workspace(db)
        request_id: str | None = getattr(request.state, "request_id", None)
        record_audit(
            db, workspace.id, "index.rebuild", "workspace", str(workspace.id),
            AuditResult.SUCCESS, request_id,
        )  # fmt: skip
        runner.submit_rebuild()

    return router
