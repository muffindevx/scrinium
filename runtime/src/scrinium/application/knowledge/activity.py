"""What happened in a project, newest first: Home's "Ver actividad".

Built from what each table already keeps (analysis runs, sync runs) and from the audit log for
what only it records: rule reviews and ticket links. The audit log's bulk rows (each evidence,
version and relation an extraction writes) stay out: the extraction itself is one event.
"""

import uuid
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    AuditEvent,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceConnection,
    SyncRun,
)

MAX_EVENTS: Final = 200
# Audit actions about one rule that a person did; the rest of the log is plumbing.
_RULE_ACTIONS: Final = (
    "knowledge_version.verify",
    "ticket_link.confirm",
    "ticket_link.dismiss",
    "ticket_link.remove",
)
# How far back the audit log is read: enough for MAX_EVENTS reviews among other rows.
_AUDIT_SCAN: Final = 2000


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _analyses(session: Session, project: Project, limit: int) -> list[dict[str, Any]]:
    runs = session.scalars(
        select(AnalysisRun)
        .where(AnalysisRun.project_id == project.id)
        .order_by(AnalysisRun.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "at": _iso(run.created_at),
            "kind": f"analysis.{run.analysis_type.value}",
            "subject": run.question[:300],
            "status": run.status.value,
            "ref": str(run.id),
            "finished_at": _iso(run.finished_at),
        }
        for run in runs
    ]


def _syncs(session: Session, project: Project, limit: int) -> list[dict[str, Any]]:
    names = dict(
        session.execute(
            select(SourceConnection.id, SourceConnection.display_name).where(
                SourceConnection.project_id == project.id
            )
        ).all()
    )
    if not names:
        return []
    runs = session.scalars(
        select(SyncRun)
        .where(SyncRun.source_connection_id.in_(list(names)))
        .order_by(SyncRun.created_at.desc())
        .limit(limit)
    )
    return [
        {
            "at": _iso(run.finished_at or run.created_at),
            "kind": "sync",
            "subject": names[run.source_connection_id],
            "status": run.status.value,
            "ref": str(run.source_connection_id),
            "counts": {
                "created": run.items_created,
                "updated": run.items_updated,
                "deleted": run.items_deleted,
            },
        }
        for run in runs
    ]


def _rule_events(session: Session, project: Project, limit: int) -> list[dict[str, Any]]:
    rows = session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.workspace_id == project.workspace_id, AuditEvent.action.in_(_RULE_ACTIONS)
        )
        .order_by(AuditEvent.created_at.desc())
        .limit(_AUDIT_SCAN)
    ).all()
    # A review names the version; a ticket link names the rule.
    version_ids = {
        uuid.UUID(row.resource_id)
        for row in rows
        if row.action == "knowledge_version.verify" and row.resource_id
    }
    entity_of = dict(
        session.execute(
            select(KnowledgeEntityVersion.id, KnowledgeEntityVersion.knowledge_entity_id).where(
                KnowledgeEntityVersion.id.in_(version_ids)
            )
        ).all()
    )
    entity_ids = set(entity_of.values()) | {
        uuid.UUID(row.resource_id)
        for row in rows
        if row.action != "knowledge_version.verify" and row.resource_id
    }
    rules = dict(
        session.execute(
            select(KnowledgeEntity.id, KnowledgeEntity.name).where(
                KnowledgeEntity.id.in_(entity_ids), KnowledgeEntity.project_id == project.id
            )
        ).all()
    )
    events: list[dict[str, Any]] = []
    for row in rows:
        if not row.resource_id:
            continue
        resource = uuid.UUID(row.resource_id)
        entity = entity_of.get(resource) if row.action == "knowledge_version.verify" else resource
        if entity is None or entity not in rules:
            continue
        meta = row.metadata_ or {}
        events.append(
            {
                "at": _iso(row.created_at),
                "kind": row.action,
                "subject": rules[entity],
                "status": row.result.value,
                "ref": str(entity),
                **({"to": meta["to"]} if "to" in meta else {}),
                **({"ticket": meta["ticket"]} if "ticket" in meta else {}),
            }
        )
        if len(events) >= limit:
            break
    return events


def activity(session: Session, project: Project, limit: int = 50) -> list[dict[str, Any]]:
    """The project's last `limit` events of every kind, newest first."""
    limit = max(1, min(limit, MAX_EVENTS))
    events = (
        _analyses(session, project, limit)
        + _syncs(session, project, limit)
        + _rule_events(session, project, limit)
    )
    events.sort(key=lambda event: event["at"] or "", reverse=True)
    return events[:limit]
