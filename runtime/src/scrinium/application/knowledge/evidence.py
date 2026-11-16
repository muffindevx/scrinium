"""Evidence, relationships between entities, and findings.

A piece of evidence points to lines that exist in a revision of the same project; the excerpt
is copied from those lines, not from what the proposer says. So a citation invented by the LLM
is never stored, and a real citation stays `unverified` if the LLM proposed it.
"""

import hashlib
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any, Final

from sqlalchemy import exists, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scrinium.application.knowledge.common import (
    KnowledgeError,
    KnowledgeNotFoundError,
    audit,
    get_in_project,
)
from scrinium.core.secret_patterns import redact_tokens
from scrinium.domain.enums import (
    AssessmentStatus,
    EntityType,
    EvidenceRelevance,
    EvidenceRole,
    EvidenceType,
    FindingType,
    RelationType,
    Severity,
    VerificationMethod,
    VerificationStatus,
)
from scrinium.infrastructure.decisions import (
    DECISIONS_VERSION,
    Decision,
    anchor_for,
    parse_decisions,
)
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    BusinessRuleVersion,
    EntityRelationship,
    EvidenceRecord,
    Finding,
    FindingEvidence,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceConnection,
    SourceItem,
    SourceRevision,
    utc_now,
)

MAX_EVIDENCE_LINES: Final = 200
MAX_EXCERPT_CHARS: Final = 2000
MAX_NOTE_CHARS: Final = 2000

# Status each method gives. The code checked that the lines exist; whether they back the
# claim only whoever chose them knows, and the LLM does not count.
_METHOD_STATUS: Final = {
    VerificationMethod.DIRECT_REFERENCE: VerificationStatus.SOURCE_SUPPORTED,
    VerificationMethod.STATIC_ANALYSIS: VerificationStatus.SOURCE_SUPPORTED,
    VerificationMethod.TEST_RESULT: VerificationStatus.TEST_SUPPORTED,
    VerificationMethod.LLM_PROPOSAL: VerificationStatus.UNVERIFIED,
}

# Relationships that allow no cycles: A depends on B depends on A describes nothing real.
ACYCLIC_RELATIONS: Final = frozenset(
    {
        RelationType.DEPENDS_ON,
        RelationType.SUPERSEDES,
        RelationType.IMPLEMENTED_BY,
        RelationType.TESTED_BY,
        RelationType.IMPLEMENTS_REQUIREMENT,
    }
)
# Undirected: A contradicts B is the same as B contradicts A.
SYMMETRIC_RELATIONS: Final = frozenset({RelationType.CONTRADICTS, RelationType.RELATED_TO})
# The entity type some relationships accept as their target.
_TARGET_TYPES: Final = {
    RelationType.TESTED_BY: {EntityType.TEST_CASE},
    RelationType.IMPLEMENTS_REQUIREMENT: {EntityType.REQUIREMENT},
    RelationType.PUBLISHES: {EntityType.EVENT},
    RelationType.SUBSCRIBES: {EntityType.EVENT},
}

_SUPPORTING_FOR_FINDINGS: Final = frozenset(
    {
        AssessmentStatus.EVIDENCE_SUPPORTED,
        AssessmentStatus.TEST_VERIFIED,
    }
)
_REVIEWED_FINDINGS: Final = frozenset(
    {AssessmentStatus.HUMAN_CONFIRMED, AssessmentStatus.FALSE_POSITIVE}
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# Decisions per file content (path, content hash): a revision is parsed once, however many
# rules cite it. Bounded, since a long extraction touches many files.
_DECISIONS_CACHE: dict[tuple[str, str], list[Decision]] = {}
_DECISIONS_CACHE_SIZE: Final = 256


def decisions_of(path: str, revision: SourceRevision) -> list[Decision]:
    key = (path, revision.content_hash)
    found = _DECISIONS_CACHE.get(key)
    if found is None:
        found = parse_decisions(path, revision.text_content or "")
        if len(_DECISIONS_CACHE) >= _DECISIONS_CACHE_SIZE:
            _DECISIONS_CACHE.pop(next(iter(_DECISIONS_CACHE)))
        _DECISIONS_CACHE[key] = found
    return found


def anchor_of(path: str, revision: SourceRevision, start: int, end: int) -> dict[str, Any] | None:
    """The decision node a citation of lines `start`-`end` rests on (ADR-011), as stored in the
    evidence locator; None when the file has no decisions there or its language has no support."""
    decision = anchor_for(decisions_of(path, revision), start, end)
    if decision is None:
        return None
    return {
        "version": DECISIONS_VERSION,
        "kind": decision.kind,
        "symbol": decision.symbol,
        "start_line": decision.start_line,
        "end_line": decision.end_line,
        "shape": decision.shape,
        "value": decision.value,
        "sketch": decision.sketch,
    }


def _revision_in_project(
    session: Session, project: Project, revision_id: uuid.UUID
) -> SourceRevision:
    query = (
        select(SourceRevision)
        .join(SourceItem, SourceItem.id == SourceRevision.source_item_id)
        .join(SourceConnection, SourceConnection.id == SourceItem.source_connection_id)
        .where(SourceRevision.id == revision_id, SourceConnection.project_id == project.id)
    )
    revision = session.scalars(query).first()
    if revision is None:
        raise KnowledgeNotFoundError
    return revision


def _target(
    session: Session, project: Project, entity_id: uuid.UUID, version_id: uuid.UUID | None
) -> None:
    get_in_project(session, KnowledgeEntity, project, entity_id)
    if version_id is not None:
        version = get_in_project(session, KnowledgeEntityVersion, project, version_id)
        if version.knowledge_entity_id != entity_id:
            raise KnowledgeError("version_of_another_entity")


def cite_source(
    session: Session,
    project: Project,
    *,
    entity_id: uuid.UUID,
    revision_id: uuid.UUID,
    start_line: int,
    end_line: int,
    role: EvidenceRole,
    evidence_type: EvidenceType,
    method: VerificationMethod,
    version_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> tuple[EvidenceRecord, bool]:
    """Evidence on lines of a revision. Returns the evidence and whether it is new."""
    if method not in _METHOD_STATUS:
        raise KnowledgeError("method_not_allowed")
    if method is VerificationMethod.TEST_RESULT and evidence_type is not EvidenceType.TEST:
        raise KnowledgeError("test_result_needs_test")
    if evidence_type is EvidenceType.HUMAN_REVIEW:
        raise KnowledgeError("method_not_allowed")
    _target(session, project, entity_id, version_id)
    revision = _revision_in_project(session, project, revision_id)
    if revision.text_content is None:
        raise KnowledgeError("revision_unreadable")
    lines = revision.text_content.splitlines()
    if not 1 <= start_line <= end_line <= len(lines):
        raise KnowledgeError("lines_out_of_range")
    if end_line - start_line + 1 > MAX_EVIDENCE_LINES:
        raise KnowledgeError("too_many_lines")

    duplicate = session.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.target_entity_id == entity_id,
            EvidenceRecord.target_version_id.is_(None)
            if version_id is None
            else EvidenceRecord.target_version_id == version_id,
            EvidenceRecord.source_revision_id == revision.id,
            EvidenceRecord.evidence_role == role,
            EvidenceRecord.verification_method == method,
            EvidenceRecord.locator["start_line"].as_integer() == start_line,
            EvidenceRecord.locator["end_line"].as_integer() == end_line,
        )
    ).first()
    if duplicate is not None:
        return duplicate, False

    excerpt = redact_tokens("\n".join(lines[start_line - 1 : end_line]))[:MAX_EXCERPT_CHARS]
    path = revision.metadata_.get("path", "")
    item = session.get(SourceItem, revision.source_item_id)
    locator: dict[str, Any] = {
        # (source, path) identifies a file: two sources may both have `src/utils.ts`.
        "source_id": str(item.source_connection_id) if item is not None else None,
        "path": path,
        "commit": revision.metadata_.get("commit"),
        "revision_key": revision.revision_key,
        "start_line": start_line,
        "end_line": end_line,
    }
    anchor = anchor_of(path, revision, start_line, end_line)
    if anchor is not None:
        locator["anchor"] = anchor
    record = EvidenceRecord(
        workspace_id=project.workspace_id,
        project_id=project.id,
        target_entity_id=entity_id,
        target_version_id=version_id,
        source_revision_id=revision.id,
        evidence_role=role,
        evidence_type=evidence_type,
        locator=locator,
        excerpt=excerpt,
        excerpt_hash=_sha256(excerpt),
        verification_method=method,
        verification_status=_METHOD_STATUS[method],
    )
    session.add(record)
    session.flush()
    audit(
        session, project, "evidence.create", "evidence_record", record.id, request_id,
        {"entity_id": str(entity_id), "role": role.value, "method": method.value},
    )  # fmt: skip
    return record, True


def record_human_review(
    session: Session,
    project: Project,
    *,
    entity_id: uuid.UUID,
    reviewer: str,
    note: str,
    role: EvidenceRole,
    version_id: uuid.UUID | None = None,
    request_id: str | None = None,
) -> EvidenceRecord:
    """What a person states after reviewing, without a source line to back it."""
    reviewer, note = reviewer.strip()[:200], redact_tokens(note.strip())[:MAX_NOTE_CHARS]
    if not reviewer:
        raise KnowledgeError("reviewer_required")
    if not note:
        raise KnowledgeError("note_required")
    _target(session, project, entity_id, version_id)
    record = EvidenceRecord(
        workspace_id=project.workspace_id,
        project_id=project.id,
        target_entity_id=entity_id,
        target_version_id=version_id,
        evidence_role=role,
        evidence_type=EvidenceType.HUMAN_REVIEW,
        locator={"reviewer": reviewer},
        excerpt=note,
        excerpt_hash=_sha256(note),
        verification_method=VerificationMethod.HUMAN_REVIEW,
        verification_status=VerificationStatus.HUMAN_VERIFIED,
    )
    session.add(record)
    session.flush()
    audit(
        session, project, "evidence.create", "evidence_record", record.id, request_id,
        {"entity_id": str(entity_id), "role": role.value, "method": "human_review"},
    )  # fmt: skip
    return record


def evidence_profile(session: Session, version_id: uuid.UUID) -> dict[str, int]:
    """How much evidence there is of each kind of behaviour, without mixing them.

    Requested (a ticket), documented, implemented and tested are different things: a ticket
    asking for it does not mean the code does it, nor does the code doing it mean it is tested.
    """
    profile = {"requested": 0, "documented": 0, "implemented": 0, "tested": 0, "contradicted": 0}
    records = session.scalars(
        select(EvidenceRecord).where(EvidenceRecord.target_version_id == version_id)
    )
    for record in records:
        role, kind = record.evidence_role, record.evidence_type
        if role is EvidenceRole.CONTRADICTS:
            profile["contradicted"] += 1
        elif role is EvidenceRole.REQUESTS or kind is EvidenceType.TICKET:
            profile["requested"] += 1
        elif role is EvidenceRole.TESTS or kind is EvidenceType.TEST:
            profile["tested"] += 1
        elif role is EvidenceRole.IMPLEMENTS or kind in (EvidenceType.CODE, EvidenceType.SCHEMA):
            profile["implemented"] += 1
        elif kind is EvidenceType.DOCUMENT:
            profile["documented"] += 1
    return profile


def _reaches(
    session: Session, start: uuid.UUID, target: uuid.UUID, relation_type: RelationType
) -> bool:
    """Whether `target` is reachable from `start` following relationships of that type."""
    edges = EntityRelationship
    reach = (
        select(edges.to_entity_id.label("node"))
        .where(edges.from_entity_id == start, edges.relation_type == relation_type)
        .cte("reach", recursive=True)
    )
    # UNION, not UNION ALL: drops repeated nodes and terminates even if a cycle already existed.
    reach = reach.union(
        select(edges.to_entity_id)
        .join(reach, edges.from_entity_id == reach.c.node)
        .where(edges.relation_type == relation_type)
    )
    return session.scalar(select(exists().where(reach.c.node == target))) or False


def add_relationship(
    session: Session,
    project: Project,
    from_entity_id: uuid.UUID,
    relation_type: RelationType,
    to_entity_id: uuid.UUID,
    *,
    evidence_id: uuid.UUID | None = None,
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    created_by: str | None = None,
    request_id: str | None = None,
) -> tuple[EntityRelationship, bool]:
    if from_entity_id == to_entity_id:
        raise KnowledgeError("self_relation")
    source = get_in_project(session, KnowledgeEntity, project, from_entity_id)
    target = get_in_project(session, KnowledgeEntity, project, to_entity_id)
    allowed = _TARGET_TYPES.get(relation_type)
    if allowed is not None and target.entity_type not in allowed:
        raise KnowledgeError("relation_not_allowed")
    if relation_type is RelationType.SUPERSEDES and source.entity_type != target.entity_type:
        raise KnowledgeError("relation_not_allowed")
    if valid_from and valid_until and valid_until <= valid_from:
        raise KnowledgeError("invalid_validity")
    if evidence_id is not None:
        evidence = get_in_project(session, EvidenceRecord, project, evidence_id)
        if evidence.target_entity_id not in (from_entity_id, to_entity_id):
            raise KnowledgeError("evidence_of_another_entity")
    if relation_type in SYMMETRIC_RELATIONS and str(from_entity_id) > str(to_entity_id):
        from_entity_id, to_entity_id = to_entity_id, from_entity_id

    existing = session.scalars(
        select(EntityRelationship).where(
            EntityRelationship.from_entity_id == from_entity_id,
            EntityRelationship.relation_type == relation_type,
            EntityRelationship.to_entity_id == to_entity_id,
        )
    ).first()
    if existing is not None:
        return existing, False
    if relation_type in ACYCLIC_RELATIONS and _reaches(
        session, to_entity_id, from_entity_id, relation_type
    ):
        raise KnowledgeError("relation_cycle")
    relationship = EntityRelationship(
        workspace_id=project.workspace_id,
        project_id=project.id,
        from_entity_id=from_entity_id,
        relation_type=relation_type,
        to_entity_id=to_entity_id,
        evidence_record_id=evidence_id,
        valid_from=valid_from,
        valid_until=valid_until,
        created_by=created_by,
    )
    session.add(relationship)
    try:
        session.flush()
    except IntegrityError as error:
        raise KnowledgeError("relation_exists") from error
    audit(
        session, project, "relationship.create", "entity_relationship", relationship.id,
        request_id, {"relation_type": relation_type.value},
    )  # fmt: skip
    return relationship, True


def relationships_of(session: Session, entity_id: uuid.UUID) -> list[EntityRelationship]:
    return list(
        session.scalars(
            select(EntityRelationship)
            .where(
                or_(
                    EntityRelationship.from_entity_id == entity_id,
                    EntityRelationship.to_entity_id == entity_id,
                )
            )
            .order_by(EntityRelationship.created_at, EntityRelationship.id)
        )
    )


def record_finding(
    session: Session,
    run: AnalysisRun,
    *,
    finding_type: FindingType,
    title: str,
    description: str,
    severity: Severity,
    location: dict[str, Any] | None = None,
    next_step: str | None = None,
    rule_version_id: uuid.UUID | None = None,
) -> Finding:
    """It is always born as a hypothesis, even if the model says it is sure."""
    title, description = redact_tokens(title.strip())[:300], redact_tokens(description.strip())
    if not title or not description:
        raise KnowledgeError("invalid_finding")
    if rule_version_id is not None:
        rule = session.get(BusinessRuleVersion, rule_version_id)
        if rule is None or rule.project_id != run.project_id:
            raise KnowledgeNotFoundError
    finding = Finding(
        workspace_id=run.workspace_id,
        project_id=run.project_id,
        analysis_run_id=run.id,
        finding_type=finding_type,
        title=title,
        description=description[:4000],
        severity=severity,
        location=location,
        recommended_next_step=redact_tokens(next_step)[:1000] if next_step else None,
        rule_version_id=rule_version_id,
    )
    session.add(finding)
    session.flush()
    return finding


def link_finding_evidence(
    session: Session,
    project: Project,
    finding_id: uuid.UUID,
    evidence_id: uuid.UUID,
    relevance: EvidenceRelevance,
) -> None:
    get_in_project(session, Finding, project, finding_id)
    get_in_project(session, EvidenceRecord, project, evidence_id)
    if session.get(FindingEvidence, (finding_id, evidence_id)) is not None:
        return
    session.add(
        FindingEvidence(
            workspace_id=project.workspace_id,
            project_id=project.id,
            finding_id=finding_id,
            evidence_record_id=evidence_id,
            relevance=relevance,
        )
    )
    session.flush()


def finding_meets(session: Session, finding: Finding, status: AssessmentStatus) -> bool:
    if status not in _SUPPORTING_FOR_FINDINGS:
        return True
    methods = (
        (VerificationMethod.TEST_RESULT,)
        if status is AssessmentStatus.TEST_VERIFIED
        else (
            VerificationMethod.DIRECT_REFERENCE,
            VerificationMethod.STATIC_ANALYSIS,
            VerificationMethod.TEST_RESULT,
        )
    )
    query = select(
        exists()
        .where(
            FindingEvidence.finding_id == finding.id,
            FindingEvidence.relevance == EvidenceRelevance.SUPPORTS,
        )
        .where(
            EvidenceRecord.id == FindingEvidence.evidence_record_id,
            EvidenceRecord.verification_method.in_(methods),
        )
    )
    return bool(session.scalar(query))


def assess_finding(
    session: Session,
    project: Project,
    finding_id: uuid.UUID,
    status: AssessmentStatus,
    *,
    reviewer: str | None = None,
    request_id: str | None = None,
) -> Finding:
    """Changes the assessment. Contradicting evidence is kept, whether confirmed or not."""
    finding = get_in_project(session, Finding, project, finding_id)
    reviewer = (reviewer or "").strip()[:200] or None
    if status in _REVIEWED_FINDINGS and reviewer is None:
        raise KnowledgeError("reviewer_required")
    if not finding_meets(session, finding, status):
        raise KnowledgeError("insufficient_evidence")
    previous = finding.assessment_status
    finding.assessment_status = status
    if reviewer is not None:
        finding.reviewed_by, finding.reviewed_at = reviewer, utc_now()
    session.flush()
    audit(
        session, project, "finding.assess", "finding", finding.id, request_id,
        {"from": previous.value, "to": status.value},
    )  # fmt: skip
    return finding


def revalidate_findings(session: Session, finding_ids: Iterable[uuid.UUID]) -> int:
    downgraded = 0
    for finding_id in set(finding_ids):
        finding = session.get(Finding, finding_id)
        if finding is None or finding_meets(session, finding, finding.assessment_status):
            continue
        finding.assessment_status = AssessmentStatus.HYPOTHESIS
        downgraded += 1
    session.flush()
    return downgraded
