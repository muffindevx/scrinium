"""The reviewed rulebook of one source, as rule files (`.scrinium/rules/`, ADR-012).

**Export.** Only what a person reviewed leaves Scrinium. For each rule, its latest version a
person verified or rejected:

- verified: title, statement, WHEN and THEN, the reason (rationale), provenance and the anchors
  of its latest citation in each file of the source;
- rejected: a tombstone, with the reason the person gave and the anchors only.

A version proposed after it (by the model, after a code change) is not exported until a person
reviews it: the file keeps saying what a person agreed to.

**Import** rebuilds that state from the files, with the same ids: each rule gets a version with
the file's text, cited where each anchor's decision is now, and the file's review (who and
when). A tombstone becomes a rejected rule on its decision, so extraction never proposes it
again while its shape is the same. Exporting what was imported gives the same bytes, as long as
the code did not change in between.
"""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.check.changes import KnownRule
from scrinium.application.check.rule_files import REJECTED, VERIFIED, FileAnchor, RuleFile
from scrinium.application.knowledge.anchoring import OldAnchor, reanchor
from scrinium.application.knowledge.common import KnowledgeError
from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import (
    Provenance,
    add_rule_version,
    create_business_rule,
    latest_version,
    set_verification,
)
from scrinium.application.knowledge.evidence import anchored_locator, cite_source, decisions_of
from scrinium.application.knowledge.files import source_of
from scrinium.application.knowledge.watch import latest_citations
from scrinium.domain.enums import (
    EntityLifecycle,
    EntityType,
    EvidenceRole,
    EvidenceType,
    ItemLifecycle,
    RuleCategory,
    VerificationMethod,
    VerificationStatus,
    VersionOrigin,
)
from scrinium.infrastructure.decisions import DECISIONS_VERSION, Decision
from scrinium.infrastructure.sqlite.models import (
    BusinessRuleVersion,
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceConnection,
    SourceItem,
    SourceRevision,
)

_REVIEWED = (VerificationStatus.HUMAN_VERIFIED, VerificationStatus.REJECTED)
TOMBSTONE_TITLE = "Regla rechazada"
_IMPORTER = "importado"


def _timestamp(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _moment(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


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


def _reason(session: Session, version_id: uuid.UUID) -> str | None:
    """What the person wrote when reviewing, if anything (the latest note)."""
    note = session.scalars(
        select(EvidenceRecord.excerpt)
        .where(
            EvidenceRecord.target_version_id == version_id,
            EvidenceRecord.evidence_type == EvidenceType.HUMAN_REVIEW,
        )
        .order_by(EvidenceRecord.created_at.desc())
        .limit(1)
    ).first()
    return " ".join(note.split())[:500] if note else None


def _descriptions(items: list[object]) -> tuple[str, ...]:
    return tuple(
        str(item["description"])
        for item in items
        if isinstance(item, dict) and item.get("description")
    )


def reviewed_rules(session: Session, project_id: uuid.UUID, source_id: uuid.UUID) -> list[RuleFile]:
    """The verified rules and the tombstones of rejected ones cited in this source, by id."""
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
                KnowledgeEntityVersion.verification_status.in_(_REVIEWED),
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
        review = {
            "verified_by": version.reviewed_by,
            "verified_at": _timestamp(version.reviewed_at),
            "anchors": tuple(anchors),
        }
        if version.verification_status is VerificationStatus.REJECTED:
            rules.append(
                RuleFile(
                    id=str(entity.id), title=None, statement="", status=REJECTED,
                    reason=_reason(session, version.id), **review,  # type: ignore[arg-type]
                )
            )  # fmt: skip
            continue
        rules.append(
            RuleFile(
                id=str(entity.id),
                title=content.title,
                statement=content.statement,
                when=_descriptions(content.trigger_conditions),
                then=_descriptions(content.expected_behavior),
                why=content.rationale,
                extractor=version.extractor_version,
                model=version.model_name,
                **review,  # type: ignore[arg-type]
            )
        )
    return rules


def verified_rules(session: Session, project_id: uuid.UUID, source_id: uuid.UUID) -> list[RuleFile]:
    """The verified rules only (no tombstones)."""
    return [r for r in reviewed_rules(session, project_id, source_id) if r.status == VERIFIED]


def export_status(session: Session, project: Project) -> list[dict[str, object]]:
    """For each source of the project: its reviewed rules, how many are not in its
    .scrinium/rules/ as they would be exported (missing or different), and how many files there
    belong to no reviewed rule any more. A source whose folder is not on this machine says so."""
    from scrinium.application.check.rule_files import RULES_DIR, pending
    from scrinium.application.ingestion.sources import settings_of
    from scrinium.application.knowledge.files import source_names

    names = source_names(session, project.id)
    status: list[dict[str, object]] = []
    for source in session.scalars(
        select(SourceConnection)
        .where(SourceConnection.project_id == project.id)
        .order_by(SourceConnection.created_at, SourceConnection.id)
    ):
        try:
            root = settings_of(source).root
        except (KeyError, TypeError):
            continue
        rules = reviewed_rules(session, project.id, source.id)
        available = root.is_dir()
        unexported, extra = pending(root / RULES_DIR, rules) if available else (0, 0)
        status.append(
            {
                "source_id": str(source.id), "name": names.get(source.id, source.display_name),
                "available": available, "reviewed": len(rules), "unexported": unexported,
                "extra": extra,
            }
        )  # fmt: skip
    return status


# --- Import -----------------------------------------------------------------------------------


@dataclass
class ImportResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    # Rules with an anchor whose decision is not in the code now: imported without that place.
    unanchored: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _current(session: Session, source: SourceConnection) -> dict[str, SourceRevision]:
    rows = session.scalars(
        select(SourceRevision)
        .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
        .where(
            SourceItem.source_connection_id == source.id,
            SourceItem.lifecycle_status == ItemLifecycle.ACTIVE,
        )
    )
    return {str(revision.metadata_.get("path", "")): revision for revision in rows}


def _content(rule: RuleFile) -> RuleContent:
    if rule.is_tombstone:
        return RuleContent(
            title=TOMBSTONE_TITLE, statement=rule.reason or "Rechazada al revisarla."
        )
    return RuleContent.model_validate(
        {
            "title": rule.title,
            "statement": rule.statement or rule.title,
            "rationale": rule.why,
            "trigger_conditions": [{"description": item} for item in rule.when],
            "expected_behavior": [{"description": item} for item in rule.then],
        }
    )


def _find(
    session: Session, source: SourceConnection, rule: RuleFile
) -> tuple[list[tuple[SourceRevision, Decision]], bool]:
    """Where each anchor's decision is now, and whether one was not found."""
    current = _current(session, source)
    anchors = [
        OldAnchor(n, a.path, a.symbol, a.kind, a.shape, a.value, a.sketch, a.start_line)
        for n, a in enumerate(rule.anchors)
    ]
    near = {a.path for a in anchors if a.path in current}
    matches = reanchor(anchors, {p: decisions_of(p, current[p]) for p in near})
    lost = [a for a in anchors if a.key not in matches]
    if lost:
        everywhere = {p: decisions_of(p, revision) for p, revision in current.items()}
        matches |= reanchor(lost, everywhere)
    found = [(current[m.path], m.decision) for m in matches.values()]
    return found, len(matches) < len(anchors)


def import_rules(
    session: Session, project: Project, source: SourceConnection, rules: list[RuleFile]
) -> ImportResult:
    result = ImportResult()
    for rule in rules:
        rule_id = uuid.UUID(rule.id)
        entity = session.get(KnowledgeEntity, rule_id)
        if entity is not None and (
            entity.project_id != project.id or entity.entity_type is not EntityType.BUSINESS_RULE
        ):
            result.skipped.append(rule.id)
            continue
        provenance = Provenance(
            VersionOrigin.IMPORT, model_name=rule.model, extractor_version=rule.extractor,
            created_by=rule.verified_by,
        )  # fmt: skip
        content = _content(rule)
        try:
            if entity is None:
                version = create_business_rule(
                    session, project, canonical_key=f"import/{rule.id}",
                    category=RuleCategory.OTHER, content=content, provenance=provenance,
                    entity_id=rule_id,
                )  # fmt: skip
                created = True
            else:
                version, created = add_rule_version(session, project, rule_id, content, provenance)
        except KnowledgeError:
            result.skipped.append(rule.id)
            continue
        version_id = version.knowledge_entity_version_id
        stored = session.get_one(KnowledgeEntityVersion, version_id)
        wanted = (
            VerificationStatus.REJECTED if rule.is_tombstone else VerificationStatus.HUMAN_VERIFIED
        )
        found, lost = _find(session, source, rule)
        for revision, decision in found:
            cite_source(
                session, project, entity_id=rule_id, version_id=version_id,
                revision_id=revision.id, start_line=decision.start_line,
                end_line=decision.end_line, role=EvidenceRole.SUPPORTS,
                evidence_type=EvidenceType.CODE, method=VerificationMethod.DIRECT_REFERENCE,
                decision=decision,
            )  # fmt: skip
        if lost:
            result.unanchored.append(rule.id)
        if stored.verification_status is wanted and not created:
            result.unchanged.append(rule.id)
            continue
        set_verification(
            session, project, version_id, wanted, reviewer=rule.verified_by or _IMPORTER
        )
        stored.reviewed_at = _moment(rule.verified_at) or stored.reviewed_at
        (result.created if entity is None else result.updated).append(rule.id)
    session.flush()
    return result


def known_rules(session: Session, project: Project, source: SourceConnection) -> list[KnownRule]:
    """Every rule of the project with its anchors in this source, at its latest version that
    is not rejected: what the Cambios screen follows between two refs (phase 7.1)."""
    rules = []
    for entity in session.scalars(
        select(KnowledgeEntity).where(
            KnowledgeEntity.project_id == project.id,
            KnowledgeEntity.entity_type == EntityType.BUSINESS_RULE,
            KnowledgeEntity.lifecycle_status.not_in(
                (EntityLifecycle.DEPRECATED, EntityLifecycle.RETIRED)
            ),
        )
    ):
        version = latest_version(session, entity.id)
        if version is None or version.verification_status is VerificationStatus.REJECTED:
            continue
        cited, anchors = _anchors(session, version.id, source.id)
        if cited and anchors:
            rules.append(
                KnownRule(
                    str(entity.id),
                    entity.name,
                    version.verification_status.value,
                    tuple(anchors),
                )
            )
    return rules
