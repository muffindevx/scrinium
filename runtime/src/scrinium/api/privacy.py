"""Privacy routes: see what is stored, export, and delete a project or the whole workspace.

Deleting requires typing a confirmation (the project key, or BORRAR TODO): one click by
mistake must not be enough to lose the data.
"""

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.concurrency import run_in_threadpool

from scrinium.application.environment import LocalEnvironment
from scrinium.application.ingestion import sources
from scrinium.application.ingestion.sources import IngestionRunner
from scrinium.application.privacy import erasure, export, restore
from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.core.config import RuntimeConfig
from scrinium.domain.enums import AuditResult
from scrinium.infrastructure.sqlite.database import Database

PASSPHRASE_HEADER = "x-backup-passphrase"  # noqa: S105  header name, not a secret


class ConfirmationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(max_length=200)


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passphrase: str | None = Field(default=None, max_length=1000)
    unencrypted: bool = False

    @model_validator(mode="after")
    def _one_choice(self) -> "ExportBody":
        # Encrypting is the default; unencrypted must be asked for explicitly.
        if (self.passphrase is None) != self.unencrypted:
            raise ValueError("passphrase_or_unencrypted")
        return self


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _errors(error: Exception) -> HTTPException:
    if isinstance(error, sources.SourceNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    if isinstance(error, erasure.ConfirmationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "confirmation_mismatch")
    if isinstance(error, erasure.WorkInProgressError | sources.SyncInProgressError):
        return HTTPException(status.HTTP_409_CONFLICT, "work_in_progress")
    raise error


def build_privacy_router(
    config: RuntimeConfig,
    database: Database,
    runner: IngestionRunner,
    environment: LocalEnvironment,
) -> APIRouter:
    indexer = runner.indexer
    router = APIRouter()
    handled = (
        sources.SourceNotFoundError,
        sources.SyncInProgressError,
        erasure.ConfirmationError,
        erasure.WorkInProgressError,
    )

    @router.get("/api/v1/privacy/stored-data")
    def stored_data() -> dict[str, Any]:
        with database.session() as session:
            data = erasure.stored_data(session, config.data_dir)
            return {"counts": data.counts, "bytes_on_disk": data.bytes_on_disk}

    @router.delete("/api/v1/projects/{project_id}")
    def delete_project(
        project_id: uuid.UUID, body: ConfirmationBody, request: Request
    ) -> dict[str, int]:
        try:
            return erasure.delete_project(
                database, indexer, project_id, body.confirmation, _request_id(request)
            )
        except handled as error:
            raise _errors(error) from None

    @router.delete("/api/v1/privacy/workspace-data")
    def wipe(body: ConfirmationBody, request: Request) -> dict[str, int]:
        try:
            return erasure.wipe_workspace(
                database, indexer, environment.secret_store, body.confirmation,
                _request_id(request),
            )  # fmt: skip
        except handled as error:
            raise _errors(error) from None

    @router.post("/api/v1/privacy/export")
    def export_data(body: ExportBody, request: Request) -> Response:
        """Streamed table by table (R1): memory stays flat with any amount of data."""
        try:
            export.check_passphrase(body.passphrase)
        except export.ExportError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code) from None
        encrypted = body.passphrase is not None
        with database.session() as session:
            # No password or content: only that an export happened and whether it was encrypted.
            record_audit(
                session, local_workspace(session).id, "workspace.export", "workspace", None,
                AuditResult.SUCCESS, _request_id(request), {"encrypted": encrypted},
            )  # fmt: skip

        def content() -> Iterator[bytes]:
            # Its own read-only transaction, open while the response is written.
            with database.scratch() as session:
                yield from export.stream_export(session, body.passphrase)

        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        name = f"scrinium-{stamp}.{'scrinium' if encrypted else 'json'}"
        return StreamingResponse(
            content(),
            media_type="application/octet-stream" if encrypted else "application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{name}"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/api/v1/privacy/restore")
    async def restore_backup(request: Request) -> dict[str, Any]:
        """The file goes as the body (up to 512 MiB, see BodyLimitMiddleware) and the password in
        a header: neither is logged."""
        data = await request.body()
        passphrase = request.headers.get(PASSPHRASE_HEADER)
        request_id = _request_id(request)

        def run() -> dict[str, int]:
            document = restore.read_backup(data, passphrase)
            with database.session() as session:
                return restore.restore(session, document, request_id)

        try:
            counts = await run_in_threadpool(run)
        except restore.RestoreError as error:
            code = status.HTTP_409_CONFLICT if error.code == "workspace_not_empty" else (
                status.HTTP_422_UNPROCESSABLE_CONTENT
            )  # fmt: skip
            raise HTTPException(code, error.code) from None
        # Chunks, FTS5 and vectors are not in the export: they are rebuilt separately.
        runner.submit_rebuild()
        return {"restored": counts}

    return router
