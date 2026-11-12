"""Restoring an export into an empty workspace.

The file is untrusted input: it may be tampered with even if it came from an export. So
it is validated before writing anything (format, version, known tables and columns, sizes) and
everything is inserted in one transaction with foreign keys, CHECKs and triggers
active: any inconsistent row makes the whole restore fail, and nothing is left half done.

Rows are reassigned to this installation's local workspace. The derived indexes (chunks,
FTS5 and vectors) are not in the export: they are rebuilt afterwards from the revisions.
"""

import json
import uuid
import zlib
from datetime import datetime
from typing import Any, Final

from sqlalchemy import Table, Uuid, func, select, update
from sqlalchemy.exc import IntegrityError, StatementError
from sqlalchemy.orm import Session

from scrinium.application.ai.answering import mark_interrupted_questions
from scrinium.application.ingestion.sources import mark_interrupted_runs
from scrinium.application.privacy.export import (
    ENCRYPTED_FORMAT,
    FORMAT,
    FORMAT_VERSION,
    ExportError,
    decrypt,
)
from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import AuditResult
from scrinium.infrastructure.sqlite.models import (
    Base,
    LocalProfile,
    Project,
    SoftwareRelease,
    SourceConnection,
    SourceItem,
    UtcDateTime,
)

MAX_PLAIN_BYTES: Final = 2 * 1024 * 1024 * 1024
# Inserted without them and set afterwards: they point to rows that do not exist yet.
_DEFERRED: Final = {
    "source_items": "current_revision_id",
    "software_releases": "previous_release_id",
}
_SKIPPED: Final = frozenset(
    {
        "workspaces",
        "local_profile",
        "content_chunks",
        "code_symbols",
        "code_edges",
        "extraction_tasks",
    }
)


class RestoreError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _bounded_gunzip(data: bytes) -> bytes:
    """Decompresses without passing the limit: a small file can expand to gigabytes."""
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = inflater.decompress(data, MAX_PLAIN_BYTES + 1)
    if len(output) > MAX_PLAIN_BYTES or inflater.unconsumed_tail:
        raise RestoreError("backup_too_large")
    return output


def read_backup(data: bytes, passphrase: str | None) -> dict[str, Any]:
    """Opens the file (encrypted or not) and checks it is a Scrinium export."""
    try:
        envelope = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RestoreError("invalid_backup") from None
    if not isinstance(envelope, dict):
        raise RestoreError("invalid_backup")
    if envelope.get("header", {}).get("format") == ENCRYPTED_FORMAT:
        if not passphrase:
            raise RestoreError("passphrase_required")
        try:
            document = decrypt(data, passphrase, decompress=_bounded_gunzip)
        except ExportError as error:
            raise RestoreError(error.code) from None
    else:
        document = envelope
    if document.get("format") != FORMAT:
        raise RestoreError("invalid_backup")
    if document.get("format_version") != FORMAT_VERSION:
        raise RestoreError("unsupported_backup_version")
    if not isinstance(document.get("tables"), dict):
        raise RestoreError("invalid_backup")
    return document


def _convert(table: Table, row: dict[str, Any], workspace_id: uuid.UUID) -> dict[str, Any]:
    columns = table.columns
    unknown = set(row) - set(columns.keys())
    if unknown:
        raise RestoreError("invalid_backup")
    values: dict[str, Any] = {}
    for name, value in row.items():
        column_type = columns[name].type
        if value is None:
            values[name] = None
        elif isinstance(column_type, Uuid):
            values[name] = uuid.UUID(str(value))
        elif isinstance(column_type, UtcDateTime):
            values[name] = datetime.fromisoformat(str(value))
        else:
            values[name] = value
    if "workspace_id" in columns:
        values["workspace_id"] = workspace_id
    return values


def is_empty(session: Session) -> bool:
    return all(
        session.scalar(select(func.count()).select_from(model)) == 0
        for model in (Project, SourceConnection)
    )


def restore(session: Session, document: dict[str, Any], request_id: str | None) -> dict[str, int]:
    """Inserts all into the local workspace; on failure raises RestoreError, writing nothing."""
    if not is_empty(session):
        raise RestoreError("workspace_not_empty")
    tables = document["tables"]
    known = {table.name: table for table in Base.metadata.sorted_tables}
    if set(tables) - set(known):
        raise RestoreError("invalid_backup")
    workspace = local_workspace(session)
    counts: dict[str, int] = {}
    deferred: dict[str, list[tuple[Any, Any]]] = {name: [] for name in _DEFERRED}
    try:
        for table in Base.metadata.sorted_tables:
            rows = tables.get(table.name, [])
            if table.name in _SKIPPED or not rows:
                continue
            if not isinstance(rows, list):
                raise RestoreError("invalid_backup")
            converted = []
            for row in rows:
                if not isinstance(row, dict):
                    raise RestoreError("invalid_backup")
                values = _convert(table, row, workspace.id)
                link = _DEFERRED.get(table.name)
                if link and values.get(link) is not None:
                    deferred[table.name].append((values["id"], values[link]))
                    values[link] = None
                converted.append(values)
            session.execute(table.insert(), converted)
            counts[table.name] = len(converted)
        for table_name, links in deferred.items():
            model = SourceItem if table_name == "source_items" else SoftwareRelease
            column = _DEFERRED[table_name]
            for row_id, target in links:
                session.execute(update(model).where(model.id == row_id).values({column: target}))
        session.flush()
    except (IntegrityError, StatementError, ValueError, TypeError) as error:
        if isinstance(error, RestoreError):
            raise
        raise RestoreError("invalid_backup") from None
    profile = tables.get("local_profile") or []
    if profile and isinstance(profile[0], dict):
        name = profile[0].get("display_name")
        local = session.get(LocalProfile, 1)
        if local is not None and (name is None or isinstance(name, str)):
            local.display_name = name[:200] if name else None
    # What was running at export time cannot continue: it is marked interrupted.
    mark_interrupted_runs(session)
    mark_interrupted_questions(session)
    record_audit(
        session, workspace.id, "workspace.restore", "workspace", str(workspace.id),
        AuditResult.SUCCESS, request_id, {"rows": sum(counts.values())},
    )  # fmt: skip
    return counts
