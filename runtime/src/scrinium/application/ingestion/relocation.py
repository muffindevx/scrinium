"""A source whose folder moved: find it again and point the source at it.

The common case is a disk mounted under another name (`/media/me/DISK` becomes
`/media/me/DISK1`): the folder is the same, only the start of its path changed. Scrinium keeps
each file by its path inside the source, so the source can change its root and keep its rules,
reviews and history.
"""

import uuid
from pathlib import Path
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application.check.git import GitError, git
from scrinium.application.ingestion.plan import SourceError, resolve_root
from scrinium.application.ingestion.policy import SourceKind
from scrinium.application.ingestion.sources import _connection, settings_of
from scrinium.application.workspaces import record_audit
from scrinium.domain.enums import AuditResult, SyncStatus
from scrinium.infrastructure.sqlite.models import SourceConnection, SyncRun

# How many folders next to each parent are looked at, and how many answers are given.
_SIBLINGS: Final = 200
_CANDIDATES: Final = 3


class RelocationError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def indexed_commit(session: Session, connection: SourceConnection) -> str | None:
    commit: str | None = session.scalars(
        select(SyncRun.cursor_after)
        .where(
            SyncRun.source_connection_id == connection.id,
            SyncRun.status == SyncStatus.SUCCEEDED,
        )
        .order_by(SyncRun.created_at.desc())
        .limit(1)
    ).first()
    return commit


def same_source(kind: SourceKind, folder: Path, commit: str | None) -> bool:
    """Whether `folder` holds what the source read: for Git, a repository with its last commit."""
    try:
        if not folder.is_dir():
            return False
    except OSError:  # a folder this user cannot enter, e.g. another user's under /tmp
        return False
    if kind is not SourceKind.GIT_REPOSITORY:
        return True
    try:
        if commit:
            git(folder, "cat-file", "-e", f"{commit}^{{commit}}")
        else:
            git(folder, "rev-parse", "--git-dir")
    except GitError:
        return False
    return True


def candidates(kind: SourceKind, old: Path, commit: str | None) -> list[Path]:
    """Folders where a missing `old` may be now: the same tail under a sibling of one of its
    parents (`/media/me/DISK/work/app` → `/media/me/DISK1/work/app`). Empty if `old` exists."""
    if old.is_dir() or not old.is_absolute():
        return []
    found: list[Path] = []
    # From the nearest parent up, never the filesystem root itself.
    for parent in list(old.parents)[:-1]:
        tail = old.relative_to(parent)
        holder = parent.parent
        try:
            siblings = sorted(holder.iterdir())[:_SIBLINGS]
        except OSError:
            continue
        for sibling in siblings:
            if sibling == parent:
                continue
            candidate = sibling / tail
            if candidate not in found and same_source(kind, candidate, commit):
                found.append(candidate)
                if len(found) >= _CANDIDATES:
                    return found
        if found:
            return found
    return found


def suggested_root(session: Session, connection: SourceConnection) -> str | None:
    """The first place the missing folder seems to be now, or None."""
    settings = settings_of(connection)
    if (connection.configuration or {}).get("remote_url"):
        return None
    found = candidates(settings.kind, settings.root, indexed_commit(session, connection))
    return str(found[0]) if found else None


def relocate(
    session: Session, source_id: uuid.UUID, raw_path: str, data_dir: Path, request_id: str | None
) -> SourceConnection:
    """Points the source at `raw_path` if it holds the same folder; keeps everything else."""
    connection = _connection(session, source_id)
    settings = settings_of(connection)
    try:
        root = resolve_root(settings.kind, raw_path, data_dir)
    except SourceError as error:
        raise RelocationError(error.code) from None
    if not same_source(settings.kind, root, indexed_commit(session, connection)):
        raise RelocationError("not_same_source")
    old = str(settings.root)
    connection.configuration = {**connection.configuration, "root": str(root)}
    record_audit(
        session, connection.workspace_id, "source.relocate", "source", str(connection.id),
        AuditResult.SUCCESS, request_id,
        {"project_id": str(connection.project_id), "moved": old != str(root)},
    )  # fmt: skip
    return connection
