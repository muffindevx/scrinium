"""Separates rules that were merged because a model titled them alike (ADR-011, phase 1.6).

Before anchors, extraction matched rules by title: a decision in `worker/config.ts` and another
in `db/client.ts`, both titled "Requisito de URL de base de datos", became one rule, and the
later version cited only the second file. The first decision was left without a rule of its own.

For each live rule, every place an earlier version cited (file and decision) that its latest
version no longer cites, whose decision is still in the code and that no other rule rests on,
becomes a rule of its own: unverified, with the text of the version that cited it, cited at its
current lines. The original rule keeps its place, its versions and its verification. Running it
twice changes nothing.
"""

import uuid
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
from scrinium.application.knowledge.common import KnowledgeError, audit
from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import (
    Provenance,
    create_business_rule,
    free_rule_key,
    latest_version,
)
from scrinium.application.knowledge.evidence import anchored_locator, cite_source, decisions_of
from scrinium.application.knowledge.files import file_key
from scrinium.domain.enums import (
    EntityLifecycle,
    EntityType,
    EvidenceRole,
    ItemLifecycle,
    VerificationMethod,
    VersionOrigin,
)
from scrinium.infrastructure.sqlite.models import (
    BusinessRule,
    BusinessRuleVersion,
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceItem,
    SourceRevision,
)

SPLIT_EXTRACTOR_VERSION: Final = "rules-split-1"
_SUPPORTING = (EvidenceRole.SUPPORTS, EvidenceRole.IMPLEMENTS)
_GONE = (EntityLifecycle.RETIRED, EntityLifecycle.DEPRECATED)


def _place(locator: dict[str, Any]) -> tuple[str, str] | None:
    anchor = locator.get("anchor")
    if not isinstance(anchor, dict) or not anchor.get("shape"):
        return None
    return file_key(locator.get("source_id"), str(locator.get("path", ""))), str(anchor["shape"])


def _supporting(session: Session, entity_id: uuid.UUID) -> list[EvidenceRecord]:
    return list(
        session.scalars(
            select(EvidenceRecord)
            .where(
                EvidenceRecord.target_entity_id == entity_id,
                EvidenceRecord.evidence_role.in_(_SUPPORTING),
                EvidenceRecord.source_revision_id.is_not(None),
            )
            .order_by(EvidenceRecord.created_at)
        )
    )


def split_title_merges(session: Session, project: Project) -> list[uuid.UUID]:
    """Creates a rule for each orphaned place. Returns the new rules' ids."""
    entities = list(
        session.scalars(
            select(KnowledgeEntity).where(
                KnowledgeEntity.project_id == project.id,
                KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
                KnowledgeEntity.lifecycle_status.not_in(_GONE),
            )
        )
    )
    # Every place some live rule's latest version rests on: those are taken.
    citations: dict[uuid.UUID, list[tuple[EvidenceRecord, dict[str, Any]]]] = {}
    taken: set[tuple[str, str]] = set()
    for entity in entities:
        latest = latest_version(session, entity.id)
        rows = [(e, anchored_locator(session, e)) for e in _supporting(session, entity.id)]
        citations[entity.id] = rows
        for evidence, locator in rows:
            place = _place(locator)
            if place is not None and latest is not None and evidence.target_version_id == latest.id:
                taken.add(place)
    created: list[uuid.UUID] = []
    for entity in entities:
        for evidence, locator in citations[entity.id]:
            place = _place(locator)
            if place is None or place in taken or evidence.target_version_id is None:
                continue
            new_id = _split_off(session, project, entity, evidence, locator, place)
            taken.add(place)
            if new_id is not None:
                created.append(new_id)
    session.flush()
    return created


def _split_off(
    session: Session,
    project: Project,
    entity: KnowledgeEntity,
    evidence: EvidenceRecord,
    locator: dict[str, Any],
    place: tuple[str, str],
) -> uuid.UUID | None:
    cited = session.get(SourceRevision, evidence.source_revision_id)
    item = session.get(SourceItem, cited.source_item_id) if cited is not None else None
    if item is None or item.lifecycle_status is not ItemLifecycle.ACTIVE:
        return None
    current = session.get(SourceRevision, item.current_revision_id)
    anchor = OldAnchor.from_locator("old", {**locator, "path": place[0]})
    if current is None or anchor is None:
        return None
    path = str(locator.get("path", ""))
    match = reanchor([anchor], {place[0]: decisions_of(path, current)}).get("old")
    if match is None:
        return None  # The decision is gone: there is nothing left to give a rule.
    version = session.get(KnowledgeEntityVersion, evidence.target_version_id)
    text = session.get(BusinessRuleVersion, evidence.target_version_id)
    rule = session.scalars(
        select(BusinessRule).where(BusinessRule.knowledge_entity_id == entity.id)
    ).first()
    if version is None or text is None or rule is None:
        return None
    content = RuleContent.model_validate(
        {
            "title": text.title, "statement": text.statement, "rationale": text.rationale,
            "scope": text.scope, "preconditions": text.preconditions,
            "trigger_conditions": text.trigger_conditions,
            "expected_behavior": text.expected_behavior, "exceptions": text.exceptions,
            "impact_targets": text.impact_targets,
        }
    )  # fmt: skip
    provenance = Provenance(
        VersionOrigin.SOURCE_EXTRACTION, version.model_provider, version.model_name,
        extractor_version=SPLIT_EXTRACTOR_VERSION, analysis_run_id=text.created_from_analysis_id,
    )  # fmt: skip
    try:
        created = create_business_rule(
            session, project, canonical_key=free_rule_key(session, project, entity.canonical_key),
            category=rule.rule_category, criticality=rule.criticality, content=content,
            provenance=provenance,
        )  # fmt: skip
        new_entity = session.get_one(
            KnowledgeEntityVersion, created.knowledge_entity_version_id
        ).knowledge_entity_id
        cite_source(
            session, project, entity_id=new_entity, revision_id=current.id,
            start_line=match.decision.start_line, end_line=match.decision.end_line,
            role=EvidenceRole.SUPPORTS, evidence_type=evidence.evidence_type,
            method=VerificationMethod.LLM_PROPOSAL,
            version_id=created.knowledge_entity_version_id,
        )  # fmt: skip
    except KnowledgeError:
        return None
    audit(
        session, project, "rule.split_title_merge", "knowledge_entity", new_entity, None,
        {"from_entity_id": str(entity.id)},
    )  # fmt: skip
    return new_entity
