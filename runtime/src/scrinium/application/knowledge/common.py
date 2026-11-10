"""Shared pieces of the knowledge model: errors, hashes and lookups by project."""

import hashlib
import json
import uuid
from typing import Any, Final

from sqlalchemy.orm import Session

from scrinium.application.workspaces import record_audit
from scrinium.domain.enums import AuditResult
from scrinium.infrastructure.sqlite.models import Project, ProjectScoped

METADATA_MAX_KEYS: Final = 20
METADATA_MAX_VALUE: Final = 500
PAYLOAD_MAX_BYTES: Final = 16_000


class KnowledgeError(ValueError):
    """A request the model does not accept. `code` is stable for the API and the UI."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class KnowledgeNotFoundError(LookupError):
    """It does not exist, or belongs to another project: for the caller it is the same."""


def content_hash(data: Any) -> str:
    """SHA-256 of a canonical JSON form: the same content gives the same hash."""
    canonical = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


def get_in_project[Row: ProjectScoped](
    session: Session, model: type[Row], project: Project, key: Any
) -> Row:
    row = session.get(model, key)
    if row is None or row.project_id != project.id:
        raise KnowledgeNotFoundError
    return row


def validate_metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    """Flat, small metadata: short keys and simple values."""
    value = value or {}
    if len(value) > METADATA_MAX_KEYS:
        raise KnowledgeError("metadata_too_large")
    for key, item in value.items():
        if not isinstance(key, str) or not 0 < len(key) <= 64:
            raise KnowledgeError("invalid_metadata")
        if isinstance(item, str) and len(item) > METADATA_MAX_VALUE:
            raise KnowledgeError("metadata_too_large")
        if item is not None and not isinstance(item, str | int | float | bool):
            raise KnowledgeError("invalid_metadata")
    return dict(value)


def validate_payload(value: dict[str, Any]) -> dict[str, Any]:
    """Payload of an entity without a typed table: JSON-serializable and size-bounded."""
    try:
        size = len(json.dumps(value, ensure_ascii=False).encode())
    except (TypeError, ValueError):
        raise KnowledgeError("invalid_payload") from None
    if size > PAYLOAD_MAX_BYTES:
        raise KnowledgeError("payload_too_large")
    return value


def audit(
    session: Session,
    project: Project,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID,
    request_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Only identifiers and closed vocabulary: never the text of a rule or a source."""
    record_audit(
        session, project.workspace_id, action, resource_type, str(resource_id),
        AuditResult.SUCCESS, request_id, {"project_id": str(project.id), **(metadata or {})},
    )  # fmt: skip
