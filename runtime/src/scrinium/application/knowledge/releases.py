"""Releases of a source and snapshots of what was known about each.

A snapshot pins which version of each entity, and which relationships, applied to a release.
It copies no documents: it points to versions, which never change. So we can later answer what
was known in that release even if the rules have changed.
"""

import re
import uuid
from datetime import datetime
from typing import Final

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from scrinium.application.knowledge.common import (
    KnowledgeError,
    KnowledgeNotFoundError,
    audit,
    content_hash,
    get_in_project,
)
from scrinium.domain.enums import EntityLifecycle, VerificationStatus
from scrinium.infrastructure.sqlite.models import (
    EntityRelationship,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    ReleaseKnowledgeSnapshot,
    ReleaseSnapshotEntry,
    SoftwareRelease,
    SourceConnection,
)

COMMIT_SHA_PATTERN: Final = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
RETRIEVAL_SCHEMA_VERSION: Final = "1"


def register_release(
    session: Session,
    project: Project,
    *,
    source_id: uuid.UUID,
    release_name: str,
    commit_sha: str | None = None,
    repository_key: str | None = None,
    previous_release_id: uuid.UUID | None = None,
    released_at: datetime | None = None,
    request_id: str | None = None,
) -> SoftwareRelease:
    connection = session.get(SourceConnection, source_id)
    if connection is None or connection.project_id != project.id:
        raise KnowledgeNotFoundError
    release_name = release_name.strip()
    if not 0 < len(release_name) <= 200 or not release_name.isprintable():
        raise KnowledgeError("invalid_release_name")
    if commit_sha is not None and not COMMIT_SHA_PATTERN.fullmatch(commit_sha):
        raise KnowledgeError("invalid_commit_sha")
    # A local repository is the whole connection; a remote connector may have several.
    repository_key = repository_key or str(connection.id)
    if previous_release_id is not None:
        previous = get_in_project(session, SoftwareRelease, project, previous_release_id)
        if (previous.source_connection_id, previous.repository_key) != (
            connection.id,
            repository_key,
        ):
            raise KnowledgeError("previous_of_another_repository")
    taken = select(SoftwareRelease.id).where(
        SoftwareRelease.source_connection_id == connection.id,
        SoftwareRelease.repository_key == repository_key,
        SoftwareRelease.release_name == release_name,
    )
    if session.scalar(taken) is not None:
        raise KnowledgeError("release_exists")
    release = SoftwareRelease(
        workspace_id=project.workspace_id,
        project_id=project.id,
        source_connection_id=connection.id,
        repository_key=repository_key,
        release_name=release_name,
        commit_sha=commit_sha,
        previous_release_id=previous_release_id,
        released_at=released_at,
    )
    session.add(release)
    session.flush()
    audit(session, project, "release.register", "software_release", release.id, request_id)
    return release


def _applicable_versions(
    session: Session, project: Project
) -> list[tuple[KnowledgeEntity, KnowledgeEntityVersion]]:
    """The latest non-rejected version of each entity that is not retired."""
    rows = session.execute(
        select(KnowledgeEntity, KnowledgeEntityVersion)
        .join(
            KnowledgeEntityVersion,
            KnowledgeEntityVersion.knowledge_entity_id == KnowledgeEntity.id,
        )
        .where(
            KnowledgeEntity.project_id == project.id,
            KnowledgeEntity.lifecycle_status != EntityLifecycle.RETIRED,
            KnowledgeEntityVersion.verification_status != VerificationStatus.REJECTED,
        )
        .order_by(KnowledgeEntity.id, KnowledgeEntityVersion.version_number.desc())
    ).all()
    latest: dict[uuid.UUID, tuple[KnowledgeEntity, KnowledgeEntityVersion]] = {}
    for entity, version in rows:
        latest.setdefault(entity.id, (entity, version))
    return list(latest.values())


def _relationships_at(session: Session, project: Project, moment: datetime) -> list[list[str]]:
    rows = session.scalars(
        select(EntityRelationship).where(
            EntityRelationship.project_id == project.id,
            or_(EntityRelationship.valid_from.is_(None), EntityRelationship.valid_from <= moment),
            or_(EntityRelationship.valid_until.is_(None), EntityRelationship.valid_until > moment),
        )
    )
    return sorted(
        [str(row.from_entity_id), row.relation_type.value, str(row.to_entity_id)] for row in rows
    )


def latest_snapshot(session: Session, release_id: uuid.UUID) -> ReleaseKnowledgeSnapshot | None:
    return session.scalars(
        select(ReleaseKnowledgeSnapshot)
        .where(ReleaseKnowledgeSnapshot.software_release_id == release_id)
        .order_by(ReleaseKnowledgeSnapshot.snapshot_version.desc())
        .limit(1)
    ).first()


def capture_snapshot(
    session: Session,
    project: Project,
    release_id: uuid.UUID,
    *,
    extractor_version: str,
    request_id: str | None = None,
) -> tuple[ReleaseKnowledgeSnapshot, bool]:
    """Pins the current knowledge for the release. If nothing changed, returns the previous one."""
    release = get_in_project(session, SoftwareRelease, project, release_id)
    entries = _applicable_versions(session, project)
    relationships = _relationships_at(session, project, release.released_at or release.captured_at)
    # The verification status is not hashed: it can change without the content changing.
    digest = content_hash(
        {
            "entries": sorted(
                [str(entity.id), str(version.id), version.content_hash]
                for entity, version in entries
            ),
            "relationships": relationships,
        }
    )
    previous = latest_snapshot(session, release.id)
    if previous is not None and previous.snapshot_hash == digest:
        return previous, False
    snapshot = ReleaseKnowledgeSnapshot(
        workspace_id=project.workspace_id,
        project_id=project.id,
        software_release_id=release.id,
        snapshot_version=1 if previous is None else previous.snapshot_version + 1,
        relationship_snapshot=relationships,
        snapshot_hash=digest,
        extractor_version=extractor_version,
        retrieval_schema_version=RETRIEVAL_SCHEMA_VERSION,
    )
    session.add(snapshot)
    session.flush()
    session.add_all(
        ReleaseSnapshotEntry(
            workspace_id=project.workspace_id,
            project_id=project.id,
            snapshot_id=snapshot.id,
            knowledge_entity_id=entity.id,
            entity_version_id=version.id,
        )
        for entity, version in entries
    )
    session.flush()
    audit(
        session, project, "snapshot.capture", "release_knowledge_snapshot", snapshot.id,
        request_id, {"release_id": str(release.id), "entries": len(entries)},
    )  # fmt: skip
    return snapshot, True


def knowledge_at(
    session: Session, project: Project, snapshot_id: uuid.UUID
) -> list[tuple[KnowledgeEntity, KnowledgeEntityVersion]]:
    """What was known in a release: each entity with the version the snapshot pinned."""
    snapshot = get_in_project(session, ReleaseKnowledgeSnapshot, project, snapshot_id)
    rows = session.execute(
        select(KnowledgeEntity, KnowledgeEntityVersion)
        .join(ReleaseSnapshotEntry, ReleaseSnapshotEntry.knowledge_entity_id == KnowledgeEntity.id)
        .join(
            KnowledgeEntityVersion,
            KnowledgeEntityVersion.id == ReleaseSnapshotEntry.entity_version_id,
        )
        .where(ReleaseSnapshotEntry.snapshot_id == snapshot.id)
        .order_by(KnowledgeEntity.entity_type, KnowledgeEntity.canonical_key)
    ).all()
    return [(entity, version) for entity, version in rows]
