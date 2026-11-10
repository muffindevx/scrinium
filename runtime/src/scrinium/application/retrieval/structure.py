"""Code symbols of the current revisions (tree-sitter). Derived and rebuildable.

They are computed on sync, together with the chunks. Earlier revisions keep
theirs: they are the history used to detect whether the code backing a rule changed.
"""

import uuid

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from scrinium.domain.enums import ItemLifecycle
from scrinium.infrastructure.code_structure import STRUCTURE_VERSION, language_for, parse_symbols
from scrinium.infrastructure.sqlite.models import (
    CodeSymbol,
    SourceConnection,
    SourceItem,
    SourceRevision,
)


def symbolize_revision(session: Session, revision: SourceRevision) -> int:
    path = revision.metadata_.get("path", "")
    language = language_for(path)
    if language is None or not revision.text_content:
        return 0
    symbols = parse_symbols(path, revision.text_content)
    for symbol in symbols:
        session.add(
            CodeSymbol(
                workspace_id=revision.workspace_id,
                source_revision_id=revision.id,
                language=language,
                kind=symbol.kind,
                name=symbol.name[:300],
                qualified_name=symbol.qualified_name[:1000],
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                content_hash=symbol.content_hash,
                logic_hash=symbol.logic_hash,
                calls=list(symbol.calls),
                structure_version=STRUCTURE_VERSION,
            )
        )
    return len(symbols)


def _pending(session: Session, connection_ids: list[uuid.UUID]) -> list[SourceRevision]:
    has_symbols = exists().where(CodeSymbol.source_revision_id == SourceRevision.id)
    revisions = session.scalars(
        select(SourceRevision)
        .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
        .where(
            SourceItem.source_connection_id.in_(connection_ids),
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            ~has_symbols,
        )
    )
    return [r for r in revisions if language_for(r.metadata_.get("path", "")) is not None]


def ensure_symbols(session: Session, connection_id: uuid.UUID) -> int:
    """Parses the current code revisions of a source that have no symbols yet."""
    return sum(symbolize_revision(session, r) for r in _pending(session, [connection_id]))


def ensure_project_symbols(session: Session, project_id: uuid.UUID) -> int:
    """For projects synced before the structural map existed."""
    connections = list(
        session.scalars(
            select(SourceConnection.id).where(SourceConnection.project_id == project_id)
        )
    )
    return sum(symbolize_revision(session, r) for r in _pending(session, connections))


def delete_symbols_of_source(session: Session, connection_id: uuid.UUID) -> None:
    revisions = (
        select(SourceRevision.id)
        .join(SourceItem, SourceItem.id == SourceRevision.source_item_id)
        .where(SourceItem.source_connection_id == connection_id)
    )
    session.execute(delete(CodeSymbol).where(CodeSymbol.source_revision_id.in_(revisions)))
