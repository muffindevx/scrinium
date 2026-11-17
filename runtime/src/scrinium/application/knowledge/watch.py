"""Rule watch: detects, without AI, when the code backing a rule changes.

For each rule it looks at the latest citation in each file and the functions it covers
(structural map). Against the file's current revision:

- same logical hash: nothing (a formatting or comment change does not affect the rule);
- another hash: the rule is flagged "Review: code changed" and the new function goes to the
  model with the rule, so it says whether it holds, is adjusted or no longer holds;
- the function or the file is gone: "Review: code removed".

A citation with an anchor (ADR-011) is followed by its decision instead of its function
(`reanchor`): the same decision with the same value, wherever it moved, keeps the rule as it is
and is cited again at its new lines; another value flags it; a decision found nowhere flags it as
changed while its function exists, and as removed otherwise. A file renamed without edits is
found by its content.
"""

import uuid
from dataclasses import dataclass, field, replace
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.anchoring import Match, OldAnchor, reanchor
from scrinium.application.knowledge.common import KnowledgeError
from scrinium.application.knowledge.evidence import anchored_locator, cite_source, decisions_of
from scrinium.application.knowledge.files import file_key, source_of
from scrinium.domain.enums import EntityLifecycle, EntityType, ItemLifecycle
from scrinium.infrastructure.code_structure import NESTED
from scrinium.infrastructure.decisions import Decision
from scrinium.infrastructure.sqlite.models import (
    BusinessRuleVersion,
    CodeSymbol,
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceConnection,
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
                CodeSymbol.source_revision_id == revision_id,
                CodeSymbol.kind.not_in(("class", NESTED)),
            )
        )
    )


@dataclass
class _Followed:
    """Where each anchored citation's decision is now, for one pass of the watch."""

    anchors: dict[uuid.UUID, OldAnchor] = field(default_factory=dict)
    matches: dict[uuid.UUID, Match] = field(default_factory=dict)
    revisions: dict[str, SourceRevision] = field(default_factory=dict)
    # (evidence, where its decision is now) for decisions that did not change: cited again.
    recite: list[tuple[EvidenceRecord, Match]] = field(default_factory=list)


def _current_files(session: Session, project_id: uuid.UUID) -> dict[str, SourceRevision]:
    """File key (source and path) to current revision of every active item of the project."""
    rows = session.execute(
        select(SourceRevision, SourceItem.source_connection_id)
        .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(
            SourceConnection.project_id == project_id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
        )
    ).all()
    return {
        file_key(source_id, str(revision.metadata_.get("path", ""))): revision
        for revision, source_id in rows
    }


def _path(revision: SourceRevision) -> str:
    return str(revision.metadata_.get("path", ""))


def _follow(session: Session, project_id: uuid.UUID, citations: list[EvidenceRecord]) -> _Followed:
    followed = _Followed()
    current = _current_files(session, project_id)
    cited_hashes: dict[str, str] = {}
    for evidence in citations:
        locator = anchored_locator(session, evidence)
        anchor = OldAnchor.from_locator(evidence.id, locator)
        cited = session.get(SourceRevision, evidence.source_revision_id)
        if anchor is None or cited is None:
            continue
        source_id = locator.get("source_id") or source_of(session, cited.id)
        anchor = replace(anchor, path=file_key(source_id, anchor.path))
        now = current.get(anchor.path)
        if now is not None and now.id == cited.id:
            continue  # The cited code is the current one: nothing to follow.
        followed.anchors[evidence.id] = anchor
        cited_hashes[anchor.path] = cited.content_hash
    if not followed.anchors:
        return followed
    # A file gone from its path whose exact content is elsewhere was renamed.
    by_hash = {revision.content_hash: path for path, revision in current.items()}
    renames = {
        path: by_hash[content]
        for path, content in cited_hashes.items()
        if path not in current and content in by_hash
    }
    near = {renames.get(path, path) for path in cited_hashes}
    decisions: dict[str, list[Decision]] = {
        key: decisions_of(_path(current[key]), current[key]) for key in near if key in current
    }
    anchors = list(followed.anchors.values())
    followed.matches = {
        key: match
        for key, match in reanchor(anchors, decisions, renames).items()
        if isinstance(key, uuid.UUID)
    }
    lost = [anchor for anchor in anchors if anchor.key not in followed.matches]
    if lost:
        # Moved to another file: only then is the rest of the project parsed.
        everywhere = {key: decisions_of(_path(rev), rev) for key, rev in current.items()}
        for key, match in reanchor(lost, everywhere, renames).items():
            if isinstance(key, uuid.UUID):
                followed.matches[key] = match
    followed.revisions = current
    return followed


def _compare_anchored(
    session: Session, evidence: EvidenceRecord, rule: WatchedRule, followed: _Followed
) -> bool:
    anchor = followed.anchors[evidence.id]
    match = followed.matches.get(evidence.id)
    if match is not None:
        revision = followed.revisions[match.path]
        if match.value_changed(anchor):
            decision = match.decision
            rule.changed.append(
                ChangedCode(
                    revision.id, _path(revision), decision.symbol or "", decision.start_line,
                    decision.end_line,
                )
            )  # fmt: skip
        else:
            followed.recite.append((evidence, match))
        return True
    # The decision is nowhere: if its function is still there, it was rewritten.
    still = followed.revisions.get(anchor.path)
    if still is None or anchor.symbol is None:
        return False
    holder = next(
        (s for s in _symbols(session, still.id) if s.qualified_name == anchor.symbol), None
    )
    if holder is None:
        return False
    rule.changed.append(
        ChangedCode(
            still.id, _path(still), holder.qualified_name, holder.start_line, holder.end_line
        )
    )
    return True


def _recite(session: Session, project_id: uuid.UUID, followed: _Followed) -> None:
    """Cites again, at their new lines, the decisions that moved without changing."""
    project = session.get(Project, project_id)
    if project is None:
        return
    for evidence, match in followed.recite:
        if evidence.target_entity_id is None:
            continue
        try:
            cite_source(
                session, project, entity_id=evidence.target_entity_id,
                version_id=evidence.target_version_id,
                revision_id=followed.revisions[match.path].id,
                start_line=match.decision.start_line, end_line=match.decision.end_line,
                role=evidence.evidence_role, evidence_type=evidence.evidence_type,
                method=evidence.verification_method,
            )  # fmt: skip
        except KnowledgeError:
            continue


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
    rules: list[tuple[KnowledgeEntity, WatchedRule, list[EvidenceRecord]]] = []
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
        rules.append((entity, rule, citations))
    followed = _follow(
        session, project_id, [evidence for _, _, citations in rules for evidence in citations]
    )
    for entity, rule, citations in rules:
        still_there = [
            _compare_anchored(session, evidence, rule, followed)
            if evidence.id in followed.anchors
            else _compare(session, evidence, rule)
            for evidence in citations
        ]
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
    _recite(session, project_id, followed)
    session.flush()
    return watched
