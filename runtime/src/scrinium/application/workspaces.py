"""Local workspace, projects and audit log."""

import re
import unicodedata
import uuid
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scrinium.domain.enums import AuditResult, WorkspaceType
from scrinium.infrastructure.sqlite.models import (
    AuditEvent,
    LocalProfile,
    Project,
    SourceConnection,
    Workspace,
)

DEFAULT_WORKSPACE_NAME: Final = "Workspace personal"
PROJECT_KEY_PATTERN: Final = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
PROJECT_KEY_MAX_LENGTH: Final = 64
_FALLBACK_KEY: Final = "proyecto"
INCLUDE_TESTS_KEY: Final = "search_include_tests"


class ProjectKeyTakenError(Exception):
    pass


def record_audit(
    session: Session,
    workspace_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: str | None,
    result: AuditResult,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Adds an event. `metadata` must never carry secrets or source content."""
    session.add(
        AuditEvent(
            workspace_id=workspace_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
            request_id=request_id,
            metadata_=metadata or {},
        )
    )


def ensure_local_workspace(session: Session) -> Workspace:
    """Creates, on first start, the MVP's single workspace and the local profile."""
    workspace = session.scalars(select(Workspace).order_by(Workspace.created_at)).first()
    if workspace is None:
        workspace = Workspace(
            name=DEFAULT_WORKSPACE_NAME, workspace_type=WorkspaceType.PERSONAL_LOCAL
        )
        session.add(workspace)
        session.flush()
        record_audit(
            session,
            workspace.id,
            "workspace.create",
            "workspace",
            str(workspace.id),
            AuditResult.SUCCESS,
        )
    if session.get(LocalProfile, 1) is None:
        session.add(LocalProfile())
    return workspace


def local_workspace(session: Session) -> Workspace:
    workspace = session.scalars(select(Workspace).order_by(Workspace.created_at)).first()
    if workspace is None:
        raise LookupError("local workspace not created")
    return workspace


def search_includes_tests(session: Session) -> bool:
    """Whether search, answers and vectors also cover tests. Off by default: tests check
    rules, they do not explain them, and they add noise and embedding time."""
    workspace = session.scalars(select(Workspace).order_by(Workspace.created_at)).first()
    return bool(workspace is not None and workspace.settings.get(INCLUDE_TESTS_KEY, False))


def set_search_includes_tests(session: Session, value: bool) -> None:
    workspace = local_workspace(session)
    # A new dict: SQLAlchemy does not see changes made inside a JSON value.
    workspace.settings = {**workspace.settings, INCLUDE_TESTS_KEY: value}


def list_projects(session: Session, workspace: Workspace) -> list[Project]:
    return list(
        session.scalars(
            select(Project)
            .where(Project.workspace_id == workspace.id)
            .order_by(Project.created_at, Project.id)
        )
    )


def count_connections(session: Session, workspace: Workspace) -> int:
    query = select(func.count()).where(SourceConnection.workspace_id == workspace.id)
    return session.scalar(query) or 0


def derive_project_key(name: str) -> str:
    """A readable key from the name: "Pagos Móvil" -> "pagos-movil"."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    key = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    return key[:PROJECT_KEY_MAX_LENGTH].rstrip("-") or _FALLBACK_KEY


def create_project(
    session: Session,
    workspace: Workspace,
    *,
    name: str,
    project_key: str | None,
    description: str,
    request_id: str | None,
) -> Project:
    key = project_key or derive_project_key(name)
    taken = select(Project.id).where(
        Project.workspace_id == workspace.id, Project.project_key == key
    )
    if session.scalar(taken) is not None:
        raise ProjectKeyTakenError
    project = Project(
        workspace_id=workspace.id, name=name, project_key=key, description=description
    )
    session.add(project)
    try:
        session.flush()
    except IntegrityError as error:
        raise ProjectKeyTakenError from error
    record_audit(
        session,
        workspace.id,
        "project.create",
        "project",
        str(project.id),
        AuditResult.SUCCESS,
        request_id,
        {"project_key": key},
    )
    return project


def reviewer_name(session: Session) -> str | None:
    """Who reviews on this machine: the local profile name. It is not an account."""
    profile = session.get(LocalProfile, 1)
    return profile.display_name if profile is not None and profile.display_name else None
