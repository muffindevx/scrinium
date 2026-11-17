"""The ⌘K palette (phase 6.2): rules, functions, files and tickets by name, as you type.

Name matching only, no model and no vectors: it has to answer between two keystrokes (target:
under 100 ms at the 95th percentile with 100 000 fragments, `benchmarks/palette_speed.py`).

SQLite's `LIKE` already ignores ASCII case; wrapping the column in `lower()` made every row
compute a new string and tripled the cost. Each group takes up to `_CANDIDATES` matches in table
order, so the scan stops early, and ranks them here: what starts with the text first, then the
shortest name.
"""

import uuid
from collections.abc import Callable, Iterable
from typing import Any, Final

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from scrinium.domain.enums import EntityType, ItemLifecycle
from scrinium.infrastructure.sqlite.models import (
    CodeSymbol,
    KnowledgeEntity,
    SourceConnection,
    SourceItem,
)

MIN_QUERY: Final = 2
MAX_QUERY: Final = 200
PER_GROUP: Final = 6
_CANDIDATES: Final = 200


def _pattern(text: str) -> str:
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _best[T](rows: Iterable[T], name: Callable[[T], str], text: str) -> list[T]:
    lowered = text.lower()

    def rank(row: T) -> tuple[bool, int]:
        return (not name(row).lower().startswith(lowered), len(name(row)))

    return sorted(rows, key=rank)[:PER_GROUP]


def palette(session: Session, project_id: uuid.UUID, query: str) -> dict[str, list[dict[str, Any]]]:
    text = " ".join(query.split())[:MAX_QUERY]
    groups: dict[str, list[dict[str, Any]]] = {"rules": [], "functions": [], "files": [],
                                                "tickets": []}  # fmt: skip
    if len(text) < MIN_QUERY:
        return groups
    pattern = _pattern(text)
    rules = session.execute(
        select(KnowledgeEntity.id, KnowledgeEntity.name)
        .where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
            KnowledgeEntity.name.like(pattern, escape="\\"),
        )
        .limit(_CANDIDATES)
    ).all()
    groups["rules"] = [
        {"id": str(rule_id), "title": name} for rule_id, name in _best(rules, lambda r: r[1], text)
    ]
    # Two steps: the names on their own table (a plain scan that stops early), then which of
    # their revisions are current files of this project. As one join, SQLite walked every file
    # first, and a query matching nothing took 80 ms with 100 000 fragments.
    named = session.execute(
        select(
            CodeSymbol.name,
            CodeSymbol.qualified_name,
            CodeSymbol.kind,
            CodeSymbol.start_line,
            CodeSymbol.source_revision_id,
        )
        .where(CodeSymbol.name.like(pattern, escape="\\"))
        .limit(_CANDIDATES * 10)
    ).all()
    current = (
        dict(
            session.execute(
                select(SourceItem.current_revision_id, SourceItem.provider_item_id)
                .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
                .where(
                    SourceItem.current_revision_id.in_({row[4] for row in named}),
                    SourceConnection.project_id == project_id,
                    SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
                )
            ).all()
        )
        if named
        else {}
    )
    functions = [(n, q, k, line, current[rev]) for n, q, k, line, rev in named if rev in current]
    groups["functions"] = [
        {"name": n, "qualified_name": q, "kind": k, "line": line, "path": p}
        for n, q, k, line, p in _best(functions, lambda r: r[0], text)
    ]
    files = session.execute(
        select(SourceItem.provider_item_id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
            SourceItem.provider_item_id.like(pattern, escape="\\"),
        )
        .limit(_CANDIDATES)
    ).all()
    groups["files"] = [
        {"path": path} for (path,) in _best(files, lambda r: r[0].rsplit("/", 1)[-1], text)
    ]
    labelled = session.execute(
        select(KnowledgeEntity.id, KnowledgeEntity.name, KnowledgeEntity.metadata_)
        .where(
            KnowledgeEntity.project_id == project_id,
            cast(KnowledgeEntity.metadata_["tickets"], String).like(pattern, escape="\\"),
        )
        .limit(_CANDIDATES)
    ).all()
    for entity_id, name, metadata in labelled:
        for ticket in (metadata or {}).get("tickets", []):
            if text.lower() in str(ticket).lower() and len(groups["tickets"]) < PER_GROUP:
                groups["tickets"].append(
                    {"ticket": str(ticket), "rule_id": str(entity_id), "rule": name}
                )
    return groups
