"""Knowledge entities and their versions: business rules, flows and their review.

A version is never modified: a change creates the next one. Every version is born `unverified`,
whether a person or the LLM writes it; it moves up only with evidence that backs it or with a
recorded human review.
"""

import re
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scrinium.application.knowledge.common import (
    KnowledgeError,
    KnowledgeNotFoundError,
    audit,
    content_hash,
    get_in_project,
    validate_metadata,
    validate_payload,
)
from scrinium.application.knowledge.content import FlowContent, RuleContent
from scrinium.domain.enums import (
    Criticality,
    EntityLifecycle,
    EntityType,
    EvidenceRole,
    EvidenceType,
    FlowType,
    RuleCategory,
    VerificationMethod,
    VerificationStatus,
    VersionOrigin,
)
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    BusinessRule,
    BusinessRuleVersion,
    EvidenceRecord,
    FlowDefinition,
    FlowStep,
    FlowTransition,
    FlowVersion,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    utc_now,
)

CANONICAL_KEY_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._:/-]{0,199}$")
FLOW_ENTITY_TYPES: Final = frozenset(
    {
        EntityType.BUSINESS_FLOW,
        EntityType.USER_JOURNEY,
        EntityType.ARCHITECTURE_FLOW,
        EntityType.DATA_FLOW,
    }
)
# Types with an extension table: their versions are created with their own function.
_TYPED_ENTITIES: Final = FLOW_ENTITY_TYPES | {EntityType.BUSINESS_RULE}
_GENERATED: Final = frozenset({VersionOrigin.SOURCE_EXTRACTION, VersionOrigin.DERIVED_ANALYSIS})
_REVIEWED: Final = frozenset({VerificationStatus.HUMAN_VERIFIED, VerificationStatus.REJECTED})
# Methods the code checks. An LLM proposal never backs anything by itself.
_CHECKED_METHODS: Final = (VerificationMethod.DIRECT_REFERENCE, VerificationMethod.STATIC_ANALYSIS)


@dataclass(frozen=True)
class Provenance:
    """Where a version comes from. Generated content needs the extractor version."""

    origin: VersionOrigin
    model_provider: str | None = None
    model_name: str | None = None
    extractor_version: str | None = None
    created_by: str | None = None
    analysis_run_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.origin in _GENERATED and not self.extractor_version:
            raise KnowledgeError("extractor_version_required")


HUMAN: Final = Provenance(VersionOrigin.HUMAN)


def title_slug(title: str) -> str:
    """Part of a canonical key from a title: lowercase ASCII and hyphens."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_title.lower()).strip("-")[:120].rstrip("-")


def create_entity(
    session: Session,
    project: Project,
    *,
    entity_type: EntityType,
    canonical_key: str,
    name: str,
    summary: str = "",
    metadata: dict[str, Any] | None = None,
    lifecycle: EntityLifecycle = EntityLifecycle.PROPOSED,
    request_id: str | None = None,
) -> KnowledgeEntity:
    if not CANONICAL_KEY_PATTERN.fullmatch(canonical_key):
        raise KnowledgeError("invalid_canonical_key")
    name = name.strip()
    if not 0 < len(name) <= 300 or len(summary) > 2000:
        raise KnowledgeError("invalid_name")
    taken = select(KnowledgeEntity.id).where(
        KnowledgeEntity.project_id == project.id,
        KnowledgeEntity.entity_type == entity_type,
        KnowledgeEntity.canonical_key == canonical_key,
    )
    if session.scalar(taken) is not None:
        raise KnowledgeError("entity_exists")
    entity = KnowledgeEntity(
        workspace_id=project.workspace_id,
        project_id=project.id,
        entity_type=entity_type,
        canonical_key=canonical_key,
        name=name,
        summary=summary.strip(),
        lifecycle_status=lifecycle,
        metadata_=validate_metadata(metadata),
    )
    session.add(entity)
    try:
        session.flush()
    except IntegrityError as error:
        raise KnowledgeError("entity_exists") from error
    audit(
        session, project, "knowledge_entity.create", "knowledge_entity", entity.id, request_id,
        {"entity_type": entity_type.value},
    )  # fmt: skip
    return entity


def latest_version(session: Session, entity_id: uuid.UUID) -> KnowledgeEntityVersion | None:
    return session.scalars(
        select(KnowledgeEntityVersion)
        .where(KnowledgeEntityVersion.knowledge_entity_id == entity_id)
        .order_by(KnowledgeEntityVersion.version_number.desc())
        .limit(1)
    ).first()


def list_versions(session: Session, entity_id: uuid.UUID) -> list[KnowledgeEntityVersion]:
    return list(
        session.scalars(
            select(KnowledgeEntityVersion)
            .where(KnowledgeEntityVersion.knowledge_entity_id == entity_id)
            .order_by(KnowledgeEntityVersion.version_number)
        )
    )


def _initial_lifecycle(provenance: Provenance) -> EntityLifecycle:
    # What the LLM or an analysis proposes is a proposal until someone reviews it.
    return EntityLifecycle.PROPOSED if provenance.origin in _GENERATED else EntityLifecycle.ACTIVE


def _add_version(
    session: Session,
    project: Project,
    entity: KnowledgeEntity,
    digest: str,
    payload: dict[str, Any],
    provenance: Provenance,
    request_id: str | None,
) -> tuple[KnowledgeEntityVersion, bool]:
    """Returns the version and whether it is new. Repeating the current content creates none."""
    previous = latest_version(session, entity.id)
    if previous is not None and previous.content_hash == digest:
        return previous, False
    version = KnowledgeEntityVersion(
        workspace_id=project.workspace_id,
        project_id=project.id,
        knowledge_entity_id=entity.id,
        version_number=1 if previous is None else previous.version_number + 1,
        content_hash=digest,
        payload=payload,
        origin=provenance.origin,
        model_provider=provenance.model_provider,
        model_name=provenance.model_name,
        extractor_version=provenance.extractor_version,
        created_by=provenance.created_by,
    )
    session.add(version)
    session.flush()
    audit(
        session, project, "knowledge_version.create", "knowledge_entity_version", version.id,
        request_id, {"entity_id": str(entity.id), "version_number": version.version_number,
                     "origin": provenance.origin.value},
    )  # fmt: skip
    return version, True


def add_version(
    session: Session,
    project: Project,
    entity_id: uuid.UUID,
    payload: dict[str, Any],
    provenance: Provenance = HUMAN,
    request_id: str | None = None,
) -> tuple[KnowledgeEntityVersion, bool]:
    """Version of an entity without a typed table (service, endpoint, ticket...)."""
    entity = get_in_project(session, KnowledgeEntity, project, entity_id)
    if entity.entity_type in _TYPED_ENTITIES:
        raise KnowledgeError("typed_entity")
    payload = validate_payload(payload)
    return _add_version(
        session, project, entity, content_hash(payload), payload, provenance, request_id
    )


def _check_analysis(session: Session, project: Project, provenance: Provenance) -> None:
    if provenance.analysis_run_id is None:
        return
    run = session.get(AnalysisRun, provenance.analysis_run_id)
    if run is None or run.project_id != project.id:
        raise KnowledgeNotFoundError


def create_business_rule(
    session: Session,
    project: Project,
    *,
    canonical_key: str,
    category: RuleCategory,
    content: RuleContent,
    criticality: Criticality = Criticality.MEDIUM,
    owner_ref: str | None = None,
    provenance: Provenance = HUMAN,
    request_id: str | None = None,
) -> BusinessRuleVersion:
    _check_analysis(session, project, provenance)
    entity = create_entity(
        session, project, entity_type=EntityType.BUSINESS_RULE, canonical_key=canonical_key,
        name=content.title, summary=content.statement[:2000],
        lifecycle=_initial_lifecycle(provenance), request_id=request_id,
    )  # fmt: skip
    session.add(
        BusinessRule(
            workspace_id=project.workspace_id,
            project_id=project.id,
            knowledge_entity_id=entity.id,
            rule_category=category,
            criticality=criticality,
            owner_ref=owner_ref,
        )
    )
    version, _ = add_rule_version(session, project, entity.id, content, provenance, request_id)
    return version


def add_rule_version(
    session: Session,
    project: Project,
    entity_id: uuid.UUID,
    content: RuleContent,
    provenance: Provenance = HUMAN,
    request_id: str | None = None,
) -> tuple[BusinessRuleVersion, bool]:
    entity = get_in_project(session, KnowledgeEntity, project, entity_id)
    if entity.entity_type is not EntityType.BUSINESS_RULE:
        raise KnowledgeError("not_a_business_rule")
    _check_analysis(session, project, provenance)
    data = content.model_dump(mode="json")
    previous = latest_version(session, entity.id)
    version, created = _add_version(
        session, project, entity, content_hash(data), {}, provenance, request_id
    )
    if not created:
        rule_version = session.get(BusinessRuleVersion, version.id)
        if rule_version is None:
            raise KnowledgeNotFoundError
        return rule_version, False
    rule_version = BusinessRuleVersion(
        knowledge_entity_version_id=version.id,
        workspace_id=project.workspace_id,
        project_id=project.id,
        supersedes_version_id=None if previous is None else previous.id,
        created_from_analysis_id=provenance.analysis_run_id,
        **data,
    )
    session.add(rule_version)
    entity.name, entity.summary = content.title, content.statement[:2000]
    session.flush()
    return rule_version, True


def create_flow(
    session: Session,
    project: Project,
    *,
    entity_type: EntityType,
    canonical_key: str,
    name: str,
    flow_type: FlowType,
    content: FlowContent,
    summary: str = "",
    provenance: Provenance = HUMAN,
    request_id: str | None = None,
) -> FlowVersion:
    if entity_type not in FLOW_ENTITY_TYPES:
        raise KnowledgeError("not_a_flow")
    entity = create_entity(
        session, project, entity_type=entity_type, canonical_key=canonical_key, name=name,
        summary=summary, lifecycle=_initial_lifecycle(provenance), request_id=request_id,
    )  # fmt: skip
    session.add(
        FlowDefinition(
            workspace_id=project.workspace_id,
            project_id=project.id,
            knowledge_entity_id=entity.id,
            flow_type=flow_type,
        )
    )
    session.flush()
    version, _ = add_flow_version(session, project, entity.id, content, provenance, request_id)
    return version


def add_flow_version(
    session: Session,
    project: Project,
    entity_id: uuid.UUID,
    content: FlowContent,
    provenance: Provenance = HUMAN,
    request_id: str | None = None,
) -> tuple[FlowVersion, bool]:
    """Creates the version with all its steps and transitions at once: a flow is not edited."""
    entity = get_in_project(session, KnowledgeEntity, project, entity_id)
    definition = session.scalars(
        select(FlowDefinition).where(FlowDefinition.knowledge_entity_id == entity.id)
    ).first()
    if definition is None:
        raise KnowledgeError("not_a_flow")
    linked = {step.entity_id for step in content.steps} - {None}
    for linked_id in linked:
        get_in_project(session, KnowledgeEntity, project, linked_id)
    for transition in content.transitions:
        if transition.evidence_id is not None:
            get_in_project(session, EvidenceRecord, project, transition.evidence_id)

    data = content.model_dump(mode="json")
    version, created = _add_version(
        session, project, entity, content_hash(data), {}, provenance, request_id
    )
    if not created:
        flow_version = session.get(FlowVersion, version.id)
        if flow_version is None:
            raise KnowledgeNotFoundError
        return flow_version, False
    scope = {"workspace_id": project.workspace_id, "project_id": project.id}
    flow_version = FlowVersion(
        knowledge_entity_version_id=version.id,
        flow_definition_id=definition.id,
        description=content.description or "",
        entry_conditions=data["entry_conditions"],
        success_conditions=data["success_conditions"],
        failure_conditions=data["failure_conditions"],
        **scope,
    )
    session.add(flow_version)
    # Without relationship() the session does not order these inserts: write in parts.
    session.flush()
    steps = {
        step.key: FlowStep(
            flow_version_id=version.id, step_key=step.key, position=position,
            step_type=step.step_type, label=step.label, description=step.description or "",
            knowledge_entity_id=step.entity_id, condition_expression=step.condition_expression,
            **scope,
        )
        for position, step in enumerate(content.steps)
    }  # fmt: skip
    session.add_all(steps.values())
    session.flush()
    session.add_all(
        FlowTransition(
            flow_version_id=version.id, from_step_id=steps[t.from_key].id,
            to_step_id=steps[t.to_key].id, transition_type=t.transition_type,
            condition_description=t.condition, evidence_record_id=t.evidence_id, **scope,
        )
        for t in content.transitions
    )  # fmt: skip
    session.flush()
    return flow_version, True


def _has_evidence(session: Session, version: KnowledgeEntityVersion, *conditions: Any) -> bool:
    query = select(
        exists().where(
            EvidenceRecord.target_version_id == version.id,
            EvidenceRecord.source_revision_id.is_not(None),
            *conditions,
        )
    )
    return bool(session.scalar(query))


def meets_requirements(
    session: Session, version: KnowledgeEntityVersion, status: VerificationStatus
) -> bool:
    """Whether the stored evidence allows the status. Human reviews do not depend on it."""
    if status is VerificationStatus.SOURCE_SUPPORTED:
        return _has_evidence(
            session, version,
            EvidenceRecord.evidence_role.in_((EvidenceRole.SUPPORTS, EvidenceRole.IMPLEMENTS)),
            EvidenceRecord.verification_method.in_(_CHECKED_METHODS),
        )  # fmt: skip
    if status is VerificationStatus.TEST_SUPPORTED:
        return _has_evidence(
            session, version,
            EvidenceRecord.evidence_role.in_((EvidenceRole.SUPPORTS, EvidenceRole.TESTS)),
            EvidenceRecord.evidence_type == EvidenceType.TEST,
            EvidenceRecord.verification_method == VerificationMethod.TEST_RESULT,
        )  # fmt: skip
    if status is VerificationStatus.CONFLICTED:
        return _has_evidence(
            session, version,
            EvidenceRecord.evidence_role == EvidenceRole.CONTRADICTS,
            EvidenceRecord.verification_method != VerificationMethod.LLM_PROPOSAL,
        )  # fmt: skip
    return True


def set_verification(
    session: Session,
    project: Project,
    version_id: uuid.UUID,
    status: VerificationStatus,
    *,
    reviewer: str | None = None,
    request_id: str | None = None,
) -> KnowledgeEntityVersion:
    version = get_in_project(session, KnowledgeEntityVersion, project, version_id)
    reviewer = (reviewer or "").strip()[:200] or None
    if status in _REVIEWED and reviewer is None:
        raise KnowledgeError("reviewer_required")
    if not meets_requirements(session, version, status):
        raise KnowledgeError("insufficient_evidence")
    previous = version.verification_status
    version.verification_status = status
    if reviewer is not None:
        version.reviewed_by, version.reviewed_at = reviewer, utc_now()
    entity = get_in_project(session, KnowledgeEntity, project, version.knowledge_entity_id)
    if status is VerificationStatus.HUMAN_VERIFIED and (
        entity.lifecycle_status is EntityLifecycle.PROPOSED
    ):
        entity.lifecycle_status = EntityLifecycle.ACTIVE
    # A person has reviewed it: it is no longer pending review.
    if status in _REVIEWED:
        entity.review_reason = None
    session.flush()
    audit(
        session, project, "knowledge_version.verify", "knowledge_entity_version", version.id,
        request_id, {"from": previous.value, "to": status.value},
    )  # fmt: skip
    return version


def revalidate_versions(session: Session, version_ids: Iterable[uuid.UUID]) -> int:
    """Moves back to `unverified` what lost the evidence backing it. Returns how many."""
    downgraded = 0
    for version_id in set(version_ids):
        version = session.get(KnowledgeEntityVersion, version_id)
        if version is None or meets_requirements(session, version, version.verification_status):
            continue
        version.verification_status = VerificationStatus.UNVERIFIED
        downgraded += 1
    session.flush()
    return downgraded
