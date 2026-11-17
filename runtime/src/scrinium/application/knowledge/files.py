"""A file is (source, path), never the path alone (ADR-011, phase 1.5).

A product can have several sources (a UI and a backend, or two subfolders of a monorepo), and
two of them may both hold `src/utils.ts`. Every layer that keys files by path within a project
uses these helpers, so the two never mix.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.infrastructure.sqlite.models import SourceConnection, SourceItem, SourceRevision


def file_key(source_id: uuid.UUID | str | None, path: str) -> str:
    """An opaque key for one file of one source."""
    return f"{source_id}:{path}"


def source_of(session: Session, revision_id: uuid.UUID) -> uuid.UUID | None:
    """The source a revision belongs to."""
    return session.scalar(
        select(SourceItem.source_connection_id)
        .join(SourceRevision, SourceRevision.source_item_id == SourceItem.id)
        .where(SourceRevision.id == revision_id)
    )


def source_names(session: Session, project_id: uuid.UUID) -> dict[uuid.UUID, str]:
    """A distinct, readable name per source of the project (`app`, `app-2`)."""
    names: dict[uuid.UUID, str] = {}
    used: set[str] = set()
    for connection in session.scalars(
        select(SourceConnection)
        .where(SourceConnection.project_id == project_id)
        .order_by(SourceConnection.created_at, SourceConnection.id)
    ):
        base = (connection.display_name or "fuente").replace("/", "-")
        name, number = base, 1
        while name in used:
            number += 1
            name = f"{base}-{number}"
        used.add(name)
        names[connection.id] = name
    return names


def shown_path(names: dict[uuid.UUID, str], source_id: uuid.UUID | None, path: str) -> str:
    """The path as a project-wide tree shows it: under its source when there are several."""
    if len(names) <= 1 or source_id is None or source_id not in names:
        return path
    return f"{names[source_id]}/{path}"
