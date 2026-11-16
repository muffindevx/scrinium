"""Which existing rule a newly extracted one is, by what it cites (ADR-011, phase 1.1).

The title never decides: a model writes it and another model writes another. A proposed rule is
the same rule as an existing one when its citation rests on the decision that continues one of
that rule's anchors (`reanchor`: exact, same file, moved, or edited in place). Citations without
an anchor (a language without decisions, or evidence from before anchors) fall back to the same
file and overlapping lines. Each existing rule is claimed at most once per extraction.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
from scrinium.application.knowledge.evidence import decisions_of
from scrinium.domain.enums import EntityLifecycle, EntityType, EvidenceRole
from scrinium.infrastructure.decisions import anchor_for
from scrinium.infrastructure.sqlite.models import (
    EvidenceRecord,
    KnowledgeEntity,
    SourceRevision,
)

_SUPPORTING = (EvidenceRole.SUPPORTS, EvidenceRole.IMPLEMENTS)
_GONE = (EntityLifecycle.RETIRED, EntityLifecycle.DEPRECATED)


@dataclass(frozen=True)
class _Span:
    path: str
    start: int
    end: int
    entity_id: uuid.UUID


@dataclass
class RuleIndex:
    anchors: list[OldAnchor] = field(default_factory=list)
    spans: list[_Span] = field(default_factory=list)
    claimed: set[uuid.UUID] = field(default_factory=set)

    @classmethod
    def load(cls, session: Session, project_id: uuid.UUID) -> "RuleIndex":
        """The latest supporting citation of each live rule in each file."""
        rows = session.execute(
            select(EvidenceRecord)
            .join(KnowledgeEntity, KnowledgeEntity.id == EvidenceRecord.target_entity_id)
            .where(
                EvidenceRecord.project_id == project_id,
                EvidenceRecord.evidence_role.in_(_SUPPORTING),
                KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
                KnowledgeEntity.lifecycle_status.not_in(_GONE),
            )
            .order_by(EvidenceRecord.created_at.desc())
        ).scalars()
        index = cls()
        seen: set[tuple[uuid.UUID, str]] = set()
        for evidence in rows:
            entity_id = evidence.target_entity_id
            path = str(evidence.locator.get("path", ""))
            if entity_id is None or (entity_id, path) in seen:
                continue
            seen.add((entity_id, path))
            anchor = OldAnchor.from_locator(entity_id, evidence.locator)
            if anchor is not None:
                index.anchors.append(anchor)
            else:
                start = int(evidence.locator.get("start_line", 0))
                end = int(evidence.locator.get("end_line", start))
                index.spans.append(_Span(path, start, end, entity_id))
        return index

    def find(self, revision: SourceRevision, path: str, start: int, end: int) -> uuid.UUID | None:
        """The existing rule a citation of lines `start`-`end` belongs to, and claims it."""
        decisions = decisions_of(path, revision)
        ours = anchor_for(decisions, start, end)
        if ours is not None:
            candidates = [
                anchor
                for anchor in self.anchors
                if anchor.key not in self.claimed
                and (anchor.path == path or anchor.shape == ours.shape)
            ]
            for key, match in reanchor(candidates, {path: decisions}).items():
                if match.decision == ours and isinstance(key, uuid.UUID):
                    self.claimed.add(key)
                    return key
        for span in self.spans:
            if (
                span.entity_id not in self.claimed
                and span.path == path
                and span.start <= end
                and span.end >= start
            ):
                self.claimed.add(span.entity_id)
                return span.entity_id
        return None
