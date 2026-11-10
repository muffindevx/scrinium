"""Local sources: registration, sync, line lookup and deletion."""

import logging
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.orm import Session

from scrinium.application.analysis.extraction_queue import delete_tasks_of_revisions
from scrinium.application.ingestion.plan import Plan, SourceError, build_plan, resolve_root
from scrinium.application.ingestion.policy import SourceKind
from scrinium.application.knowledge.deletion import (
    citing_runs,
    delete_runs,
    forget_releases,
    forget_revisions,
)
from scrinium.application.retrieval.indexing import Indexer, delete_chunks_of_source, ensure_chunks
from scrinium.application.retrieval.structure import delete_symbols_of_source, ensure_symbols
from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import (
    AuditResult,
    ConnectionStatus,
    ItemLifecycle,
    SyncStatus,
    SyncTrigger,
)
from scrinium.infrastructure.local_files.git_reader import GitError
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    Project,
    SoftwareRelease,
    SourceConnection,
    SourceItem,
    SourceRevision,
    SyncRun,
    utc_now,
)

logger = logging.getLogger("scrinium.ingestion")

PROVIDERS: Final = {
    SourceKind.GIT_REPOSITORY: "local_git",
    SourceKind.FOLDER: "local_folder",
}
_KINDS: Final = {provider: kind for kind, provider in PROVIDERS.items()}
PARSER_NAME: Final = "text"
PARSER_VERSION: Final = "1"
MAX_LINES_PER_REQUEST: Final = 500
_ACTIVE: Final = (SyncStatus.QUEUED, SyncStatus.RUNNING)


class SourceNotFoundError(LookupError):
    pass


class SyncInProgressError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceSettings:
    kind: SourceKind
    root: Path
    exclude_patterns: list[str]


def settings_of(connection: SourceConnection) -> SourceSettings:
    config = connection.configuration
    return SourceSettings(
        _KINDS[connection.provider], Path(config["root"]), list(config["exclude_patterns"])
    )


def _connection(session: Session, source_id: uuid.UUID) -> SourceConnection:
    workspace = local_workspace(session)
    connection = session.get(SourceConnection, source_id)
    if connection is None or connection.workspace_id != workspace.id:
        raise SourceNotFoundError
    if connection.provider not in _KINDS:
        raise SourceNotFoundError
    return connection


def project_of(session: Session, project_id: uuid.UUID) -> Project:
    project = session.get(Project, project_id)
    if project is None or project.workspace_id != local_workspace(session).id:
        raise SourceNotFoundError
    return project


def preview(kind: SourceKind, raw_path: str, patterns: list[str], data_dir: Path) -> Plan:
    return build_plan(kind, resolve_root(kind, raw_path, data_dir), patterns)


def latest_run(session: Session, connection_id: uuid.UUID) -> SyncRun | None:
    return session.scalars(
        select(SyncRun)
        .where(SyncRun.source_connection_id == connection_id)
        .order_by(SyncRun.created_at.desc(), SyncRun.id)
        .limit(1)
    ).first()


def list_sources(session: Session, project: Project) -> list[SourceConnection]:
    return list(
        session.scalars(
            select(SourceConnection)
            .where(SourceConnection.project_id == project.id)
            .order_by(SourceConnection.created_at)
        )
    )


def count_active_items(session: Session, connection_id: uuid.UUID) -> int:
    query = select(func.count()).where(
        SourceItem.source_connection_id == connection_id,
        SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
    )
    return session.scalar(query) or 0


def _queue_run(
    session: Session, connection: SourceConnection, trigger: SyncTrigger, request_id: str | None
) -> SyncRun:
    running = select(SyncRun.id).where(
        SyncRun.source_connection_id == connection.id, SyncRun.status.in_(_ACTIVE)
    )
    if session.scalar(running) is not None:
        raise SyncInProgressError
    previous = latest_run(session, connection.id)
    run = SyncRun(
        workspace_id=connection.workspace_id,
        source_connection_id=connection.id,
        trigger=trigger,
        cursor_before=previous.cursor_after if previous else None,
    )
    session.add(run)
    session.flush()
    record_audit(
        session, connection.workspace_id, "source.sync", "source", str(connection.id),
        AuditResult.SUCCESS, request_id, {"sync_run_id": str(run.id), "trigger": trigger.value},
    )  # fmt: skip
    return run


def create_source(
    session: Session,
    project: Project,
    kind: SourceKind,
    raw_path: str,
    patterns: list[str],
    data_dir: Path,
    request_id: str | None,
) -> tuple[SourceConnection, SyncRun]:
    root = resolve_root(kind, raw_path, data_dir)
    connection = SourceConnection(
        workspace_id=project.workspace_id,
        project_id=project.id,
        provider=PROVIDERS[kind],
        display_name=root.name or str(root),
        status=ConnectionStatus.ACTIVE,
        configuration={"root": str(root), "exclude_patterns": patterns},
    )
    session.add(connection)
    session.flush()
    record_audit(
        session, project.workspace_id, "source.create", "source", str(connection.id),
        AuditResult.SUCCESS, request_id, {"provider": connection.provider},
    )  # fmt: skip
    return connection, _queue_run(session, connection, SyncTrigger.INITIAL, request_id)


def request_sync(session: Session, source_id: uuid.UUID, request_id: str | None) -> SyncRun:
    return _queue_run(session, _connection(session, source_id), SyncTrigger.MANUAL, request_id)


def source_revision_ids(connection_id: uuid.UUID) -> Select[uuid.UUID]:
    items = select(SourceItem.id).where(SourceItem.source_connection_id == connection_id)
    return select(SourceRevision.id).where(SourceRevision.source_item_id.in_(items))


def source_references(session: Session, connection: SourceConnection) -> set[str]:
    """Identifiers an analysis may cite: the source, its revisions and its releases."""
    releases = select(SoftwareRelease.id).where(
        SoftwareRelease.source_connection_id == connection.id
    )
    ids = [connection.id, *session.scalars(source_revision_ids(connection.id))]
    ids += list(session.scalars(releases))
    return {str(value) for value in ids}


def delete_source(
    session: Session, source_id: uuid.UUID, request_id: str | None, delete_citing: bool = False
) -> dict[str, int]:
    """Deletes the source and everything derived: files, revisions, sync history,
    evidence citing it, releases, and extracted rules that relied on it alone.
    With `delete_citing`, also the questions and analyses that cite it."""
    connection = _connection(session, source_id)
    running = select(SyncRun.id).where(
        SyncRun.source_connection_id == connection.id, SyncRun.status.in_(_ACTIVE)
    )
    if session.scalar(running) is not None:
        raise SyncInProgressError
    items = select(SourceItem.id).where(SourceItem.source_connection_id == connection.id)
    summary = {"analyses_deleted": 0}
    if delete_citing and connection.project_id is not None:
        citing = citing_runs(session, connection.project_id, source_references(session, connection))
        summary["analyses_deleted"] = delete_runs(session, citing)
    delete_chunks_of_source(session, connection.id)
    delete_symbols_of_source(session, connection.id)
    delete_tasks_of_revisions(session, source_revision_ids(connection.id))
    summary.update(forget_revisions(session, source_revision_ids(connection.id)))
    summary["releases_deleted"] = forget_releases(session, connection.id)
    session.execute(
        update(SourceItem)
        .where(SourceItem.source_connection_id == connection.id)
        .values(current_revision_id=None)
    )
    revisions = session.execute(
        delete(SourceRevision).where(SourceRevision.source_item_id.in_(items))
    )
    summary["revisions_deleted"] = revisions.rowcount  # type: ignore[attr-defined]
    session.execute(delete(SourceItem).where(SourceItem.source_connection_id == connection.id))
    session.execute(delete(SyncRun).where(SyncRun.source_connection_id == connection.id))
    session.delete(connection)
    record_audit(
        session, connection.workspace_id, "source.delete", "source", str(connection.id),
        AuditResult.SUCCESS, request_id, dict(summary),
    )  # fmt: skip
    return summary


def mark_interrupted_runs(session: Session) -> None:
    """At startup: what the previous process left halfway will not progress any more."""
    session.execute(
        update(SyncRun)
        .where(SyncRun.status.in_(_ACTIVE))
        .values(status=SyncStatus.FAILED, error_summary="interrupted", finished_at=utc_now())
    )


def _apply_plan(session: Session, run: SyncRun, connection: SourceConnection, plan: Plan) -> None:
    items = {
        item.provider_item_id: item
        for item in session.scalars(
            select(SourceItem).where(SourceItem.source_connection_id == connection.id)
        )
    }
    now = utc_now()
    created = updated = 0
    for file in plan.included:
        item = items.pop(file.path, None)
        is_new = item is None
        if item is None:
            item = SourceItem(
                workspace_id=connection.workspace_id,
                source_connection_id=connection.id,
                provider_item_id=file.path,
                item_type="file",
                canonical_uri=(plan.root / file.path).as_uri(),
                title=file.path,
            )
            session.add(item)
            session.flush()
            created += 1
        known = select(SourceRevision.id).where(
            SourceRevision.source_item_id == item.id,
            SourceRevision.revision_key == file.revision_key,
        )
        revision_id = session.scalar(known)
        if revision_id is None:
            revision = SourceRevision(
                workspace_id=connection.workspace_id,
                source_item_id=item.id,
                revision_key=file.revision_key,
                content_hash=file.content_hash,
                text_content=file.text,
                metadata_=_locator_base(plan, file.path, file.text),
                parser_name=PARSER_NAME,
                parser_version=PARSER_VERSION,
            )
            session.add(revision)
            session.flush()
            revision_id = revision.id
        changed = (
            item.current_revision_id != revision_id
            or item.lifecycle_status is not ItemLifecycle.ACTIVE
        )
        item.current_revision_id = revision_id
        item.lifecycle_status = ItemLifecycle.ACTIVE
        if changed and not is_new:
            updated += 1
        item.last_seen_at = now

    # What is gone, or is now excluded, stops being available. Its revisions are
    # kept as history until the source is deleted.
    deleted = 0
    for item in items.values():
        if item.lifecycle_status is ItemLifecycle.ACTIVE:
            item.lifecycle_status = ItemLifecycle.DELETED
            deleted += 1

    # Chunks (and their FTS5 entry) in the same transaction: text search works as soon as
    # the sync finishes. Vectors come later.
    ensure_chunks(session, connection.id)
    ensure_symbols(session, connection.id)

    run.items_seen = len(plan.included) + len(plan.excluded)
    run.items_created = created
    run.items_updated = updated
    run.items_deleted = deleted
    run.items_excluded = len(plan.excluded)
    run.cursor_after = plan.commit
    run.status = SyncStatus.SUCCEEDED
    run.finished_at = now
    connection.last_successful_sync_at = now
    connection.status = ConnectionStatus.ACTIVE


def _locator_base(plan: Plan, path: str, text: str) -> dict[str, Any]:
    """What is needed to cite: path, commit and line count. The lines go in the citation."""
    return {
        "path": path,
        "commit": plan.commit,
        "line_count": len(text.splitlines()),
    }


def run_sync(database: Database, run_id: uuid.UUID, data_dir: Path) -> None:
    with database.session() as session:
        run = session.get(SyncRun, run_id)
        if run is None or run.status is not SyncStatus.QUEUED:
            return
        run.status = SyncStatus.RUNNING
        run.started_at = utc_now()
        settings = settings_of(_connection(session, run.source_connection_id))

    error: str | None = None
    plan: Plan | None = None
    try:
        root = resolve_root(settings.kind, str(settings.root), data_dir)
        plan = build_plan(settings.kind, root, settings.exclude_patterns)
    except (SourceError, GitError) as failure:
        error = str(failure)
    except Exception as failure:
        # Type only: the message could contain paths or source content.
        logger.error("sync_failed run=%s type=%s", run_id, type(failure).__name__)
        error = "internal_error"

    with database.session() as session:
        run = session.get(SyncRun, run_id)
        if run is None:
            return
        connection = _connection(session, run.source_connection_id)
        if plan is not None:
            _apply_plan(session, run, connection, plan)
        else:
            run.status = SyncStatus.FAILED
            run.error_summary = error
            run.finished_at = utc_now()
            connection.status = ConnectionStatus.ERROR
        record_audit(
            session, connection.workspace_id, "sync.finish", "sync_run", str(run.id),
            AuditResult.SUCCESS if plan is not None else AuditResult.FAILURE, None,
            {"created": run.items_created, "updated": run.items_updated,
             "deleted": run.items_deleted, "excluded": run.items_excluded, "error": error},
        )  # fmt: skip


@dataclass(frozen=True)
class CitedLines:
    revision: SourceRevision
    start: int
    end: int
    lines: list[str]


def read_lines(session: Session, revision_id: uuid.UUID, start: int, end: int) -> CitedLines:
    """Lines of a stored revision, not from disk: the citation stays valid even if it changes."""
    revision = session.get(SourceRevision, revision_id)
    if revision is None or revision.workspace_id != local_workspace(session).id:
        raise SourceNotFoundError
    lines = (revision.text_content or "").splitlines()
    if start < 1 or end < start or start > max(len(lines), 1):
        raise ValueError("invalid range")
    end = min(end, len(lines), start + MAX_LINES_PER_REQUEST - 1)
    return CitedLines(revision, start, end, lines[start - 1 : end])


def current_revisions(
    session: Session, source_id: uuid.UUID, limit: int
) -> list[tuple[SourceItem, SourceRevision]]:
    connection = _connection(session, source_id)
    query = (
        select(SourceItem, SourceRevision)
        .join(SourceRevision, SourceRevision.id == SourceItem.current_revision_id)
        .where(
            SourceItem.source_connection_id == connection.id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
        )
        .order_by(SourceItem.provider_item_id)
        .limit(limit)
    )
    return [(item, revision) for item, revision in session.execute(query)]


class IngestionRunner:
    """Runs syncs one at a time, off the request thread."""

    def __init__(
        self,
        database: Database,
        data_dir: Path,
        indexer: Indexer,
        executor: Executor | None = None,
    ) -> None:
        self._database = database
        self._data_dir = data_dir
        self.indexer = indexer
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix="sync")
        # What to do after each sync (rule watch); the application sets it.
        self.after_sync: Callable[[uuid.UUID], object] | None = None

    def submit(self, run_id: uuid.UUID) -> None:
        self._executor.submit(self._run, run_id)

    def submit_embeddings(self) -> None:
        self._executor.submit(self._guarded, self.indexer.embed_pending)

    def submit_rebuild(self) -> None:
        self._executor.submit(self._guarded, self.indexer.rebuild)

    def _run(self, run_id: uuid.UUID) -> None:
        self._guarded(lambda: run_sync(self._database, run_id, self._data_dir))
        after = self.after_sync
        if after is not None:
            self._guarded(lambda: after(run_id))
        # Vectors, after each sync: it is the slow part and can fail without a model.
        self._guarded(self.indexer.embed_pending)

    @staticmethod
    def _guarded(task: Callable[[], object]) -> None:
        try:
            task()
        except Exception as failure:
            logger.error("background_task_failed type=%s", type(failure).__name__)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
