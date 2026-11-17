"""The verified rulebook of one source, as rule files (`.scrinium/rules/`, ADR-012).

Only what a person verified leaves Scrinium: for each rule, its latest human-verified version,
with the anchors of its latest citation in each file of the source. A version proposed after it
(by the model, after a code change) is not exported until a person verifies it: the file keeps
saying what was verified, and `scrinium check` keeps measuring drift from there.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.check.rule_files import FileAnchor, RuleFile
from scrinium.application.knowledge.evidence import anchored_locator
from scrinium.application.knowledge.files import source_of
from scrinium.application.knowledge.watch import latest_citations
from scrinium.domain.enums import EntityLifecycle, EntityType, VerificationStatus
from scrinium.infrastructure.decisions import DECISIONS_VERSION
from scrinium.infrastructure.sqlite.models import (
    BusinessRuleVersion,
    KnowledgeEntity,
    KnowledgeEntityVersion,
)


def _timestamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _anchors(
    session: Session, version_id: uuid.UUID, source_id: uuid.UUID
) -> tuple[bool, list[FileAnchor]]:
    """(whether the version cites this source, its anchors in it)."""
    cited = False
    anchors = []
    for evidence in latest_citations(session, version_id):
        if evidence.source_revision_id is None:
            continue
        locator = anchored_locator(session, evidence)
        owner = locator.get("source_id") or source_of(session, evidence.source_revision_id)
        if str(owner) != str(source_id):
            continue
        cited = True
        anchor = locator.get("anchor")
        if not isinstance(anchor, dict) or not anchor.get("shape"):
            continue
        if str(anchor.get("version", DECISIONS_VERSION)) != DECISIONS_VERSION:
            continue
        anchors.append(
            FileAnchor(
                path=str(locator.get("path", "")),
                symbol=anchor.get("symbol"),
                kind=str(anchor.get("kind", "")),
                start_line=int(anchor.get("start_line") or 1),
                end_line=int(anchor.get("end_line") or anchor.get("start_line") or 1),
                shape=str(anchor["shape"]),
                value=str(anchor.get("value", "")),
                sketch=str(anchor.get("sketch", "")),
                decisions=DECISIONS_VERSION,
            )
        )
    unique = {(a.path, a.start_line, a.end_line, a.shape): a for a in anchors}
    return cited, list(unique.values())


def verified_rules(session: Session, project_id: uuid.UUID, source_id: uuid.UUID) -> list[RuleFile]:
    """The verified rules cited in this source, in id order."""
    rules = []
    entities = session.scalars(
        select(KnowledgeEntity)
        .where(
            KnowledgeEntity.project_id == project_id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
            KnowledgeEntity.lifecycle_status.not_in(
                [EntityLifecycle.RETIRED, EntityLifecycle.DEPRECATED]
            ),
        )
        .order_by(KnowledgeEntity.id)
    )
    for entity in entities:
        version = session.scalars(
            select(KnowledgeEntityVersion)
            .where(
                KnowledgeEntityVersion.knowledge_entity_id == entity.id,
                KnowledgeEntityVersion.verification_status == VerificationStatus.HUMAN_VERIFIED,
            )
            .order_by(KnowledgeEntityVersion.version_number.desc())
            .limit(1)
        ).first()
        if version is None:
            continue
        content = session.get(BusinessRuleVersion, version.id)
        if content is None:
            continue
        cited, anchors = _anchors(session, version.id, source_id)
        if not cited:
            continue
        rules.append(
            RuleFile(
                id=str(entity.id),
                title=content.title,
                statement=content.statement,
                verified_by=version.reviewed_by,
                verified_at=_timestamp(version.reviewed_at),
                anchors=tuple(anchors),
            )
        )
    return rules
