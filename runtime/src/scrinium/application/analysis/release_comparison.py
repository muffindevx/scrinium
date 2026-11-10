"""Releases of a local repository and comparison between two of them.

Discovery reads the repository's tags without changing it. A snapshot is only captured for the
release matching the indexed commit: capturing an old tag's knowledge now would
mix current rules with a past version.

Comparison does not use the LLM: it crosses the snapshots with the files that change between
the two commits. Each rule change is classified (added, modified, retired) and assessed
(supported, unverifiable, contradicted), with the evidence on each side.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.analysis.runs import finish_run
from scrinium.application.ingestion.policy import SourceKind
from scrinium.application.ingestion.sources import PROVIDERS, settings_of
from scrinium.application.knowledge.common import (
    KnowledgeError,
    KnowledgeNotFoundError,
    get_in_project,
)
from scrinium.application.knowledge.evidence import record_finding
from scrinium.application.knowledge.releases import (
    capture_snapshot,
    latest_snapshot,
    register_release,
)
from scrinium.domain.enums import (
    AnalysisStatus,
    Criticality,
    EvidenceRole,
    FindingType,
    RelationType,
    ReleaseStatus,
    Severity,
    SyncStatus,
    VerificationMethod,
    VerificationStatus,
)
from scrinium.infrastructure.local_files.git_reader import GitError, GitRepository
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    BusinessRule,
    EvidenceRecord,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    ReleaseKnowledgeSnapshot,
    ReleaseSnapshotEntry,
    SoftwareRelease,
    SourceConnection,
    SyncRun,
)

MAX_CHANGED_FILES: Final = 2000
MAX_FINDINGS: Final = 50
MAX_EVIDENCE_PER_SIDE: Final = 10
SEVERITY_BY_CRITICALITY: Final = {
    Criticality.LOW: Severity.LOW,
    Criticality.MEDIUM: Severity.MEDIUM,
    Criticality.HIGH: Severity.HIGH,
    Criticality.CRITICAL: Severity.HIGH,
}
LOCAL_GIT: Final = PROVIDERS[SourceKind.GIT_REPOSITORY]


def indexed_commit(session: Session, connection_id: uuid.UUID) -> str | None:
    """The commit of the last finished sync: what is indexed today."""
    return session.scalar(
        select(SyncRun.cursor_after)
        .where(
            SyncRun.source_connection_id == connection_id,
            SyncRun.status == SyncStatus.SUCCEEDED,
        )
        .order_by(SyncRun.finished_at.desc())
        .limit(1)
    )


def _git_source(session: Session, project: Project, source_id: uuid.UUID) -> SourceConnection:
    connection = session.get(SourceConnection, source_id)
    if connection is None or connection.project_id != project.id:
        raise KnowledgeNotFoundError
    if connection.provider != LOCAL_GIT:
        raise KnowledgeError("not_a_git_source")
    return connection


@dataclass(frozen=True)
class Discovery:
    created: list[uuid.UUID]
    existing: int


def discover_releases(
    database: Database, project_id: uuid.UUID, source_id: uuid.UUID, request_id: str | None
) -> Discovery:
    """Records the missing tags, in date order and chained to the previous one."""
    with database.session() as session:
        project = session.get_one(Project, project_id)
        root = settings_of(_git_source(session, project, source_id)).root
    # Git outside the transaction: SQLite is not locked while it reads the repository.
    try:
        with GitRepository(root) as repository:
            repository.check_root()
            tags = sorted(repository.tags(), key=lambda tag: (tag.created_at, tag.name))
    except GitError as error:
        raise KnowledgeError(error.code.value) from None

    created, existing = [], 0
    with database.session() as session:
        project = session.get_one(Project, project_id)
        known = {
            release.release_name: release
            for release in session.scalars(
                select(SoftwareRelease).where(SoftwareRelease.source_connection_id == source_id)
            )
        }
        previous: SoftwareRelease | None = None
        for tag in tags:
            release = known.get(tag.name)
            if release is not None:
                existing += 1
            else:
                try:
                    release = register_release(
                        session, project, source_id=source_id, release_name=tag.name,
                        commit_sha=tag.commit,
                        released_at=datetime.fromtimestamp(tag.created_at, UTC),
                        previous_release_id=previous.id if previous is not None else None,
                        request_id=request_id,
                    )  # fmt: skip
                except KnowledgeError:
                    continue  # a name the model does not accept (for example, not printable)
                created.append(release.id)
            previous = release
    return Discovery(created, existing)


def snapshot_release(
    session: Session,
    project: Project,
    release_id: uuid.UUID,
    extractor_version: str,
    request_id: str | None,
) -> ReleaseKnowledgeSnapshot:
    release = get_in_project(session, SoftwareRelease, project, release_id)
    commit = indexed_commit(session, release.source_connection_id)
    if release.commit_sha is None or release.commit_sha != commit:
        raise KnowledgeError("release_not_indexed")
    snapshot, _ = capture_snapshot(
        session, project, release.id, extractor_version=extractor_version, request_id=request_id
    )
    release.status = ReleaseStatus.READY
    return snapshot


def _entries(session: Session, snapshot: ReleaseKnowledgeSnapshot | None) -> dict[uuid.UUID, Any]:
    if snapshot is None:
        return {}
    rows = session.execute(
        select(ReleaseSnapshotEntry.knowledge_entity_id, KnowledgeEntityVersion)
        .join(
            KnowledgeEntityVersion,
            KnowledgeEntityVersion.id == ReleaseSnapshotEntry.entity_version_id,
        )
        .where(ReleaseSnapshotEntry.snapshot_id == snapshot.id)
    ).all()
    return {entity_id: version for entity_id, version in rows}  # noqa: C416


def _evidence(session: Session, version_id: uuid.UUID) -> list[dict[str, Any]]:
    records = session.scalars(
        select(EvidenceRecord)
        .where(EvidenceRecord.target_version_id == version_id)
        .order_by(EvidenceRecord.created_at)
        .limit(MAX_EVIDENCE_PER_SIDE)
    )
    return [
        {
            "evidence_id": str(r.id), "role": r.evidence_role.value,
            "type": r.evidence_type.value, "method": r.verification_method.value,
            "status": r.verification_status.value, "path": r.locator.get("path"),
            "commit": r.locator.get("commit"), "start_line": r.locator.get("start_line"),
            "end_line": r.locator.get("end_line"), "revision_id": str(r.source_revision_id)
            if r.source_revision_id else None,
        }
        for r in records
    ]  # fmt: skip


def _assessment(
    session: Session, version: KnowledgeEntityVersion, contradicted: set[uuid.UUID]
) -> str:
    contrary = session.scalar(
        select(EvidenceRecord.id).where(
            EvidenceRecord.target_version_id == version.id,
            EvidenceRecord.evidence_role == EvidenceRole.CONTRADICTS,
            EvidenceRecord.verification_method != VerificationMethod.LLM_PROPOSAL,
        )
    )
    if (
        version.verification_status is VerificationStatus.CONFLICTED
        or contrary is not None
        or version.knowledge_entity_id in contradicted
    ):
        return "contradictory"
    if version.verification_status is VerificationStatus.UNVERIFIED:
        return "unverifiable"
    return "supported"


def _describe(session: Session, entity_id: uuid.UUID) -> dict[str, Any]:
    entity = session.get_one(KnowledgeEntity, entity_id)
    return {
        "entity_id": str(entity.id), "entity_type": entity.entity_type.value,
        "canonical_key": entity.canonical_key, "name": entity.name,
    }  # fmt: skip


def compare_knowledge(
    session: Session,
    base: ReleaseKnowledgeSnapshot | None,
    target: ReleaseKnowledgeSnapshot | None,
) -> dict[str, Any]:
    if base is None or target is None:
        return {"available": False, "changes": [], "relationships": {"added": [], "removed": []}}
    before, after = _entries(session, base), _entries(session, target)
    target_relations = {tuple(r) for r in target.relationship_snapshot}
    base_relations = {tuple(r) for r in base.relationship_snapshot}
    contradicted = {
        uuid.UUID(node)
        for start, kind, end in target_relations
        if kind == RelationType.CONTRADICTS.value
        for node in (start, end)
    }
    changes = []
    for entity_id in sorted(before.keys() | after.keys(), key=str):
        old, new = before.get(entity_id), after.get(entity_id)
        if old is not None and new is not None and old.id == new.id:
            continue
        change = "added" if old is None else "retired" if new is None else "modified"
        changes.append(
            {
                **_describe(session, entity_id), "change": change,
                "assessment": _assessment(session, new, contradicted) if new else "retired",
                "base_version": old.version_number if old else None,
                "target_version": new.version_number if new else None,
                "base_evidence": _evidence(session, old.id) if old else [],
                "target_evidence": _evidence(session, new.id) if new else [],
            }
        )  # fmt: skip
    return {
        "available": True,
        "unchanged": sum(1 for e in before.keys() & after.keys() if before[e].id == after[e].id),
        "changes": changes,
        "relationships": {
            "added": sorted(list(r) for r in target_relations - base_relations),
            "removed": sorted(list(r) for r in base_relations - target_relations),
        },
    }


def _rules_citing(session: Session, project_id: uuid.UUID, paths: set[str]) -> dict[str, Any]:
    """Rules with evidence in those files: the code backing them has changed."""
    rows = session.execute(
        select(EvidenceRecord, BusinessRule)
        .join(BusinessRule, BusinessRule.knowledge_entity_id == EvidenceRecord.target_entity_id)
        .where(EvidenceRecord.project_id == project_id)
    ).all()
    touched: dict[str, Any] = {}
    for record, rule in rows:
        path = record.locator.get("path")
        if path not in paths:
            continue
        item = touched.setdefault(
            str(rule.knowledge_entity_id),
            {**_describe(session, rule.knowledge_entity_id), "criticality": rule.criticality,
             "paths": set()},
        )  # fmt: skip
        item["paths"].add(path)
    return touched


def compare_releases(
    database: Database, run_id: uuid.UUID, base_id: uuid.UUID, target_id: uuid.UUID
) -> None:
    with database.session() as session:
        run = session.get_one(AnalysisRun, run_id)
        project = session.get_one(Project, run.project_id)
        base = get_in_project(session, SoftwareRelease, project, base_id)
        target = get_in_project(session, SoftwareRelease, project, target_id)
        same_source = base.source_connection_id == target.source_connection_id
        root = None
        connection = session.get(SourceConnection, target.source_connection_id)
        if same_source and connection is not None and connection.provider == LOCAL_GIT:
            root = settings_of(connection).root
        shas = base.commit_sha, target.commit_sha

    limitations: list[str] = []
    changed: list[dict[str, str]] = []
    code_status = "unavailable"
    if root is not None and shas[0] and shas[1]:
        try:
            with GitRepository(root) as repository:
                files = repository.changed_files(shas[0], shas[1])
            code_status = "complete" if len(files) <= MAX_CHANGED_FILES else "truncated"
            changed = [{"status": f.status, "path": f.path} for f in files[:MAX_CHANGED_FILES]]
        except GitError:
            code_status = "failed"
            limitations.append("No se pudo leer el repositorio para comparar los commits.")
    else:
        limitations.append(
            "Sin commits de las dos releases en el mismo repositorio local: no hay comparación "
            "de código."
        )

    with database.session() as session:
        run = session.get_one(AnalysisRun, run_id)
        project = session.get_one(Project, run.project_id)
        base_snapshot = latest_snapshot(session, base_id)
        target_snapshot = latest_snapshot(session, target_id)
        knowledge = compare_knowledge(session, base_snapshot, target_snapshot)
        if not knowledge["available"]:
            missing = [
                name
                for name, snapshot in (("base", base_snapshot), ("objetivo", target_snapshot))
                if snapshot is None
            ]
            limitations.append(
                f"Falta el snapshot de conocimiento de la release {' y '.join(missing)}: no se "
                "pueden comparar sus reglas. Se captura al indexar el commit de esa release."
            )
        if code_status == "truncated":
            limitations.append(f"Solo se consideran los primeros {MAX_CHANGED_FILES} archivos.")

        findings = 0
        changed_rules = {c["entity_id"] for c in knowledge["changes"]}
        for change in knowledge["changes"]:
            if change["assessment"] != "contradictory" or findings >= MAX_FINDINGS:
                continue
            record_finding(
                session, run, finding_type=FindingType.CONTRADICTION,
                title=f"Contradicción en {change['name']}"[:300],
                description="La versión de la release objetivo tiene evidencia o relaciones que "
                "la contradicen.", severity=Severity.MEDIUM,
                location={"entity_id": change["entity_id"]},
                next_step="Revisa la evidencia en contra y decide qué versión es la vigente.",
            )  # fmt: skip
            findings += 1
        touched = _rules_citing(session, project.id, {c["path"] for c in changed})
        affected = []
        known = bool(knowledge["available"])
        for entity_id, item in touched.items():
            paths = sorted(item.pop("paths"))
            criticality = item.pop("criticality")
            # Without both snapshots we cannot know whether the rule changed: None, not False.
            knowledge_changed = entity_id in changed_rules if known else None
            affected.append({**item, "paths": paths, "knowledge_changed": knowledge_changed})
            if knowledge_changed or findings >= MAX_FINDINGS:
                continue
            # The code backing it changed; the rule did not, or we do not know.
            description = (
                "Cambiaron archivos que respaldan esta regla, pero la regla es la misma en los "
                "dos snapshots."
                if known
                else "Cambiaron archivos que respaldan esta regla. Sin el snapshot de las dos "
                "releases no se sabe si la regla cambió con ellos."
            )
            record_finding(
                session, run, finding_type=FindingType.IMPACT_RISK,
                title=f"El código de {item['name']} cambió entre releases"[:300],
                description=description, severity=SEVERITY_BY_CRITICALITY[criticality],
                location={"entity_id": entity_id, "paths": paths},
                next_step="Comprueba si la regla sigue siendo cierta en la release objetivo.",
            )  # fmt: skip
            findings += 1
        result = {
            "base": {"release_id": str(base_id), "commit": shas[0],
                     "snapshot_id": str(base_snapshot.id) if base_snapshot else None},
            "target": {"release_id": str(target_id), "commit": shas[1],
                       "snapshot_id": str(target_snapshot.id) if target_snapshot else None},
            "knowledge": knowledge,
            "code": {"status": code_status, "changed_files": changed},
            "rules_with_changed_code": affected,
            "findings": findings,
            "limitations": limitations,
        }  # fmt: skip
    status = AnalysisStatus.COMPLETED if not limitations else AnalysisStatus.PARTIAL
    finish_run(database, run_id, status, result)
