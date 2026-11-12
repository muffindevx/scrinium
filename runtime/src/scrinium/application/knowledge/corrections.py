"""New versions of a rule that keep its citations: a corrected statement, a written one."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.common import KnowledgeNotFoundError
from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import (
    Provenance,
    add_rule_version,
    latest_version,
    set_verification,
)
from scrinium.application.knowledge.evidence import cite_source
from scrinium.domain.enums import (
    EvidenceRole,
    VerificationMethod,
    VerificationStatus,
    VersionOrigin,
)
from scrinium.infrastructure.sqlite.models import BusinessRuleVersion, EvidenceRecord, Project


def copy_citations(
    session: Session,
    project: Project,
    entity_id: uuid.UUID,
    from_version_id: uuid.UUID,
    to_version_id: uuid.UUID,
    method: VerificationMethod | None = None,
) -> int:
    """The previous version's citations, on the new one: rule watch and the rule screen look at
    those of the current version. The evidence count counts distinct citations, not copies.
    `method` replaces the one each citation had; by default it is kept."""
    copied = 0
    for record in session.scalars(
        select(EvidenceRecord).where(EvidenceRecord.target_version_id == from_version_id)
    ):
        locator = record.locator or {}
        if record.source_revision_id is None or "start_line" not in locator:
            continue
        cite_source(
            session, project, entity_id=entity_id, revision_id=record.source_revision_id,
            start_line=locator["start_line"], end_line=locator["end_line"],
            role=EvidenceRole(record.evidence_role), evidence_type=record.evidence_type,
            method=method or record.verification_method, version_id=to_version_id,
        )  # fmt: skip
        copied += 1
    return copied


def correct_rule(
    session: Session,
    project: Project,
    entity_id: uuid.UUID,
    *,
    statement: str,
    title: str | None,
    author: str,
    request_id: str | None = None,
) -> uuid.UUID:
    """A person rewrites a rule that was real but badly worded: a new version with their text,
    the same citations, and confirmed by them. Returns the new version's id."""
    current = latest_version(session, entity_id)
    if current is None:
        raise KnowledgeNotFoundError
    previous = session.get(BusinessRuleVersion, current.id)
    if previous is None:
        raise KnowledgeNotFoundError
    content = RuleContent.model_validate(
        {
            "title": (title or previous.title).strip(),
            "statement": statement.strip(),
            "rationale": previous.rationale,
            "scope": previous.scope,
        }
    )
    version, created = add_rule_version(
        session, project, entity_id, content,
        Provenance(VersionOrigin.HUMAN, created_by=author), request_id,
    )  # fmt: skip
    version_id = version.knowledge_entity_version_id
    if created:
        copy_citations(session, project, entity_id, current.id, version_id)
    set_verification(
        session, project, version_id, VerificationStatus.HUMAN_VERIFIED,
        reviewer=author, request_id=request_id,
    )  # fmt: skip
    return version_id
