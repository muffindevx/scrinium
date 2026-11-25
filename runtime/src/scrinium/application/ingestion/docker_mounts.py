"""The folders `docker/scrinium.sh start <folder>` shares with the container.

The script mounts each folder read-only at /repos/<name> and lists them in
SCRINIUM_DOCKER_MOUNTS as "host<TAB>container" pairs joined by commas (it refuses folder names
with commas or tabs). With that list Scrinium:

- adds each folder as a project with one source when it starts, and syncs it, so the folder
  given to `start` is read without another step;
- accepts the folder's path on the host in the console too, since that is the path the person
  knows, and reads it at its place in the container;
- says that a host folder was not shared, instead of "that folder does not exist".
"""

import logging
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from scrinium.application import workspaces
from scrinium.application.ingestion.policy import SourceKind
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import Project, SourceConnection

ENV: Final = "SCRINIUM_DOCKER_MOUNTS"
logger = logging.getLogger("scrinium.ingestion")


@dataclass(frozen=True)
class Mount:
    host: str
    container: str


def mounts(environ: Mapping[str, str] = os.environ) -> list[Mount] | None:
    """The shared folders, or None outside the container the script starts."""
    raw = environ.get(ENV)
    if raw is None:
        return None
    found = []
    for pair in raw.split(","):
        host, _, container = pair.partition("\t")
        if host.startswith("/") and container.startswith("/"):
            found.append(Mount(host.rstrip("/") or "/", container.rstrip("/")))
    return found


def to_container(path: str, shared: list[Mount]) -> str:
    """A host path inside a shared folder, at its place in the container. Anything else as is."""
    given = PurePosixPath(path)
    for mount in shared:
        host = PurePosixPath(mount.host)
        if given == host or host in given.parents:
            return str(PurePosixPath(mount.container) / given.relative_to(host))
    return path


def is_shared(path: str, shared: list[Mount]) -> bool:
    given = PurePosixPath(path)
    return any(
        given == PurePosixPath(m.container) or PurePosixPath(m.container) in given.parents
        for m in shared
    )


@dataclass(frozen=True)
class Added:
    host: str
    project: str
    # A project made now; otherwise the folder already had one and is only synced again.
    new: bool
    # Read as a plain folder: no Changes, rule history or `scrinium check` without Git.
    git: bool = True


def _source_at(session: Session, root: str) -> SourceConnection | None:
    for connection in session.scalars(select(SourceConnection)):
        if (connection.configuration or {}).get("root") == root:
            return connection
    return None


def _new_project(session: Session, name: str) -> Project:
    workspace = workspaces.local_workspace(session)
    base = workspaces.derive_project_key(name)
    for attempt in range(1, 100):
        key = base if attempt == 1 else f"{base}-{attempt}"
        try:
            with session.begin_nested():
                return workspaces.create_project(
                    session, workspace, name=name, project_key=key, description="",
                    request_id=None,
                )  # fmt: skip
        except workspaces.ProjectKeyTakenError:
            continue
    raise RuntimeError(f"no free project key for {name}")


def _is_git(root: str) -> bool:
    return os.path.exists(os.path.join(root, ".git"))


def _kind_of(root: str) -> SourceKind:
    return SourceKind.GIT_REPOSITORY if _is_git(root) else SourceKind.FOLDER


def register(
    database: Database, submit: Callable[[uuid.UUID], None], data_dir: Path, shared: list[Mount]
) -> list[Added]:
    """Each shared folder as a project with its source, synced now. A folder that already has a
    source is synced again (only what changed is read); one that cannot be added is skipped and
    logged, so Scrinium still starts."""
    from scrinium.application.ingestion import sources  # sources -> plan -> this module

    added, runs = [], []
    # Every folder is written down first and synced after: a sync that starts while the next
    # project is being written would hold the database ("database is locked").
    for mount in shared:
        try:
            with database.session() as session:
                existing = _source_at(session, mount.container)
                if existing is not None:
                    name = session.get_one(Project, existing.project_id).name
                    run = sources.request_sync(session, existing.id, None)
                else:
                    project = _new_project(session, PurePosixPath(mount.container).name)
                    name = project.name
                    _, run = sources.create_source(
                        session, project, _kind_of(mount.container), mount.container, [],
                        data_dir, None,
                    )  # fmt: skip
                run_id = run.id
        except sources.SyncInProgressError:
            added.append(Added(mount.host, name, new=False, git=_is_git(mount.container)))
            continue
        except Exception:
            logger.exception("could not add the shared folder %s", mount.container)
            continue
        runs.append(run_id)
        added.append(Added(mount.host, name, existing is None, _is_git(mount.container)))
    for run_id in runs:
        submit(run_id)
    return added
