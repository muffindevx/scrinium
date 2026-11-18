"""Deletion of sources, projects and the workspace, and a summary of what is stored.

Deleting has two phases. First, in a transaction, the rows are deleted. Then, once committed,
the disk is purged: vectors (in SQLite since phase 9.1),
FTS5 segments and the SQLite WAL. Without the second phase, deleted data would stay in the files.

What it cannot guarantee: copies the file system or the SSD keep in freed blocks,
backups made earlier, and exports already downloaded.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from scrinium.application.ingestion import sources
from scrinium.application.knowledge.deletion import (
    citing_runs,
    entity_fate,
    forget_project_knowledge,
)
from scrinium.application.privacy import pending_purge
from scrinium.application.retrieval.indexing import Indexer
from scrinium.application.trackers.connections import remove_connection
from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import AnalysisStatus, AnalysisType, AuditResult, SyncStatus
from scrinium.infrastructure.secret_store import SecretStore
from scrinium.infrastructure.sqlite.database import DATABASE_FILENAME, Database
from scrinium.infrastructure.sqlite.models import (
    AiCacheEntry,
    AiConsent,
    AiRequest,
    AnalysisRun,
    AuditEvent,
    Base,
    BusinessRule,
    ContentChunk,
    EvidenceRecord,
    Finding,
    KnowledgeEntity,
    LocalProfile,
    ModelProvider,
    Project,
    SoftwareRelease,
    SourceConnection,
    SourceItem,
    SourceRevision,
    SyncRun,
    TicketConnection,
    Workspace,
)
from scrinium.infrastructure.vector_index.base import LEGACY_VECTOR_DIRNAME

WIPE_CONFIRMATION: Final = "BORRAR TODO"
_RUNNING_SYNC: Final = (SyncStatus.QUEUED, SyncStatus.RUNNING)
_RUNNING_ANALYSIS: Final = (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING)
# Never deleted: the local workspace is unique and the profile is a fixed row.
_KEPT_TABLES: Final = frozenset({"workspaces", "local_profile"})

logger = logging.getLogger("scrinium.privacy")


class WorkInProgressError(RuntimeError):
    """A sync or an analysis is running: deleting now would leave data half done."""


class ConfirmationError(ValueError):
    pass


def _busy(session: Session, project_id: uuid.UUID | None = None) -> bool:
    syncs = select(SyncRun.id).where(SyncRun.status.in_(_RUNNING_SYNC))
    analyses = select(AnalysisRun.id).where(AnalysisRun.status.in_(_RUNNING_ANALYSIS))
    if project_id is not None:
        syncs = syncs.join(
            SourceConnection, SourceConnection.id == SyncRun.source_connection_id
        ).where(SourceConnection.project_id == project_id)
        analyses = analyses.where(AnalysisRun.project_id == project_id)
    return session.scalar(syncs.limit(1)) is not None or (
        session.scalar(analyses.limit(1)) is not None
    )


def source_deletion_impact(session: Session, source_id: uuid.UUID) -> dict[str, Any]:
    """What would be deleted with the source, to decide beforehand."""
    connection = session.get(SourceConnection, source_id)
    if connection is None or connection.workspace_id != local_workspace(session).id:
        raise sources.SourceNotFoundError
    revisions = sources.source_revision_ids(connection.id)
    fate = entity_fate(session, revisions)
    citing = (
        citing_runs(session, connection.project_id, sources.source_references(session, connection))
        if connection.project_id is not None
        else []
    )
    questions = session.scalar(
        select(func.count()).where(
            AnalysisRun.id.in_(citing), AnalysisRun.analysis_type == AnalysisType.QUESTION
        )
    )
    return {
        "files": session.scalar(
            select(func.count()).where(SourceItem.source_connection_id == connection.id)
        ),
        "revisions": session.scalar(select(func.count()).where(SourceRevision.id.in_(revisions))),
        "releases": session.scalar(
            select(func.count()).where(SoftwareRelease.source_connection_id == connection.id)
        ),
        "evidence": session.scalar(
            select(func.count()).where(EvidenceRecord.source_revision_id.in_(revisions))
        ),
        "entities_to_delete": len(fate.delete),
        "entities_to_review": len(fate.review),
        # Of those, rules a person wrote or reviewed that only this source backs: they can go
        # too, if the person says so (`delete_reviewed`).
        "entities_reviewed_here": len(fate.reviewed_here),
        "citing_questions": questions or 0,
        "citing_analyses": len(citing) - (questions or 0),
    }


def _purge_step(database: Database, indexer: Indexer, kind: str, target: uuid.UUID | None) -> None:
    """The second phase of a deletion: vectors of that source, that project or all, then the
    database files."""
    if kind == "source" and target is not None:
        indexer.delete_source(target)
    elif kind == "project" and target is not None:
        indexer.vector_index.delete_project(target)
    elif kind == "all":
        indexer.vector_index.reset()
    database.purge()


def _after_commit(
    database: Database, indexer: Indexer, kind: str, target: uuid.UUID | None
) -> None:
    """The disk purge after a committed deletion; a failure is remembered, never raised: the
    rows are gone already, and the person sees the deletion done (R3)."""
    try:
        _purge_step(database, indexer, kind, target)
    except Exception:
        logger.warning("purge_failed kind=%s", kind)
        pending_purge.remember(database.data_dir, kind, target)


def vacuum(database: Database) -> None:
    """Compacts the database file after a large deletion (2.2): the freed pages, already zeroed
    by `secure_delete`, leave the file. A failure only leaves the file larger."""
    try:
        with database.engine.connect() as connection:
            connection.exec_driver_sql("VACUUM")
    except Exception:
        logger.info("vacuum_skipped")


def retry_pending_purges(database: Database, indexer: Indexer) -> int:
    """At startup: the purges a failed deletion left to do (R3). Returns how many remain."""
    if database.data_dir is None:
        return 0

    def step(kind: str) -> Callable[[Any], None]:
        def run(target: Any) -> None:
            _purge_step(database, indexer, kind, target)

        return run

    steps = {kind: step(kind) for kind in ("source", "project", "all")}
    return pending_purge.run_pending(database.data_dir, steps)


def purge(database: Database, indexer: Indexer) -> None:
    """Second phase: removes from disk what was already deleted in the database."""
    indexer.vector_index.purge()
    database.purge()


def delete_source(
    database: Database,
    indexer: Indexer,
    source_id: uuid.UUID,
    request_id: str | None,
    delete_citing: bool,
    delete_reviewed: bool = False,
) -> dict[str, int]:
    with database.session() as session:
        summary = sources.delete_source(
            session, source_id, request_id, delete_citing, delete_reviewed
        )
    # After committing: if it fails, it is recorded and done again at the next start (R3).
    _after_commit(database, indexer, "source", source_id)
    return summary


def delete_project(
    database: Database,
    indexer: Indexer,
    project_id: uuid.UUID,
    confirmation: str,
    request_id: str | None,
    secret_store: SecretStore,
) -> dict[str, int]:
    with database.session() as session:
        project = sources.project_of(session, project_id)
        if confirmation != project.project_key:
            raise ConfirmationError
        if _busy(session, project.id):
            raise WorkInProgressError
        summary = forget_project_knowledge(session, project.id)
        summary["sources_deleted"] = 0
        for connection_id in list(
            session.scalars(
                select(SourceConnection.id).where(SourceConnection.project_id == project.id)
            )
        ):
            sources.delete_source(session, connection_id, request_id)
            summary["sources_deleted"] += 1
        # Its ticket connections, their tickets, links and tokens (ADR-014).
        summary["ticket_connections_deleted"] = 0
        for ticket_connection in list(
            session.scalars(
                select(TicketConnection).where(TicketConnection.project_id == project.id)
            )
        ):
            remove_connection(session, secret_store, ticket_connection, request_id)
            summary["ticket_connections_deleted"] += 1
        # The project's record of remote requests and its consents go with it (ADR-010); the
        # provider stays.
        summary["ai_requests_deleted"] = session.execute(
            delete(AiRequest).where(AiRequest.project_id == project.id)
        ).rowcount  # type: ignore[attr-defined]
        session.execute(delete(AiCacheEntry).where(AiCacheEntry.project_id == project.id))
        summary["ai_consents_deleted"] = session.execute(
            delete(AiConsent).where(AiConsent.project_id == project.id)
        ).rowcount  # type: ignore[attr-defined]
        # Without relationship() the session does not order deletes: sources before the project.
        session.flush()
        session.delete(project)
        record_audit(
            session, project.workspace_id, "project.delete", "project", str(project.id),
            AuditResult.SUCCESS, request_id, dict(summary),
        )  # fmt: skip
    _after_commit(database, indexer, "project", project_id)
    vacuum(database)
    return summary


def wipe_workspace(
    database: Database,
    indexer: Indexer,
    secret_store: SecretStore,
    confirmation: str,
    request_id: str | None,
) -> dict[str, int]:
    """Deletes all the workspace's data, the audit log included, and leaves a single event."""
    if confirmation != WIPE_CONFIRMATION:
        raise ConfirmationError
    with database.session() as session:
        if _busy(session):
            raise WorkInProgressError
        workspace = local_workspace(session)
        secrets = [ref for ref in session.scalars(select(SourceConnection.secret_ref)) if ref]
        # Remote providers' API keys too (ADR-010), and trackers' tokens (ADR-014).
        secrets += [ref for ref in session.scalars(select(ModelProvider.secret_ref)) if ref]
        secrets += [ref for ref in session.scalars(select(TicketConnection.secret_ref)) if ref]
        counts: dict[str, int] = {}
        # From children to parents: the reverse order of the dependencies between tables.
        session.execute(update(SourceItem).values(current_revision_id=None))
        session.execute(update(SoftwareRelease).values(previous_release_id=None))
        for table in reversed(Base.metadata.sorted_tables):
            if table.name in _KEPT_TABLES:
                continue
            counts[table.name] = session.execute(delete(table)).rowcount  # type: ignore[attr-defined]
        profile = session.get(LocalProfile, 1)
        if profile is not None:
            profile.display_name = None
        workspace_id = workspace.id
    # Credentials live outside the database: they are deleted separately.
    for ref in secrets:
        try:
            secret_store.delete(ref)
        except Exception:
            counts["secrets_not_deleted"] = counts.get("secrets_not_deleted", 0) + 1
    counts["secrets_deleted"] = len(secrets) - counts.get("secrets_not_deleted", 0)
    _after_commit(database, indexer, "all", None)
    vacuum(database)
    with database.session() as session:
        record_audit(
            session, workspace_id, "workspace.wipe", "workspace", str(workspace_id),
            AuditResult.SUCCESS, request_id,
            {k: v for k, v in counts.items() if v},
        )  # fmt: skip
    return counts


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


@dataclass(frozen=True)
class StoredData:
    counts: dict[str, int]
    bytes_on_disk: dict[str, int]


def stored_data(session: Session, data_dir: Path) -> StoredData:
    """What is stored and how much space it takes: the data view."""

    def count(model: type[Any]) -> int:
        return session.scalar(select(func.count()).select_from(model)) or 0

    counts = {
        "projects": count(Project),
        "sources": count(SourceConnection),
        "files": count(SourceItem),
        "revisions": count(SourceRevision),
        "fragments": count(ContentChunk),
        "rules": count(BusinessRule),
        "entities": count(KnowledgeEntity),
        "evidence": count(EvidenceRecord),
        "analyses": count(AnalysisRun),
        "findings": count(Finding),
        "releases": count(SoftwareRelease),
        "audit_events": count(AuditEvent),
        "workspaces": count(Workspace),
    }
    database = data_dir / DATABASE_FILENAME
    vectors = data_dir / LEGACY_VECTOR_DIRNAME
    return StoredData(
        counts,
        {
            "database": sum(
                _size(path)
                for path in (database, database.with_name(database.name + "-wal"))
                if path.exists()
            ),
            "vectors": _size(vectors) if vectors.exists() else 0,
        },
    )
