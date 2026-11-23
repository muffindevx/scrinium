"""Knowledge-model reads for the UI: Rulebook, relationship graph and flows.

Nothing here writes. The backend builds the graph from stored entities and relationships,
never the LLM, and each edge says where it comes from: observed in a source, backed
by a test, inferred (no evidence, or proposed by the LLM) or confirmed by a person.
"""

import base64
import json
import uuid
from typing import Any, Final

from sqlalchemy import String, cast, func, select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.common import get_in_project
from scrinium.application.knowledge.entities import FLOW_ENTITY_TYPES, latest_version, list_versions
from scrinium.application.knowledge.evidence import evidence_profile
from scrinium.application.knowledge.rule_impact import rule_impact
from scrinium.domain.enums import (
    EntityLifecycle,
    EvidenceType,
    VerificationMethod,
    VerificationStatus,
    VersionOrigin,
)
from scrinium.infrastructure.code_structure import innermost_function
from scrinium.infrastructure.sqlite.models import (
    BusinessRule,
    BusinessRuleVersion,
    CodeSymbol,
    EntityRelationship,
    EvidenceRecord,
    Finding,
    FlowDefinition,
    FlowStep,
    FlowTransition,
    FlowVersion,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceRevision,
)

MAX_GRAPH_NODES: Final = 200
MAX_GRAPH_EDGES: Final = 500
_EDGE_ORIGIN: Final = {
    VerificationMethod.DIRECT_REFERENCE: "observed",
    VerificationMethod.STATIC_ANALYSIS: "observed",
    VerificationMethod.TEST_RESULT: "supported",
    VerificationMethod.HUMAN_REVIEW: "confirmed",
    VerificationMethod.LLM_PROPOSAL: "inferred",
}


def _version_summary(version: KnowledgeEntityVersion | None) -> dict[str, Any] | None:
    if version is None:
        return None
    return {
        "id": str(version.id), "version_number": version.version_number,
        "verification_status": version.verification_status.value,
        "origin": version.origin.value, "model_name": version.model_name,
        "extractor_version": version.extractor_version, "reviewed_by": version.reviewed_by,
        "reviewed_at": version.reviewed_at.isoformat() if version.reviewed_at else None,
        "created_at": version.created_at.isoformat(),
    }  # fmt: skip


def list_rules(
    session: Session,
    project: Project,
    verification: VerificationStatus | None = None,
    lifecycle: EntityLifecycle | None = None,
    text: str | None = None,
) -> list[dict[str, Any]]:
    # Distinct citations: a new rule version repeats the previous one's citations (the
    # same lines, the same excerpt), and they are not new evidence.
    evidence_count = (
        select(
            func.count(
                func.distinct(
                    func.coalesce(EvidenceRecord.excerpt_hash, cast(EvidenceRecord.id, String))
                )
            )
        )
        .where(EvidenceRecord.target_entity_id == KnowledgeEntity.id)
        .scalar_subquery()
    )
    query = (
        select(KnowledgeEntity, BusinessRule, evidence_count)
        .join(BusinessRule, BusinessRule.knowledge_entity_id == KnowledgeEntity.id)
        .where(KnowledgeEntity.project_id == project.id)
        .order_by(KnowledgeEntity.name, KnowledgeEntity.id)
    )
    if lifecycle is not None:
        query = query.where(KnowledgeEntity.lifecycle_status == lifecycle)
    if text:
        pattern = f"%{text.lower()}%"
        query = query.where(
            func.lower(KnowledgeEntity.name).like(pattern)
            | func.lower(KnowledgeEntity.summary).like(pattern)
            | func.lower(KnowledgeEntity.canonical_key).like(pattern)
            # A ticket key finds the rules it labels (IMP-2).
            | func.lower(cast(KnowledgeEntity.metadata_["tickets"], String)).like(pattern)
            # A file path finds the rules cited in it (the ⌘K palette opens files this way).
            | select(EvidenceRecord.id)
            .where(
                EvidenceRecord.target_entity_id == KnowledgeEntity.id,
                func.lower(cast(EvidenceRecord.locator["path"], String)).like(pattern),
            )
            .exists()
        )
    rows = session.execute(query).all()
    versions = _latest_versions(session, project)
    citations = _first_code_citations(session, [version.id for version in versions.values()])
    rules = []
    for entity, rule, evidence in rows:
        version = versions.get(entity.id)
        citation = None if version is None else citations.get(version.id)
        if verification is not None and (
            version is None or version.verification_status is not verification
        ):
            continue
        rules.append(
            {
                "entity_id": str(entity.id), "canonical_key": entity.canonical_key,
                "name": entity.name, "summary": entity.summary,
                "lifecycle_status": entity.lifecycle_status.value,
                "rule_category": rule.rule_category.value, "criticality": rule.criticality.value,
                "latest_version": _version_summary(version), "evidence_count": evidence,
                "review_reason": entity.review_reason, "citation": citation,
                "tickets": list(entity.metadata_.get("tickets", [])),
            }
        )  # fmt: skip
    return rules


def _latest_versions(session: Session, project: Project) -> dict[uuid.UUID, KnowledgeEntityVersion]:
    """The latest version of every entity in the project, in one query (the list has thousands)."""
    latest = (
        select(
            KnowledgeEntityVersion.knowledge_entity_id,
            func.max(KnowledgeEntityVersion.version_number).label("number"),
        )
        .where(KnowledgeEntityVersion.project_id == project.id)
        .group_by(KnowledgeEntityVersion.knowledge_entity_id)
        .subquery()
    )
    versions = session.scalars(
        select(KnowledgeEntityVersion).join(
            latest,
            (KnowledgeEntityVersion.knowledge_entity_id == latest.c.knowledge_entity_id)
            & (KnowledgeEntityVersion.version_number == latest.c.number),
        )
    )
    return {version.knowledge_entity_id: version for version in versions}


def _first_code_citations(
    session: Session, version_ids: list[uuid.UUID]
) -> dict[uuid.UUID, dict[str, Any] | None]:
    """`first_code_citation` for many versions, in one query per batch of ids."""
    first: dict[uuid.UUID, EvidenceRecord] = {}
    for start in range(0, len(version_ids), _IN_BATCH):
        records = session.scalars(
            _code_citations().where(
                EvidenceRecord.target_version_id.in_(version_ids[start : start + _IN_BATCH])
            )
        )
        for record in records:
            first.setdefault(record.target_version_id, record)
    return {version_id: _citation(record) for version_id, record in first.items()}


# SQLite caps the parameters of one statement; the list asks for its ids in batches.
_IN_BATCH: Final = 900


def _code_citations() -> Any:
    return (
        select(EvidenceRecord)
        .where(
            EvidenceRecord.source_revision_id.is_not(None),
            EvidenceRecord.evidence_type.in_((EvidenceType.CODE, EvidenceType.SCHEMA)),
        )
        .order_by(EvidenceRecord.created_at)
    )


def first_code_citation(session: Session, version_id: uuid.UUID) -> dict[str, Any] | None:
    """Where the rule lives in the code: the list shows those lines without opening the rule."""
    record = session.scalars(
        _code_citations().where(EvidenceRecord.target_version_id == version_id).limit(1)
    ).first()
    return None if record is None else _citation(record)


def _citation(record: EvidenceRecord) -> dict[str, Any] | None:
    if "start_line" not in (record.locator or {}):
        return None
    return {
        "revision_id": str(record.source_revision_id), "path": record.locator.get("path"),
        "commit": record.locator.get("commit"), "start_line": record.locator["start_line"],
        "end_line": record.locator.get("end_line", record.locator["start_line"]),
    }  # fmt: skip


def _evidence(record: EvidenceRecord) -> dict[str, Any]:
    return {
        "id": str(record.id), "role": record.evidence_role.value,
        "type": record.evidence_type.value, "method": record.verification_method.value,
        "status": record.verification_status.value, "locator": record.locator,
        "excerpt": record.excerpt,
        "revision_id": str(record.source_revision_id) if record.source_revision_id else None,
        "created_at": record.created_at.isoformat(),
    }  # fmt: skip


# Over this many lines, a function is looked into for the nested one that holds the citation.
_LARGE_FUNCTION_LINES: Final = 150


def enclosing_symbol(session: Session, record: EvidenceRecord) -> dict[str, Any] | None:
    """The smallest function or method that holds a citation's lines (stage 3, phase A): the
    rule screen shows it whole, with the rule's lines marked."""
    locator = record.locator or {}
    if record.source_revision_id is None or "start_line" not in locator:
        return None
    start = int(locator["start_line"])
    end = int(locator.get("end_line", start))
    symbol = session.scalars(
        select(CodeSymbol)
        .where(
            CodeSymbol.source_revision_id == record.source_revision_id,
            CodeSymbol.kind != "class",
            CodeSymbol.start_line <= start,
            CodeSymbol.end_line >= end,
        )
        .order_by(CodeSymbol.end_line - CodeSymbol.start_line)
        .limit(1)
    ).first()
    if symbol is None:
        return None
    found = {
        "id": str(symbol.id), "name": symbol.qualified_name, "kind": symbol.kind,
        "start_line": symbol.start_line, "end_line": symbol.end_line, "nested_in": None,
    }  # fmt: skip
    if symbol.end_line - symbol.start_line > _LARGE_FUNCTION_LINES:
        # A huge function usually wraps nested ones: show the one that really holds the rule.
        revision = session.get(SourceRevision, record.source_revision_id)
        inner = innermost_function(
            locator.get("path", ""), (revision.text_content or "") if revision else "", start, end
        )
        if inner is not None and inner[2] - inner[1] < symbol.end_line - symbol.start_line:
            found.update(
                name=inner[0], start_line=inner[1], end_line=inner[2],
                nested_in=symbol.qualified_name,
            )  # fmt: skip
    return found


def _neighbours(session: Session, entity_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = session.execute(
        select(EntityRelationship, KnowledgeEntity)
        .join(
            KnowledgeEntity,
            (KnowledgeEntity.id == EntityRelationship.to_entity_id)
            | (KnowledgeEntity.id == EntityRelationship.from_entity_id),
        )
        .where(
            (EntityRelationship.from_entity_id == entity_id)
            | (EntityRelationship.to_entity_id == entity_id),
            KnowledgeEntity.id != entity_id,
        )
    ).all()
    return [
        {
            "id": str(rel.id), "relation_type": rel.relation_type.value,
            "direction": "out" if rel.from_entity_id == entity_id else "in",
            "other": {"id": str(other.id), "name": other.name,
                      "entity_type": other.entity_type.value},
            "basis": rel.basis,
            "evidence_id": str(rel.evidence_record_id) if rel.evidence_record_id else None,
        }
        for rel, other in rows
    ]  # fmt: skip


def rule_detail(session: Session, project: Project, entity_id: uuid.UUID) -> dict[str, Any]:
    entity = get_in_project(session, KnowledgeEntity, project, entity_id)
    rule = session.scalars(
        select(BusinessRule).where(BusinessRule.knowledge_entity_id == entity.id)
    ).one()
    versions = []
    for version in reversed(list_versions(session, entity.id)):
        content = session.get(BusinessRuleVersion, version.id)
        records = session.scalars(
            select(EvidenceRecord)
            .where(EvidenceRecord.target_version_id == version.id)
            .order_by(EvidenceRecord.created_at)
        )
        findings = session.scalars(select(Finding).where(Finding.rule_version_id == version.id))
        versions.append(
            {
                **(_version_summary(version) or {}),
                "content": None if content is None else {
                    "title": content.title, "statement": content.statement,
                    "rationale": content.rationale, "scope": content.scope,
                    "preconditions": content.preconditions,
                    "trigger_conditions": content.trigger_conditions,
                    "expected_behavior": content.expected_behavior,
                    "exceptions": content.exceptions, "impact_targets": content.impact_targets,
                    "from_analysis_id": str(content.created_from_analysis_id)
                    if content.created_from_analysis_id else None,
                },
                "evidence": [
                    {**_evidence(r), "symbol": enclosing_symbol(session, r)} for r in records
                ],
                "profile": evidence_profile(session, version.id),
                "findings": [
                    {"id": str(f.id), "title": f.title, "finding_type": f.finding_type.value,
                     "assessment_status": f.assessment_status.value,
                     "analysis_id": str(f.analysis_run_id)}
                    for f in findings
                ],
            }
        )  # fmt: skip
    entity_evidence = session.scalars(
        select(EvidenceRecord).where(
            EvidenceRecord.target_entity_id == entity.id,
            EvidenceRecord.target_version_id.is_(None),
        )
    )
    return {
        "entity_id": str(entity.id), "canonical_key": entity.canonical_key,
        "name": entity.name, "summary": entity.summary,
        "lifecycle_status": entity.lifecycle_status.value,
        "rule_category": rule.rule_category.value, "criticality": rule.criticality.value,
        "owner_ref": rule.owner_ref, "versions": versions,
        "review_reason": entity.review_reason,
        "entity_evidence": [_evidence(r) for r in entity_evidence],
        "relationships": _neighbours(session, entity.id),
        "tickets": list(entity.metadata_.get("tickets", [])),
        "impact": _located(session, rule_impact(session, project, entity)),
    }  # fmt: skip


def _located(session: Session, impact: dict[str, Any]) -> dict[str, Any]:
    """Each rule the impact names, with where it lives: two rules can share a name, and the file
    and line are what tell them apart (and say why one depends on the other)."""
    named = [*impact["same_decision"], *impact["dependents"]]
    versions = {
        item["id"]: version
        for item in named
        if (version := latest_version(session, uuid.UUID(item["id"]))) is not None
    }
    citations = _first_code_citations(session, [version.id for version in versions.values()])
    for item in named:
        version = versions.get(item["id"])
        item["citation"] = None if version is None else citations.get(version.id)
    return impact


def _node_state(version: KnowledgeEntityVersion | None) -> str:
    if version is None:
        return "no_version"
    if version.verification_status is VerificationStatus.HUMAN_VERIFIED:
        return "confirmed"
    if version.verification_status in (
        VerificationStatus.SOURCE_SUPPORTED,
        VerificationStatus.TEST_SUPPORTED,
    ):
        return "supported"
    if version.verification_status is VerificationStatus.CONFLICTED:
        return "conflicted"
    if version.verification_status is VerificationStatus.REJECTED:
        return "rejected"
    # Inferred is what Scrinium proposes; what a person wrote is only unverified.
    if version.origin in (VersionOrigin.SOURCE_EXTRACTION, VersionOrigin.DERIVED_ANALYSIS):
        return "inferred"
    return "unverified"


def _components(
    ids: list[uuid.UUID], pairs: list[tuple[uuid.UUID, uuid.UUID]]
) -> list[list[uuid.UUID]]:
    """Groups of entities joined by relationships, largest first (ties in the given order)."""
    parent = {i: i for i in ids}

    def root(i: uuid.UUID) -> uuid.UUID:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in pairs:
        parent[root(a)] = root(b)
    groups: dict[uuid.UUID, list[uuid.UUID]] = {}
    for i in ids:
        groups.setdefault(root(i), []).append(i)
    return sorted(groups.values(), key=len, reverse=True)


def graph(session: Session, project: Project) -> dict[str, Any]:
    """The entities that relate to others, and every process, with their edges, origin and basis.

    Any other entity with no relationship is not on the map (the Rulebook lists it); the count says
    how many. Whole groups of related entities go in, largest first, up to `MAX_GRAPH_NODES`.
    """
    entities = list(
        session.scalars(
            select(KnowledgeEntity)
            .where(
                KnowledgeEntity.project_id == project.id,
                KnowledgeEntity.lifecycle_status != EntityLifecycle.RETIRED,
            )
            .order_by(KnowledgeEntity.entity_type, KnowledgeEntity.canonical_key)
        )
    )
    by_id = {e.id: e for e in entities}
    relations = [
        r
        for r in session.scalars(
            select(EntityRelationship)
            .where(EntityRelationship.project_id == project.id)
            .order_by(EntityRelationship.created_at)
        )
        if r.from_entity_id in by_id and r.to_entity_id in by_id
    ]
    related = {r.from_entity_id for r in relations} | {r.to_entity_id for r in relations}
    # A process is drawn even alone: it is what the map is for, and there are few.
    drawn = related | {e.id for e in entities if e.entity_type in FLOW_ENTITY_TYPES}
    groups = _components(
        [e.id for e in entities if e.id in drawn],
        [(r.from_entity_id, r.to_entity_id) for r in relations],
    )
    shown: list[uuid.UUID] = []
    for group in groups:
        if len(shown) + len(group) > MAX_GRAPH_NODES:
            # A group that does not fit is cut only if nothing is shown yet.
            if not shown:
                shown = group[:MAX_GRAPH_NODES]
            continue
        shown.extend(group)
    shown_set = set(shown)
    evidence_ids = {r.evidence_record_id for r in relations if r.evidence_record_id}
    methods = dict(
        session.execute(
            select(EvidenceRecord.id, EvidenceRecord.verification_method).where(
                EvidenceRecord.id.in_(evidence_ids)
            )
        ).all()
    )
    edges = []
    for relation in relations:
        if relation.from_entity_id not in shown_set or relation.to_entity_id not in shown_set:
            continue
        method = methods.get(relation.evidence_record_id) if relation.evidence_record_id else None
        edges.append(
            {
                "id": str(relation.id), "from": str(relation.from_entity_id),
                "to": str(relation.to_entity_id), "relation_type": relation.relation_type.value,
                "origin": _EDGE_ORIGIN[method] if method else "inferred",
                "basis": relation.basis,
                "evidence_id": str(relation.evidence_record_id)
                if relation.evidence_record_id else None,
            }
        )  # fmt: skip
    nodes = [
        {
            "id": str(e.id), "name": e.name, "entity_type": e.entity_type.value,
            "canonical_key": e.canonical_key, "lifecycle_status": e.lifecycle_status.value,
            "state": _node_state(latest_version(session, e.id)),
        }
        for e in (by_id[i] for i in shown)
    ]  # fmt: skip
    return {
        "nodes": nodes,
        "edges": edges[:MAX_GRAPH_EDGES],
        "coverage": {
            "entities_total": len(entities), "entities_shown": len(nodes),
            "relationships_total": len(relations),
            "relationships_shown": min(len(edges), MAX_GRAPH_EDGES),
            "entities_without_relationships": len(entities) - len(drawn),
            "groups_total": len(groups),
        },
    }  # fmt: skip


def list_flows(session: Session, project: Project) -> list[dict[str, Any]]:
    rows = session.execute(
        select(KnowledgeEntity, FlowDefinition)
        .join(FlowDefinition, FlowDefinition.knowledge_entity_id == KnowledgeEntity.id)
        .where(KnowledgeEntity.project_id == project.id)
        .order_by(KnowledgeEntity.name)
    ).all()
    return [
        {
            "entity_id": str(entity.id), "name": entity.name, "summary": entity.summary,
            "entity_type": entity.entity_type.value, "flow_type": definition.flow_type.value,
            "lifecycle_status": entity.lifecycle_status.value,
            "latest_version": _version_summary(latest_version(session, entity.id)),
        }
        for entity, definition in rows
    ]  # fmt: skip


def flow_detail(session: Session, project: Project, entity_id: uuid.UUID) -> dict[str, Any]:
    entity = get_in_project(session, KnowledgeEntity, project, entity_id)
    if entity.entity_type not in FLOW_ENTITY_TYPES:
        raise LookupError
    versions = []
    for version in reversed(list_versions(session, entity.id)):
        flow = session.get(FlowVersion, version.id)
        steps = list(
            session.scalars(
                select(FlowStep)
                .where(FlowStep.flow_version_id == version.id)
                .order_by(FlowStep.position)
            )
        )
        keys = {step.id: step.step_key for step in steps}
        transitions = session.scalars(
            select(FlowTransition).where(FlowTransition.flow_version_id == version.id)
        )
        versions.append(
            {
                **(_version_summary(version) or {}),
                "description": flow.description if flow else "",
                "entry_conditions": flow.entry_conditions if flow else [],
                "success_conditions": flow.success_conditions if flow else [],
                "failure_conditions": flow.failure_conditions if flow else [],
                "steps": [
                    {"key": s.step_key, "position": s.position, "step_type": s.step_type.value,
                     "label": s.label, "description": s.description,
                     "entity_id": str(s.knowledge_entity_id) if s.knowledge_entity_id else None,
                     "condition_expression": s.condition_expression}
                    for s in steps
                ],
                "transitions": [
                    {"from": keys[t.from_step_id], "to": keys[t.to_step_id],
                     "transition_type": t.transition_type.value,
                     "condition": t.condition_description,
                     "evidence_id": str(t.evidence_record_id) if t.evidence_record_id else None}
                    for t in transitions
                ],
            }
        )  # fmt: skip
    return {
        "entity_id": str(entity.id), "name": entity.name, "summary": entity.summary,
        "entity_type": entity.entity_type.value,
        "lifecycle_status": entity.lifecycle_status.value, "versions": versions,
    }  # fmt: skip


def make_cursor(name: str, entity_id: str) -> str:
    """An opaque position in the rules list: after this name and id (phase 9.4)."""
    raw = json.dumps([name, entity_id], ensure_ascii=False).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def read_cursor(cursor: str) -> tuple[str, str]:
    """Raises ValueError for a cursor this API did not make."""
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        name, entity_id = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError):
        raise ValueError("invalid cursor") from None
    if not isinstance(name, str) or not isinstance(entity_id, str):
        raise ValueError("invalid cursor")
    return name, entity_id
