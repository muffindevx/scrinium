"""Local source routes: preview, index, read, cite and delete."""

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from scrinium.application.ingestion import clone, sources
from scrinium.application.ingestion.plan import Plan, SourceError
from scrinium.application.ingestion.policy import (
    MAX_USER_PATTERNS,
    ExclusionReason,
    SourceKind,
    validate_user_patterns,
)
from scrinium.application.privacy import erasure
from scrinium.application.workspaces import local_workspace
from scrinium.core.config import RuntimeConfig
from scrinium.domain.enums import ConnectionStatus, SyncStatus, SyncTrigger
from scrinium.infrastructure.local_files.git_reader import GitError, GitRepository
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import SourceConnection, SyncRun

# How many files a preview lists. The totals count all of them.
PREVIEW_LIST_LIMIT: Final = 500
FILES_LIST_LIMIT: Final = 5000


class SourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: SourceKind
    path: str = Field(default="", max_length=4096)
    # Phase 9.6: an https URL to clone instead of a folder on this machine (Git only).
    url: str | None = Field(default=None, max_length=2000)
    exclude_patterns: list[str] = Field(default_factory=list, max_length=MAX_USER_PATTERNS)
    # Generated code (`@generated`, `DO NOT EDIT`, `*.pb.go`…) is left out unless asked for.
    include_generated: bool = False

    @field_validator("exclude_patterns")
    @classmethod
    def _patterns(cls, patterns: list[str]) -> list[str]:
        return validate_user_patterns(patterns)


class PreviewFile(BaseModel):
    path: str
    size: int


class PreviewExclusion(BaseModel):
    path: str
    reason: ExclusionReason
    rule: str | None
    line: int | None


class PreviewResponse(BaseModel):
    kind: SourceKind
    root: str
    commit: str | None
    included_count: int
    included_bytes: int
    excluded_count: int
    excluded_by_reason: dict[str, int]
    included: list[PreviewFile]
    excluded: list[PreviewExclusion]


class SyncRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    trigger: SyncTrigger
    status: SyncStatus
    cursor_before: str | None
    cursor_after: str | None
    items_seen: int
    items_created: int
    items_updated: int
    items_deleted: int
    items_excluded: int
    error_summary: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class SourceResponse(BaseModel):
    id: uuid.UUID
    kind: SourceKind
    display_name: str
    root: str
    exclude_patterns: list[str]
    include_generated: bool
    status: ConnectionStatus
    active_files: int
    last_successful_sync_at: datetime | None
    latest_sync: SyncRunResponse | None
    # Git sources: the branch, the commit indexed, and whether the repository moved on since.
    remote_url: str | None = None
    branch: str | None = None
    indexed_commit: str | None = None
    head_commit: str | None = None
    head_moved: bool = False


class SourceListResponse(BaseModel):
    sources: list[SourceResponse]


class CreatedSourceResponse(BaseModel):
    source: SourceResponse
    sync_run: SyncRunResponse


class SourceFile(BaseModel):
    path: str
    item_id: uuid.UUID
    revision_id: uuid.UUID
    line_count: int


class SourceFilesResponse(BaseModel):
    files: list[SourceFile]
    truncated: bool


class LinesResponse(BaseModel):
    revision_id: uuid.UUID
    path: str
    commit: str | None
    start: int
    end: int
    line_count: int
    lines: list[str]


def _preview_response(plan: Plan) -> PreviewResponse:
    return PreviewResponse(
        kind=plan.kind,
        root=str(plan.root),
        commit=plan.commit,
        included_count=len(plan.included),
        included_bytes=plan.included_bytes,
        excluded_count=len(plan.excluded),
        excluded_by_reason=plan.excluded_by_reason(),
        included=[PreviewFile(path=f.path, size=f.size) for f in plan.included][
            :PREVIEW_LIST_LIMIT
        ],
        excluded=[
            PreviewExclusion(
                path=e.path, reason=e.exclusion.reason, rule=e.exclusion.rule, line=e.exclusion.line
            )
            for e in plan.excluded[:PREVIEW_LIST_LIMIT]
        ],
    )


def _source_response(session: DbSession, connection: SourceConnection) -> SourceResponse:
    settings = sources.settings_of(connection)
    run = sources.latest_run(session, connection.id)
    return SourceResponse(
        id=connection.id,
        kind=settings.kind,
        display_name=connection.display_name,
        root=str(settings.root),
        exclude_patterns=settings.exclude_patterns,
        include_generated=settings.include_generated,
        status=connection.status,
        active_files=sources.count_active_items(session, connection.id),
        last_successful_sync_at=connection.last_successful_sync_at,
        latest_sync=SyncRunResponse.model_validate(run) if run else None,
        **_git_position(session, connection, settings.kind, settings.root),
    )


def _git_position(
    session: DbSession, connection: SourceConnection, kind: SourceKind, root: Path
) -> dict[str, Any]:
    """Which branch and commit were indexed, and whether HEAD has moved since (phase 9.6)."""
    if kind is not SourceKind.GIT_REPOSITORY:
        return {}
    indexed = session.scalars(
        select(SyncRun.cursor_after)
        .where(
            SyncRun.source_connection_id == connection.id,
            SyncRun.status == SyncStatus.SUCCEEDED,
        )
        .order_by(SyncRun.created_at.desc())
        .limit(1)
    ).first()
    head_file = root / ".git" / "HEAD"
    branch = None
    try:
        reference = head_file.read_text(encoding="utf-8").strip()
        if reference.startswith("ref: refs/heads/"):
            branch = reference.removeprefix("ref: refs/heads/")
        head = GitRepository(root).head()
    except (OSError, GitError):
        head = None
    return {
        "remote_url": (connection.configuration or {}).get("remote_url"),
        "branch": branch,
        "indexed_commit": indexed,
        "head_commit": head,
        "head_moved": bool(indexed and head and indexed != head),
    }


@contextmanager
def _http_errors() -> Iterator[None]:
    try:
        yield
    except sources.SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
    except sources.SyncInProgressError:
        raise HTTPException(status.HTTP_409_CONFLICT, "sync_in_progress") from None
    except (SourceError, GitError) as error:
        # Code only: never paths or git output.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(error)) from None


def _request_id(request: Request) -> str | None:
    request_id: str | None = getattr(request.state, "request_id", None)
    return request_id


def build_sources_router(
    config: RuntimeConfig, database: Database, runner: sources.IngestionRunner
) -> APIRouter:
    router = APIRouter()

    def db_session() -> Iterator[DbSession]:
        with database.session() as session:
            yield session

    Db = Annotated[DbSession, Depends(db_session)]

    @router.post("/api/v1/projects/{project_id}/sources/preview")
    def preview_source(project_id: uuid.UUID, body: SourceRequest, db: Db) -> PreviewResponse:
        with _http_errors():
            sources.project_of(db, project_id)
            plan = sources.preview(
                body.kind, body.path, body.exclude_patterns, config.data_dir, body.include_generated
            )
        return _preview_response(plan)

    @router.post("/api/v1/projects/{project_id}/sources", status_code=status.HTTP_201_CREATED)
    def create_source(
        project_id: uuid.UUID, body: SourceRequest, request: Request
    ) -> CreatedSourceResponse:
        path, remote_url = body.path, None
        if body.url:
            if body.kind is not SourceKind.GIT_REPOSITORY:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "url_needs_git")
            with database.session() as session, _http_errors():
                sources.project_of(session, project_id)  # 404 before anything is downloaded
            try:
                remote_url = clone.check_url(body.url)
                path = str(clone.clone(remote_url, config.data_dir))
            except clone.CloneError as error:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, error.code) from None
        elif not path:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "path_not_absolute")
        # Own transaction: the sync starts once the source is stored.
        with _http_errors(), database.session() as session:
            connection, run = sources.create_source(
                session,
                sources.project_of(session, project_id),
                body.kind,
                path,
                body.exclude_patterns,
                config.data_dir,
                _request_id(request),
                body.include_generated,
                remote_url,
            )
            run_id, connection_id = run.id, connection.id
        runner.submit(run_id)
        with database.session() as session:
            connection = session.get_one(SourceConnection, connection_id)
            return CreatedSourceResponse(
                source=_source_response(session, connection),
                sync_run=SyncRunResponse.model_validate(session.get_one(SyncRun, run_id)),
            )

    @router.get("/api/v1/projects/{project_id}/sources")
    def list_sources(project_id: uuid.UUID, db: Db) -> SourceListResponse:
        with _http_errors():
            project = sources.project_of(db, project_id)
        return SourceListResponse(
            sources=[_source_response(db, c) for c in sources.list_sources(db, project)]
        )

    @router.post("/api/v1/sources/{source_id}/sync", status_code=status.HTTP_202_ACCEPTED)
    def sync_source(source_id: uuid.UUID, request: Request) -> SyncRunResponse:
        with _http_errors(), database.session() as session:
            run_id = sources.request_sync(session, source_id, _request_id(request)).id
        runner.submit(run_id)
        with database.session() as session:
            return SyncRunResponse.model_validate(session.get_one(SyncRun, run_id))

    @router.delete("/api/v1/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_source(
        source_id: uuid.UUID,
        request: Request,
        delete_citing: bool = False,
        delete_reviewed: bool = False,
    ) -> Response:
        # Deletes rows and vectors and purges the disk. With delete_citing, also the questions and
        # analyses that cite it; with delete_reviewed, also the reviewed rules only it backs
        # (see GET .../deletion-impact).
        with _http_errors():
            erasure.delete_source(
                database, runner.indexer, source_id, _request_id(request), delete_citing,
                delete_reviewed,
            )  # fmt: skip
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/api/v1/sources/{source_id}/deletion-impact")
    def deletion_impact(source_id: uuid.UUID, db: Db) -> dict[str, Any]:
        with _http_errors():
            return erasure.source_deletion_impact(db, source_id)

    @router.get("/api/v1/sync-runs/{run_id}")
    def read_sync_run(run_id: uuid.UUID, db: Db) -> SyncRunResponse:
        run = db.get(SyncRun, run_id)
        if run is None or run.workspace_id != local_workspace(db).id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        return SyncRunResponse.model_validate(run)

    @router.get("/api/v1/sources/{source_id}/files")
    def list_files(source_id: uuid.UUID, db: Db) -> SourceFilesResponse:
        with _http_errors():
            rows = sources.current_revisions(db, source_id, FILES_LIST_LIMIT + 1)
        return SourceFilesResponse(
            files=[
                SourceFile(
                    path=item.provider_item_id,
                    item_id=item.id,
                    revision_id=revision.id,
                    line_count=revision.metadata_.get("line_count", 0),
                )
                for item, revision in rows[:FILES_LIST_LIMIT]
            ],
            truncated=len(rows) > FILES_LIST_LIMIT,
        )

    @router.get("/api/v1/source-revisions/{revision_id}/lines")
    def read_lines(
        revision_id: uuid.UUID,
        db: Db,
        start: Annotated[int, Query(ge=1)] = 1,
        end: Annotated[int, Query(ge=1)] = 50,
    ) -> LinesResponse:
        try:
            with _http_errors():
                cited = sources.read_lines(db, revision_id, start, end)
        except ValueError:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_line_range"
            ) from None
        metadata = cited.revision.metadata_
        return LinesResponse(
            revision_id=cited.revision.id,
            path=metadata["path"],
            commit=metadata.get("commit"),
            start=cited.start,
            end=cited.end,
            line_count=metadata.get("line_count", 0),
            lines=cited.lines,
        )

    return router
