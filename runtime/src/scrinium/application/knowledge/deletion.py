"""Deletion that propagates to knowledge.

Deleting a source removes the evidence citing its revisions, with its excerpts.
Then, for each entity that had evidence there:

- If the LLM or an analysis proposed it, no person reviewed it, and all its evidence came from
  that source, it is deleted: its text paraphrases the deleted source.
- Otherwise it is kept and flagged for review (`review_reason`). Whatever relied only on the
  source goes back to `unverified` or `hypothesis`: it cannot keep showing as supported.

Deleting a project or the workspace deletes all its knowledge, without distinction.
"""

import json
import re
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, delete, exists, or_, select, update
from sqlalchemy.orm import Session

from scrinium.application.knowledge.entities import revalidate_versions
from scrinium.application.knowledge.evidence import revalidate_findings
from scrinium.domain.enums import EntityType, EvidenceType, VersionOrigin
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    BusinessRule,
    BusinessRuleVersion,
    EntityRelationship,
    EvidenceRecord,
    Finding,
    FindingEvidence,
    FlowDefinition,
    FlowStep,
    FlowTransition,
    FlowVersion,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    ReleaseKnowledgeSnapshot,
    ReleaseSnapshotEntry,
    SoftwareRelease,
    TicketLink,
)

REVIEW_SOURCE_DELETED: Final = "source_deleted"
_GENERATED: Final = (VersionOrigin.SOURCE_EXTRACTION, VersionOrigin.DERIVED_ANALYSIS)
_UUID: Final = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@dataclass(frozen=True)
class EntityFate:
    delete: set[uuid.UUID]
    review: set[uuid.UUID]
    # Of `review`: rules a person wrote or reviewed whose only evidence is in these revisions.
    reviewed_here: frozenset[uuid.UUID] = frozenset()


def _without_human_involvement(session: Session, entity_id: uuid.UUID) -> bool:
    """Neither written nor reviewed by a person, and not part of a flow."""
    entity = session.get(KnowledgeEntity, entity_id)
    if entity is None or entity.entity_type is not EntityType.BUSINESS_RULE:
        return False
    human_version = select(
        exists().where(
            KnowledgeEntityVersion.knowledge_entity_id == entity_id,
            or_(
                KnowledgeEntityVersion.origin.not_in(_GENERATED),
                KnowledgeEntityVersion.reviewed_at.is_not(None),
            ),
        )
    )
    human_evidence = select(
        exists().where(
            EvidenceRecord.target_entity_id == entity_id,
            EvidenceRecord.evidence_type == EvidenceType.HUMAN_REVIEW,
        )
    )
    in_flow = select(exists().where(FlowStep.knowledge_entity_id == entity_id))
    return not (
        session.scalar(human_version) or session.scalar(human_evidence) or session.scalar(in_flow)
    )


def entity_fate(
    session: Session, revision_ids: Select[uuid.UUID], delete_reviewed: bool = False
) -> EntityFate:
    """Which entities would be deleted, and which flagged, if those revisions disappear.

    Rules backed elsewhere too are always kept, flagged. A rule backed only here goes with
    them if no person wrote or reviewed it; with `delete_reviewed`, even if one did."""
    cited = select(EvidenceRecord.target_entity_id).where(
        EvidenceRecord.source_revision_id.in_(revision_ids)
    )
    affected = set(session.scalars(cited.distinct()))
    elsewhere = set(
        session.scalars(
            select(EvidenceRecord.target_entity_id)
            .where(
                EvidenceRecord.target_entity_id.in_(affected),
                or_(
                    EvidenceRecord.source_revision_id.is_(None),
                    EvidenceRecord.source_revision_id.not_in(revision_ids),
                ),
            )
            .distinct()
        )
    )
    removable = {
        entity_id
        for entity_id in affected - elsewhere
        if _without_human_involvement(session, entity_id)
    }
    reviewed_here = frozenset(
        entity_id for entity_id in affected - elsewhere - removable if _is_rule(session, entity_id)
    )
    if delete_reviewed:
        removable |= reviewed_here
    return EntityFate(removable, affected - removable, reviewed_here)


def _is_rule(session: Session, entity_id: uuid.UUID) -> bool:
    entity = session.get(KnowledgeEntity, entity_id)
    return entity is not None and entity.entity_type is EntityType.BUSINESS_RULE


def delete_entities(session: Session, entity_ids: Iterable[uuid.UUID]) -> int:
    """Deletes entities with everything hanging from them. Outside references are released."""
    ids = list(entity_ids)
    if not ids:
        return 0
    versions = select(KnowledgeEntityVersion.id).where(
        KnowledgeEntityVersion.knowledge_entity_id.in_(ids)
    )
    evidence = select(EvidenceRecord.id).where(EvidenceRecord.target_entity_id.in_(ids))
    session.execute(
        update(Finding).where(Finding.rule_version_id.in_(versions)).values(rule_version_id=None)
    )
    session.execute(
        update(FlowTransition)
        .where(FlowTransition.evidence_record_id.in_(evidence))
        .values(evidence_record_id=None)
    )
    session.execute(
        delete(EntityRelationship).where(
            or_(
                EntityRelationship.from_entity_id.in_(ids),
                EntityRelationship.to_entity_id.in_(ids),
            )
        )
    )
    # A relationship between other entities that used this evidence is kept without it.
    session.execute(
        update(EntityRelationship)
        .where(EntityRelationship.evidence_record_id.in_(evidence))
        .values(evidence_record_id=None)
    )
    findings = set(
        session.scalars(
            select(FindingEvidence.finding_id).where(
                FindingEvidence.evidence_record_id.in_(evidence)
            )
        )
    )
    session.execute(delete(FindingEvidence).where(FindingEvidence.evidence_record_id.in_(evidence)))
    session.execute(delete(EvidenceRecord).where(EvidenceRecord.target_entity_id.in_(ids)))
    session.execute(
        delete(ReleaseSnapshotEntry).where(ReleaseSnapshotEntry.knowledge_entity_id.in_(ids))
    )
    # Flows: transitions, steps and versions before the definition.
    flow_versions = select(FlowVersion.knowledge_entity_version_id).where(
        FlowVersion.knowledge_entity_version_id.in_(versions)
    )
    session.execute(delete(FlowTransition).where(FlowTransition.flow_version_id.in_(flow_versions)))
    session.execute(delete(FlowStep).where(FlowStep.flow_version_id.in_(flow_versions)))
    session.execute(
        delete(FlowVersion).where(FlowVersion.knowledge_entity_version_id.in_(versions))
    )
    session.execute(delete(FlowDefinition).where(FlowDefinition.knowledge_entity_id.in_(ids)))
    session.execute(
        delete(BusinessRuleVersion).where(
            BusinessRuleVersion.knowledge_entity_version_id.in_(versions)
        )
    )
    session.execute(delete(BusinessRule).where(BusinessRule.knowledge_entity_id.in_(ids)))
    session.execute(
        delete(KnowledgeEntityVersion).where(KnowledgeEntityVersion.knowledge_entity_id.in_(ids))
    )
    # A person's links from these rules to tickets (ADR-014): they point to nothing now.
    session.execute(
        delete(TicketLink).where(
            TicketLink.from_kind == "rule", TicketLink.from_ref.in_([str(i) for i in ids])
        )
    )
    deleted = session.execute(delete(KnowledgeEntity).where(KnowledgeEntity.id.in_(ids)))
    revalidate_findings(session, findings)
    return deleted.rowcount  # type: ignore[attr-defined, no-any-return]


def forget_revisions(
    session: Session, revision_ids: Select[uuid.UUID], delete_reviewed: bool = False
) -> dict[str, int]:
    """Deletes the evidence of those revisions and applies to each entity what it needs.

    Call before deleting the revisions.
    """
    fate = entity_fate(session, revision_ids, delete_reviewed)
    entities_deleted = delete_entities(session, fate.delete)
    evidence = select(EvidenceRecord.id).where(EvidenceRecord.source_revision_id.in_(revision_ids))
    versions = {
        version_id
        for version_id in session.scalars(
            select(EvidenceRecord.target_version_id).where(EvidenceRecord.id.in_(evidence))
        )
        if version_id is not None
    }
    findings = set(
        session.scalars(
            select(FindingEvidence.finding_id).where(
                FindingEvidence.evidence_record_id.in_(evidence)
            )
        )
    )
    session.execute(
        update(EntityRelationship)
        .where(EntityRelationship.evidence_record_id.in_(evidence))
        .values(evidence_record_id=None)
    )
    session.execute(
        update(FlowTransition)
        .where(FlowTransition.evidence_record_id.in_(evidence))
        .values(evidence_record_id=None)
    )
    session.execute(delete(FindingEvidence).where(FindingEvidence.evidence_record_id.in_(evidence)))
    deleted = session.execute(delete(EvidenceRecord).where(EvidenceRecord.id.in_(evidence)))
    session.execute(
        update(KnowledgeEntity)
        .where(KnowledgeEntity.id.in_(fate.review))
        .values(review_reason=REVIEW_SOURCE_DELETED)
    )
    return {
        "evidence_deleted": deleted.rowcount,  # type: ignore[attr-defined]
        "entities_deleted": entities_deleted,
        "entities_marked_for_review": len(fate.review),
        "versions_unverified": revalidate_versions(session, versions),
        "findings_reopened": revalidate_findings(session, findings),
    }


def forget_releases(session: Session, connection_id: uuid.UUID) -> int:
    """Deletes a source's releases and their snapshots. The versions they pinned remain."""
    releases = select(SoftwareRelease.id).where(
        SoftwareRelease.source_connection_id == connection_id
    )
    snapshots = select(ReleaseKnowledgeSnapshot.id).where(
        ReleaseKnowledgeSnapshot.software_release_id.in_(releases)
    )
    session.execute(
        delete(ReleaseSnapshotEntry).where(ReleaseSnapshotEntry.snapshot_id.in_(snapshots))
    )
    session.execute(
        delete(ReleaseKnowledgeSnapshot).where(ReleaseKnowledgeSnapshot.id.in_(snapshots))
    )
    # A release points to the previous one: links are released before deleting.
    session.execute(
        update(SoftwareRelease)
        .where(SoftwareRelease.source_connection_id == connection_id)
        .values(previous_release_id=None)
    )
    deleted = session.execute(
        delete(SoftwareRelease).where(SoftwareRelease.source_connection_id == connection_id)
    )
    return deleted.rowcount  # type: ignore[attr-defined, no-any-return]


def citing_runs(session: Session, project_id: uuid.UUID, referenced: set[str]) -> list[uuid.UUID]:
    """Questions and analyses whose result names any of those identifiers (the source,
    its revisions or its releases): their text may paraphrase what is about to be deleted."""
    runs = session.execute(
        select(AnalysisRun.id, AnalysisRun.result).where(AnalysisRun.project_id == project_id)
    )
    return [
        run_id
        for run_id, result in runs
        if set(_UUID.findall(json.dumps(result or {}))) & referenced
    ]


def forget_analysis_run(session: Session, run_id: uuid.UUID) -> None:
    """Deletes the findings of a question or analysis. Call before deleting the analysis."""
    findings = select(Finding.id).where(Finding.analysis_run_id == run_id)
    session.execute(delete(FindingEvidence).where(FindingEvidence.finding_id.in_(findings)))
    session.execute(delete(Finding).where(Finding.analysis_run_id == run_id))
    # The rule is kept; it only loses the link to the question it came from.
    session.execute(
        update(BusinessRuleVersion)
        .where(BusinessRuleVersion.created_from_analysis_id == run_id)
        .values(created_from_analysis_id=None)
    )


def delete_runs(session: Session, run_ids: Iterable[uuid.UUID]) -> int:
    count = 0
    for run_id in run_ids:
        forget_analysis_run(session, run_id)
        count += session.execute(delete(AnalysisRun).where(AnalysisRun.id == run_id)).rowcount  # type: ignore[attr-defined]
    return count


def forget_project_knowledge(session: Session, project_id: uuid.UUID) -> dict[str, int]:
    """All of a project's knowledge, analyses and releases."""
    runs = list(session.scalars(select(AnalysisRun.id).where(AnalysisRun.project_id == project_id)))
    entities = list(
        session.scalars(select(KnowledgeEntity.id).where(KnowledgeEntity.project_id == project_id))
    )
    snapshots = select(ReleaseKnowledgeSnapshot.id).where(
        ReleaseKnowledgeSnapshot.project_id == project_id
    )
    session.execute(
        delete(ReleaseSnapshotEntry).where(ReleaseSnapshotEntry.snapshot_id.in_(snapshots))
    )
    session.execute(
        delete(ReleaseKnowledgeSnapshot).where(ReleaseKnowledgeSnapshot.project_id == project_id)
    )
    session.execute(
        update(SoftwareRelease)
        .where(SoftwareRelease.project_id == project_id)
        .values(previous_release_id=None)
    )
    releases = session.execute(
        delete(SoftwareRelease).where(SoftwareRelease.project_id == project_id)
    )
    # Analyses first: their findings point to rule versions.
    runs_deleted = delete_runs(session, runs)
    return {
        "analyses_deleted": runs_deleted,
        "entities_deleted": delete_entities(session, entities),
        "releases_deleted": releases.rowcount,  # type: ignore[attr-defined]
    }
