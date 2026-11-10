"""Rule watch: detects, without AI, when the code backing a rule changes.

For each rule it looks at the latest citation in each file and the functions it covers
(structural map). Against the file's current revision:

- same logical hash: nothing (a formatting or comment change does not affect the rule);
- another hash: the rule is flagged "Review: code changed" and the new function goes to the
  model with the rule, so it says whether it holds, is adjusted or no longer holds;
- the function or the file is gone: "Review: code removed".
"""

import uuid
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.domain.enums import EntityLifecycle, EntityType, ItemLifecycle
from scrinium.infrastructure.sqlite.models import (
    BusinessRuleVersion,
    CodeSymbol,
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    SourceItem,
    SourceRevision,
)

REVIEW_CODE_CHANGED: Final = "code_changed"
REVIEW_CODE_REMOVED: Final = "code_removed"
REVIEW_CODE_CONTRADICTS: Final = "code_contradicts"
WATCH_REASONS: Final = frozenset({REVIEW_CODE_CHANGED, REVIEW_CODE_REMOVED})


@dataclass(frozen=True)
class ChangedCode:
    """Where the code that backed a rule is now."""

    revision_id: uuid.UUID
    path: str
    symbol: str
    start_line: int
    end_line: int


@dataclass
class WatchedRule:
    entity_id: uuid.UUID
    version_id: uuid.UUID
    title: str
    statement: str
    changed: list[ChangedCode] = field(default_factory=list)
    removed: bool = False


def _latest_citations(session: Session, version_id: uuid.UUID) -> list[EvidenceRecord]:
    """The latest citation in each file: earlier ones are history."""
    rows = session.execute(
        select(EvidenceRecord, SourceRevision)
        .join(SourceRevision, SourceRevision.id == EvidenceRecord.source_revision_id)
        .where(EvidenceRecord.target_version_id == version_id)
        .order_by(SourceRevision.captured_at.desc())
    ).all()
    latest: dict[uuid.UUID, EvidenceRecord] = {}
    for evidence, revision in rows:
        latest.setdefault(revision.source_item_id, evidence)
    return list(latest.values())


def _symbols(session: Session, revision_id: uuid.UUID) -> list[CodeSymbol]:
    return list(
        session.scalars(
            select(CodeSymbol).where(
                CodeSymbol.source_revision_id == revision_id, CodeSymbol.kind != "class"
            )
        )
    )


def _compare(session: Session, evidence: EvidenceRecord, rule: WatchedRule) -> bool:
    """Compares a citation with the current code. True if the cited code still exists."""
    cited = session.get(SourceRevision, evidence.source_revision_id)
    if cited is None:
        return False
    item = session.get(SourceItem, cited.source_item_id)
    if item is None or item.lifecycle_status is not ItemLifecycle.ACTIVE:
        return False
    current_id = item.current_revision_id
    if current_id is None:
        return False
    if current_id == cited.id:
        return True
    start = int(evidence.locator.get("start_line", 0))
    end = int(evidence.locator.get("end_line", start))
    covering = [
        s for s in _symbols(session, cited.id) if s.start_line <= end and s.end_line >= start
    ]
    if not covering:
        # Code outside functions or without a grammar: the cited text is compared.
        current = session.get_one(SourceRevision, current_id)
        excerpt = evidence.excerpt or ""
        return bool(excerpt) and excerpt in (current.text_content or "")
    current_symbols = {s.qualified_name: s for s in _symbols(session, current_id)}
    found = False
    for symbol in covering:
        now = current_symbols.get(symbol.qualified_name)
        if now is None:
            continue
        found = True
        if now.logic_hash != symbol.logic_hash:
            rule.changed.append(
                ChangedCode(
                    current_id, cited.metadata_.get("path", ""), now.qualified_name,
                    now.start_line, now.end_line,
                )
            )  # fmt: skip
    return found


def detect_changes(session: Session, project_id: uuid.UUID) -> list[WatchedRule]:
    """Flags for review the rules whose code changed or disappeared. Returns all those
    with changed code (for the model) or removed code."""
    watched: list[WatchedRule] = []
    entities = session.scalars(
        select(KnowledgeEntity).where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
            KnowledgeEntity.lifecycle_status.not_in(
                [EntityLifecycle.RETIRED, EntityLifecycle.DEPRECATED]
            ),
        )
    )
    for entity in entities:
        version = session.scalars(
            select(KnowledgeEntityVersion)
            .where(KnowledgeEntityVersion.knowledge_entity_id == entity.id)
            .order_by(KnowledgeEntityVersion.version_number.desc())
            .limit(1)
        ).first()
        if version is None:
            continue
        content = session.get(BusinessRuleVersion, version.id)
        citations = _latest_citations(session, version.id)
        if content is None or not citations:
            continue
        rule = WatchedRule(entity.id, version.id, content.title, content.statement)
        still_there = [_compare(session, evidence, rule) for evidence in citations]
        rule.removed = not any(still_there)
        if rule.changed:
            entity.review_reason = REVIEW_CODE_CHANGED
            watched.append(rule)
        elif rule.removed:
            entity.review_reason = REVIEW_CODE_REMOVED
            watched.append(rule)
        elif entity.review_reason in WATCH_REASONS:
            # The code went back to what it was (or was re-anchored): nothing left to review.
            entity.review_reason = None
    session.flush()
    return watched
