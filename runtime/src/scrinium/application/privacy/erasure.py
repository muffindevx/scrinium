"""Deletion of sources, projects and the workspace, and a summary of what is stored.

Deleting has two phases. First, in a transaction, the rows are deleted. Then, once committed,
the disk is purged: LanceDB vectors (deleting creates a new version and the old ones remain),
FTS5 segments and the SQLite WAL. Without the second phase, deleted data would stay in the files.

What it cannot guarantee: copies the file system or the SSD keep in freed blocks,
backups made earlier, and exports already downloaded.
"""

import uuid
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
from scrinium.application.retrieval.indexing import Indexer
from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import AnalysisStatus, AnalysisType, AuditResult, SyncStatus
from scrinium.infrastructure.secret_store import SecretStore
from scrinium.infrastructure.sqlite.database import DATABASE_FILENAME, Database
from scrinium.infrastructure.sqlite.models import (
    AiConsent,
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
    Workspace,
)
from scrinium.infrastructure.vector_index.lance import VECTOR_DIRNAME

WIPE_CONFIRMATION: Final = "BORRAR TODO"
_RUNNING_SYNC: Final = (SyncStatus.QUEUED, SyncStatus.RUNNING)
_RUNNING_ANALYSIS: Final = (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING)
# Never deleted: the local workspace is unique and the profile is a fixed row.
_KEPT_TABLES: Final = frozenset({"workspaces", "local_profile"})


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
        "citing_questions": questions or 0,
        "citing_analyses": len(citing) - (questions or 0),
    }


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
) -> dict[str, int]:
    with database.session() as session:
        summary = sources.delete_source(session, source_id, request_id, delete_citing)
    # After committing: if it fails, orphan vectors remain, which search discards.
    indexer.delete_source(source_id)
    database.purge()
    return summary


def delete_project(
    database: Database,
    indexer: Indexer,
    project_id: uuid.UUID,
    confirmation: str,
    request_id: str | None,
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
        # The project's consents to remote models go with it (ADR-010); the provider stays.
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
    indexer.vector_index.delete_project(project_id)
    database.purge()
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
        # Remote providers' API keys too (ADR-010).
        secrets += [ref for ref in session.scalars(select(ModelProvider.secret_ref)) if ref]
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
    indexer.vector_index.reset()
    database.purge()
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
    vectors = data_dir / VECTOR_DIRNAME
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
