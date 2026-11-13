"""The impact of a rule (stage 3, N7): what depends on it, read backwards from phase C.

Phase C stores `depends_on` from the rule whose code calls to the rule whose code is called.
Read backwards, it answers "if this rule changes, what else may break":

- the same decision in another place (`duplicate`, `constraint`): it has to change with it;
- the rules that depend on it, directly or through others (up to `MAX_DEPTH` steps), and on
  the copies of the same decision: a caller of a copy relies on the same decision;
- the processes with a step on it or on one of those rules, or related to them;
- the tickets that label it and those rules (IMP-2).

Nothing is computed by the model and nothing is written: it is a read of stored relationships.
"""

import uuid
from collections import deque
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.entities import FLOW_ENTITY_TYPES, latest_version
from scrinium.domain.enums import EntityLifecycle, RelationType
from scrinium.infrastructure.sqlite.models import (
    BusinessRule,
    EntityRelationship,
    FlowStep,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
)

# Phase C already follows calls up to 3 hops inside each relationship: three steps of rules is
# far enough to say what may break, and a longer chain stops being read.
MAX_DEPTH: Final = 3
MAX_DEPENDENTS: Final = 40
# The bases that mean "the same decision, written again elsewhere".
_SAME_DECISION: Final = ("duplicate", "constraint")
_CRITICALITY_ORDER: Final = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _same_decision(basis: str | None) -> bool:
    return basis is not None and basis.split(":", 1)[0] in _SAME_DECISION


def rule_impact(session: Session, project: Project, entity: KnowledgeEntity) -> dict[str, Any]:
    relations = list(
        session.scalars(
            select(EntityRelationship).where(EntityRelationship.project_id == project.id)
        )
    )
    entities = {
        e.id: e
        for e in session.scalars(
            select(KnowledgeEntity).where(
                KnowledgeEntity.project_id == project.id,
                KnowledgeEntity.lifecycle_status != EntityLifecycle.RETIRED,
            )
        )
    }
    criticality = dict(
        session.execute(
            select(BusinessRule.knowledge_entity_id, BusinessRule.criticality).where(
                BusinessRule.knowledge_entity_id.in_(entities)
            )
        ).all()
    )
    callers: dict[uuid.UUID, list[EntityRelationship]] = {}
    for relation in relations:
        if relation.relation_type is RelationType.DEPENDS_ON:
            callers.setdefault(relation.to_entity_id, []).append(relation)

    def summary(other: uuid.UUID) -> dict[str, Any]:
        found = entities[other]
        level = criticality.get(other)
        return {
            "id": str(other), "name": found.name, "entity_type": found.entity_type.value,
            "criticality": level.value if level else None,
            "tickets": list(found.metadata_.get("tickets", [])),
        }  # fmt: skip

    same = []
    for relation in relations:
        if not _same_decision(relation.basis) or entity.id not in (
            relation.from_entity_id,
            relation.to_entity_id,
        ):
            continue
        other = (
            relation.to_entity_id
            if relation.from_entity_id == entity.id
            else relation.from_entity_id
        )
        if other in entities and other != entity.id:
            same.append({**summary(other), "basis": relation.basis})

    # Breadth first from the rule and its copies: the shortest chain is the one shown.
    seen: set[uuid.UUID] = {entity.id} | {uuid.UUID(s["id"]) for s in same}
    queue = deque((start, 0) for start in sorted(seen, key=lambda i: i != entity.id))
    dependents: list[dict[str, Any]] = []
    truncated = False
    while queue:
        target, depth = queue.popleft()
        if depth == MAX_DEPTH:
            continue
        for relation in callers.get(target, []):
            caller = relation.from_entity_id
            if caller in seen or caller not in entities:
                continue
            seen.add(caller)
            if len(dependents) == MAX_DEPENDENTS:
                truncated = True
                continue
            dependents.append(
                {
                    **summary(caller), "depth": depth + 1, "basis": relation.basis,
                    # The rule it reaches this one through; None when it calls it directly.
                    "via": None if target == entity.id else {
                        "id": str(target), "name": entities[target].name,
                    },
                }
            )  # fmt: skip
            queue.append((caller, depth + 1))
    dependents.sort(
        key=lambda d: (d["depth"], _CRITICALITY_ORDER.get(d["criticality"], 4), d["name"])
    )

    affected = {entity.id} | {uuid.UUID(d["id"]) for d in [*same, *dependents]}
    flows: dict[uuid.UUID, set[uuid.UUID]] = {}
    for step_entity, version_id in session.execute(
        select(FlowStep.knowledge_entity_id, FlowStep.flow_version_id).where(
            FlowStep.project_id == project.id, FlowStep.knowledge_entity_id.in_(affected)
        )
    ):
        flow_id = _flow_of_latest_version(session, version_id)
        if step_entity is not None and flow_id is not None and flow_id in entities:
            flows.setdefault(flow_id, set()).add(step_entity)
    for relation in relations:
        for one, other in (
            (relation.from_entity_id, relation.to_entity_id),
            (relation.to_entity_id, relation.from_entity_id),
        ):
            found = entities.get(one)
            if found is not None and found.entity_type in FLOW_ENTITY_TYPES and other in affected:
                flows.setdefault(one, set()).add(other)
    processes = [
        {
            "id": str(flow_id),
            "name": entities[flow_id].name,
            "through": sorted(entities[r].name for r in through if r in entities),
        }
        for flow_id, through in flows.items()
    ]
    return {
        "same_decision": sorted(same, key=lambda s: s["name"]),
        "dependents": dependents,
        "truncated": truncated,
        "max_depth": MAX_DEPTH,
        "flows": sorted(processes, key=lambda f: f["name"]),
        "tickets": list(entity.metadata_.get("tickets", [])),
    }


def _flow_of_latest_version(session: Session, version_id: uuid.UUID) -> uuid.UUID | None:
    """The process a step belongs to, only if the step is in its latest version."""
    version = session.get(KnowledgeEntityVersion, version_id)
    if version is None:
        return None
    latest = latest_version(session, version.knowledge_entity_id)
    return version.knowledge_entity_id if latest is not None and latest.id == version.id else None
