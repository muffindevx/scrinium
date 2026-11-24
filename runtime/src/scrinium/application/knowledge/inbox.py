"""The review inbox: what a person should look at next, and in what order (phase 4, Annex B.8).

Every rule not yet reviewed, and every rule the watch or the consistency review flagged, is an
item. The order is the value of the information a review gives:

    score = impact * uncertainty / cost

- **impact:** how many rules depend on it (N7), how often its file changed in the last 90 days,
  its criticality and how many bug tickets point to it (phase 10.5);
- **uncertainty:** 1 if nobody reviewed it; higher when a verified rule drifted (its values
  changed); lower when it is in a group of near-identical rules one of which a person
  confirmed;
- **cost:** how much code a person must read (the cited lines).

**Groups** (Annex B.9): rules anchored to the same decision (its shape and its values), rules
whose statements are near-identical (MinHash with banded LSH, Jaccard ≥ 0.75), and rules that
name the same quoted values about the same subject in different files (B4: a column's allowed
values in `schema.ts` and in its SQL migration), joined with union-find. A group can be
confirmed, rejected or merged in one action.

**F2** (phase 4.3): a proposal written by a model whose statement says a number or a `name`
its anchored code does not hold never reaches the inbox; it is counted apart, to be redone.

All reads are a handful of queries for the whole project, never one per rule: the order is
computed per request (O(n log n)), which the plan's incremental heap would only beat with far
more rules than a project has.
"""

import math
import re
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scrinium.application.analysis.rule_checks import (
    decision_reach,
    quoted_literals,
    unanchored_literals,
    unanchored_terms,
)
from scrinium.application.knowledge.evidence import decisions_of
from scrinium.application.trackers.evidence import bug_tickets_by_rule
from scrinium.domain.enums import (
    Criticality,
    EntityLifecycle,
    EntityType,
    RelationType,
    VerificationStatus,
    VersionOrigin,
)
from scrinium.infrastructure.decisions import DECISIONS_VERSION, anchor_for, similarity, sketch_of
from scrinium.infrastructure.sqlite.models import (
    BusinessRule,
    BusinessRuleVersion,
    EntityRelationship,
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceRevision,
    utc_now,
)

_REVIEWED: Final = frozenset({VerificationStatus.HUMAN_VERIFIED, VerificationStatus.REJECTED})
_GENERATED: Final = frozenset({VersionOrigin.SOURCE_EXTRACTION, VersionOrigin.DERIVED_ANALYSIS})
_CRITICALITY: Final = {
    Criticality.LOW: 1.0,
    Criticality.MEDIUM: 2.0,
    Criticality.HIGH: 3.0,
    Criticality.CRITICAL: 4.0,
}
_UNCERTAINTY: Final = {
    "rule_changed": 1.5,
    "code_changed": 1.2,
    "code_removed": 1.1,
    "code_contradicts": 1.4,
    "contradiction": 1.3,
}
_GROUP_CONFIRMED: Final = 0.3
_DEPENDENCY_TYPES: Final = (RelationType.DEPENDS_ON, RelationType.IMPACTS)
_CHURN_DAYS: Final = 90
_PENDING_PREFIX: Final = "Pendiente de redactar."
_NOW_REASONS: Final = frozenset({"rule_changed", "code_changed", "code_contradicts"})
_SIMILAR: Final = 0.75
# B4: rules on the same decision must also say something alike to be grouped.
_SAME_DECISION: Final = 0.3
_BANDS: Final = 8
# One bug weighs like a step of criticality; ten weigh about five (logarithmic).
_BUG_WEIGHT: Final = 2.0


@dataclass
class _Rule:
    """The columns the inbox reads, not whole rows: thousands of rules load in milliseconds."""

    entity_id: uuid.UUID
    review_reason: str | None
    version_id: uuid.UUID
    status: VerificationStatus
    origin: VersionOrigin
    title: str
    statement: str
    criticality: Criticality
    citation: EvidenceRecord | None = None
    shape: str | None = None


# MinHash of a version's statement, by version id: a version never changes, so it is computed
# once per process. Bounded, like the decisions cache.
_SKETCHES: dict[uuid.UUID, str] = {}
_SKETCHES_SIZE: Final = 100_000


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[uuid.UUID, uuid.UUID] = {}

    def find(self, item: uuid.UUID) -> uuid.UUID:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, first: uuid.UUID, second: uuid.UUID) -> None:
        a, b = self.find(first), self.find(second)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


def _rules(session: Session, project: Project) -> list[_Rule]:
    latest = (
        select(
            KnowledgeEntityVersion.knowledge_entity_id,
            func.max(KnowledgeEntityVersion.version_number).label("number"),
        )
        .where(KnowledgeEntityVersion.project_id == project.id)
        .group_by(KnowledgeEntityVersion.knowledge_entity_id)
        .subquery()
    )
    rows = session.execute(
        select(
            KnowledgeEntity.id,
            KnowledgeEntity.review_reason,
            KnowledgeEntityVersion.id,
            KnowledgeEntityVersion.verification_status,
            KnowledgeEntityVersion.origin,
            BusinessRuleVersion.title,
            BusinessRuleVersion.statement,
            BusinessRule.criticality,
        )
        .join(latest, latest.c.knowledge_entity_id == KnowledgeEntity.id)
        .join(
            KnowledgeEntityVersion,
            (KnowledgeEntityVersion.knowledge_entity_id == KnowledgeEntity.id)
            & (KnowledgeEntityVersion.version_number == latest.c.number),
        )
        .join(
            BusinessRuleVersion,
            BusinessRuleVersion.knowledge_entity_version_id == KnowledgeEntityVersion.id,
        )
        .join(BusinessRule, BusinessRule.knowledge_entity_id == KnowledgeEntity.id)
        .where(
            KnowledgeEntity.project_id == project.id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
            KnowledgeEntity.lifecycle_status.not_in(
                [EntityLifecycle.RETIRED, EntityLifecycle.DEPRECATED]
            ),
        )
    ).all()
    rules = [_Rule(*row) for row in rows]
    # The first code citation of each latest version, in one query.
    by_version = {rule.version_id: rule for rule in rules}
    citations = session.scalars(
        select(EvidenceRecord)
        .where(
            EvidenceRecord.target_version_id.in_(list(by_version)),
            EvidenceRecord.source_revision_id.is_not(None),
        )
        .order_by(EvidenceRecord.created_at)
    )
    for record in citations:
        rule = by_version[record.target_version_id]  # type: ignore[index]
        if rule.citation is None:
            rule.citation = record
            anchor = (record.locator or {}).get("anchor")
            if isinstance(anchor, dict) and str(anchor.get("version")) == DECISIONS_VERSION:
                # The shape alone is any `enum` column; with its values, this decision.
                shape = str(anchor.get("shape") or "")
                rule.shape = f"{shape}:{anchor.get('value') or ''}" if shape else None
    return rules


def _dependents(session: Session, project: Project) -> dict[uuid.UUID, int]:
    rows = session.execute(
        select(EntityRelationship.to_entity_id, func.count())
        .where(
            EntityRelationship.project_id == project.id,
            EntityRelationship.relation_type.in_(_DEPENDENCY_TYPES),
        )
        .group_by(EntityRelationship.to_entity_id)
    )
    return {row[0]: row[1] for row in rows}


def _churn(session: Session, revision_ids: set[uuid.UUID]) -> dict[uuid.UUID, int]:
    """Revisions in the last 90 days of each cited revision's file."""
    if not revision_ids:
        return {}
    items = dict(
        session.execute(
            select(SourceRevision.id, SourceRevision.source_item_id).where(
                SourceRevision.id.in_(list(revision_ids))
            )
        ).all()
    )
    since = utc_now() - timedelta(days=_CHURN_DAYS)
    counts = dict(
        session.execute(
            select(SourceRevision.source_item_id, func.count())
            .where(
                SourceRevision.source_item_id.in_(set(items.values())),
                SourceRevision.captured_at >= since,
            )
            .group_by(SourceRevision.source_item_id)
        ).all()
    )
    return {revision: counts.get(item, 0) for revision, item in items.items()}


def _statement_sketch(rule: _Rule) -> str:
    found = _SKETCHES.get(rule.version_id)
    if found is None:
        words = "".join(c if c.isalnum() else " " for c in rule.statement.lower()).split()
        found = sketch_of(" ".join(words))
        if len(_SKETCHES) >= _SKETCHES_SIZE:
            _SKETCHES.pop(next(iter(_SKETCHES)))
        _SKETCHES[rule.version_id] = found
    return found


# B4: words that say nothing about what a statement is about ("debe estar limitado a").
_FILLER: Final = frozenset(
    {
        "a", "al", "con", "de", "del", "el", "en", "la", "las", "lo", "los", "o", "para", "por",
        "se", "su", "sus", "un", "una", "unos", "unas", "y", "que", "como", "uno", "solo",
        "debe", "deben", "ser", "estar", "esta", "estan", "es", "son", "tener", "tiene",
        "limitado", "limitada", "limitados", "limitadas", "valor", "valores", "permitido",
        "permitida", "permitidos", "permitidas", "lista", "pertenecer", "definido", "definida",
        "definidos", "definidas", "posible", "posibles", "siguiente", "siguientes", "entre",
    }
)  # fmt: skip
_SAME_SUBJECT: Final = 0.5


def _subject(statement: str, literals: list[str]) -> frozenset[str]:
    """What a statement is about: its words, without the quoted values, accents or plurals."""
    text = statement
    for literal in literals:
        text = text.replace(literal, " ")
    folded = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z]{2,}", folded)
    return frozenset(
        re.sub(r"(?<=[^aeiou])es$|s$", "", word) for word in words if word not in _FILLER
    )


def _same_values(rules: list[_Rule], groups: "_UnionFind") -> None:
    """B4: the same quoted values (two or more) about the same subject, in different files."""
    by_values: dict[frozenset[str], list[tuple[_Rule, frozenset[str]]]] = defaultdict(list)
    for rule in rules:
        literals = quoted_literals(rule.statement)
        if len(set(literals)) >= 2 and rule.citation is not None:
            by_values[frozenset(literals)].append((rule, _subject(rule.statement, literals)))
    for members in by_values.values():
        for index, (rule, subject) in enumerate(members):
            path = (rule.citation.locator or {}).get("path") if rule.citation else None
            for other, other_subject in members[index + 1 :]:
                other_path = (other.citation.locator or {}).get("path") if other.citation else None
                union = subject | other_subject
                if (
                    path != other_path
                    and union
                    and len(subject & other_subject) / len(union) >= _SAME_SUBJECT
                ):
                    groups.union(rule.entity_id, other.entity_id)


def _groups(rules: list[_Rule]) -> _UnionFind:
    groups = _UnionFind()
    sketches = {rule.entity_id: _statement_sketch(rule) for rule in rules}
    by_shape: dict[str, list[uuid.UUID]] = defaultdict(list)
    for rule in rules:
        if rule.shape:
            by_shape[rule.shape].append(rule.entity_id)
    for same in by_shape.values():
        # The same decision says the same thing: `x > 0` is also "shown if any" and "positive".
        for index, first in enumerate(same):
            for other in same[index + 1 :]:
                if similarity(sketches[first], sketches[other]) >= _SAME_DECISION:
                    groups.union(first, other)
    width = 32 // _BANDS * 8  # Hex characters per band.
    buckets: dict[tuple[int, str], list[uuid.UUID]] = defaultdict(list)
    for entity_id, sketch in sketches.items():
        for band in range(_BANDS):
            buckets[(band, sketch[band * width : (band + 1) * width])].append(entity_id)
    for members in buckets.values():
        for other in members[1:]:
            if groups.find(members[0]) != groups.find(other) and (
                similarity(sketches[members[0]], sketches[other]) >= _SIMILAR
            ):
                groups.union(members[0], other)
    _same_values(rules, groups)
    return groups


def _unanchored(session: Session, rule: _Rule) -> list[str]:
    """F2 for a model's proposal: its numbers and names that its code does not hold."""
    statement = rule.statement
    if rule.origin not in _GENERATED or statement.startswith(_PENDING_PREFIX):
        return []
    citation = rule.citation
    if citation is None or citation.source_revision_id is None:
        return []
    revision = session.get(SourceRevision, citation.source_revision_id)
    if revision is None or revision.text_content is None:
        return []
    locator = citation.locator or {}
    start = int(locator.get("start_line", 1))
    end = int(locator.get("end_line", start))
    path = str(locator.get("path", ""))
    decision = anchor_for(decisions_of(path, revision), start, end)
    lines = revision.text_content.splitlines()
    code = "\n".join(lines[start - 1 : end])
    missing = unanchored_terms(statement, decision.literals if decision else (), code)
    return missing + unanchored_literals(statement, decision_reach(lines, start, end))


def inbox(session: Session, project: Project, offset: int = 0, limit: int = 100) -> dict[str, Any]:
    rules = _rules(session, project)
    dependents = _dependents(session, project)
    cited_revisions = {
        rule.citation.source_revision_id
        for rule in rules
        if rule.citation is not None and rule.citation.source_revision_id is not None
    }
    churn = _churn(session, cited_revisions)
    # Phase 10.5: bugs filed against a rule make reviewing it worth more.
    bugs = {
        entity_id: len(tickets)
        for entity_id, tickets in bug_tickets_by_rule(session, project.id).items()
    }
    groups = _groups(rules)
    members: dict[uuid.UUID, list[_Rule]] = defaultdict(list)
    for rule in rules:
        members[groups.find(rule.entity_id)].append(rule)

    items: list[dict[str, Any]] = []
    unanchored: list[dict[str, Any]] = []
    for rule in rules:
        status = rule.status
        reason = rule.review_reason
        if status in _REVIEWED and reason is None:
            continue
        missing = _unanchored(session, rule)
        if missing:
            unanchored.append(
                {"entity_id": str(rule.entity_id), "title": rule.title, "missing": missing}
            )
            continue
        group = members[groups.find(rule.entity_id)]
        confirmed = any(
            other.status is VerificationStatus.HUMAN_VERIFIED
            for other in group
            if other is not rule
        )
        cited = rule.citation
        revision_id = cited.source_revision_id if cited else None
        impact = (
            1.0
            + dependents.get(rule.entity_id, 0)
            + math.log1p(churn.get(revision_id, 0) if revision_id else 0)
            + _CRITICALITY.get(rule.criticality, 2.0)
            + _BUG_WEIGHT * math.log1p(bugs.get(str(rule.entity_id), 0))
        )
        uncertainty = _UNCERTAINTY.get(reason or "", 1.0)
        if confirmed and reason is None:
            uncertainty = _GROUP_CONFIRMED
        locator = (cited.locator or {}) if cited else {}
        lines = (
            int(locator.get("end_line", 0)) - int(locator.get("start_line", 0)) + 1 if cited else 0
        )
        cost = 1.0 + max(lines, 0) / 20
        items.append(
            {
                "entity_id": str(rule.entity_id), "version_id": str(rule.version_id),
                "title": rule.title, "statement": rule.statement,
                "verification_status": status.value, "origin": rule.origin.value,
                "review_reason": reason, "criticality": rule.criticality.value,
                "score": round(impact * uncertainty / cost, 3),
                "parts": {
                    "impact": round(impact, 2), "uncertainty": uncertainty,
                    "cost": round(cost, 2), "bugs": bugs.get(str(rule.entity_id), 0),
                },
                "citation": None if cited is None else {
                    "revision_id": str(cited.source_revision_id),
                    "path": locator.get("path"), "start_line": locator.get("start_line"),
                    "end_line": locator.get("end_line"),
                },
                # Where its decision is now, when the code drifted from what was verified.
                "now": None,
                "group": None if len(group) < 2 else {
                    "key": str(groups.find(rule.entity_id)), "size": len(group),
                    "confirmed": confirmed,
                    "members": [str(other.entity_id) for other in group],
                },
            }
        )  # fmt: skip
    items.sort(key=lambda item: (-item["score"], item["title"], item["entity_id"]))
    buckets = {"attention": 0, "high": 0, "rest": 0}
    for item in items:
        buckets[bucket_of(item)] += 1
    page = items[offset : offset + limit]
    for item in page:
        if item["review_reason"] in _NOW_REASONS:
            from scrinium.application.knowledge.watch import current_places

            places = current_places(session, project.id, uuid.UUID(item["version_id"]))
            item["now"] = places[0] if places else None
    return {
        "total": len(items),
        "buckets": buckets,
        "items": page,
        "unanchored": unanchored[:50],
        "unanchored_total": len(unanchored),
    }


# The inbox's tabs (as Gmail's): what stopped being true, what matters most, the rest.
_ATTENTION: Final = frozenset(
    {"rule_changed", "code_changed", "code_removed", "code_contradicts", "contradiction",
     "source_deleted"}
)  # fmt: skip


def bucket_of(item: dict[str, Any]) -> str:
    """attention, high or rest: the same rule as the console's tabs."""
    if item["review_reason"] in _ATTENTION:
        return "attention"
    if item["criticality"] in ("high", "critical") or item["parts"].get("bugs", 0) > 0:
        return "high"
    return "rest"


_DRIFT: Final = frozenset({"rule_changed", "code_changed", "code_contradicts"})
_CONSISTENCY: Final = frozenset({"possible_duplicate", "contradiction", "ambiguous"})


# How far back a rule still counts as new on Home.
_NEW_DAYS: Final = 7


def health(session: Session, project: Project) -> dict[str, Any]:
    """The rulebook's health and freshness for the Home screen (phase 4.4), in aggregate
    queries: no work per rule, so it stays fast with thousands of rules."""
    from scrinium.infrastructure.sqlite.models import SourceConnection

    latest = (
        select(
            KnowledgeEntityVersion.knowledge_entity_id.label("entity_id"),
            func.max(KnowledgeEntityVersion.version_number).label("number"),
        )
        .where(KnowledgeEntityVersion.project_id == project.id)
        .group_by(KnowledgeEntityVersion.knowledge_entity_id)
        .subquery()
    )
    live = (
        KnowledgeEntity.project_id == project.id,
        KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
        KnowledgeEntity.lifecycle_status.not_in(
            [EntityLifecycle.RETIRED, EntityLifecycle.DEPRECATED]
        ),
    )
    rows = session.execute(
        select(
            KnowledgeEntityVersion.verification_status,
            KnowledgeEntity.review_reason,
            func.count(),
            func.min(KnowledgeEntityVersion.created_at),
        )
        .join(latest, latest.c.entity_id == KnowledgeEntity.id)
        .join(
            KnowledgeEntityVersion,
            (KnowledgeEntityVersion.knowledge_entity_id == KnowledgeEntity.id)
            & (KnowledgeEntityVersion.version_number == latest.c.number),
        )
        .where(*live)
        .group_by(KnowledgeEntityVersion.verification_status, KnowledgeEntity.review_reason)
    ).all()
    counts: dict[str, int] = defaultdict(int)
    oldest_unreviewed = None
    for status, reason, count, oldest in rows:
        counts["rules"] += count
        reviewed = status in _REVIEWED
        counts["verified" if status is VerificationStatus.HUMAN_VERIFIED else (
            "rejected" if status is VerificationStatus.REJECTED else "unreviewed"
        )] += count  # fmt: skip
        if reason in _DRIFT:
            counts["drifted"] += count
        elif reason == "code_removed":
            counts["removed"] += count
        elif reason in _CONSISTENCY:
            counts["consistency"] += count
        if not reviewed or reason is not None:
            counts["inbox"] += count
        if not reviewed and oldest is not None:
            oldest_unreviewed = (
                oldest if oldest_unreviewed is None else min(oldest_unreviewed, oldest)
            )
    last_review = session.scalar(
        select(func.max(KnowledgeEntityVersion.reviewed_at)).where(
            KnowledgeEntityVersion.project_id == project.id
        )
    )
    last_sync = session.scalar(
        select(func.max(SourceConnection.last_successful_sync_at)).where(
            SourceConnection.project_id == project.id
        )
    )
    # Rules first proposed in the last week, still live: Home's "N nuevas".
    new_this_week = session.scalar(
        select(func.count(KnowledgeEntity.id)).where(
            *live, KnowledgeEntity.created_at >= utc_now() - timedelta(days=_NEW_DAYS)
        )
    )
    # Rules a person reviewed in the same week: the pace to set against the new ones.
    reviewed_this_week = session.scalar(
        select(func.count(func.distinct(KnowledgeEntityVersion.knowledge_entity_id))).where(
            KnowledgeEntityVersion.project_id == project.id,
            KnowledgeEntityVersion.reviewed_at >= utc_now() - timedelta(days=_NEW_DAYS),
        )
    )

    def iso(moment: Any) -> str | None:
        return moment.isoformat() if moment is not None else None

    return {
        **{
            key: counts.get(key, 0)
            for key in (
                "rules",
                "verified",
                "rejected",
                "unreviewed",
                "drifted",
                "removed",
                "consistency",
                "inbox",
            )
        },
        "new_this_week": new_this_week or 0,
        "reviewed_this_week": reviewed_this_week or 0,
        "last_review_at": iso(last_review),
        "oldest_unreviewed_at": iso(oldest_unreviewed),
        "last_sync_at": iso(last_sync),
    }
