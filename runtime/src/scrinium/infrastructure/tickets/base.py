"""What every ticket tracker client shares (ADR-014): errors, the ticket as Scrinium keeps it, a
page of tickets, and the small surface the sync needs from a client.

Linear (GraphQL) and Jira (REST) implement `TrackerClient`; the sync, the evidence graph and the
console never see which one it is.
"""

import json
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final, Protocol

MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
TIMEOUT_SECONDS: Final = 30.0
# Waits up to this long are slept; longer ones go back to the job queue.
MAX_WAIT_SECONDS: Final = 60.0
# Label names that make an issue a bug where the tracker has no issue type (ADR-014, amendment 4).
BUG_LABELS: Final = frozenset({"bug", "bugs", "error", "defect", "fallo", "incidencia"})
MAX_TEXT: Final = {"title": 500, "status": 100, "label": 100, "url": 500, "id": 100,
                   "description": 20_000, "comment": 5_000}  # fmt: skip
MAX_COMMENTS: Final = 20


class TrackerError(Exception):
    """A tracker call that failed. `code` is stable and safe to store; no message is kept."""

    code = "error"


class TrackerAuthError(TrackerError):
    """The token was rejected (expired, revoked or without access). Not retried."""

    code = "auth"


class TrackerUnavailableError(TrackerError):
    """Network failure, timeout or a server error: worth trying again later."""

    code = "unavailable"


class TrackerRateLimitedError(TrackerUnavailableError):
    """The provider asked to wait longer than Scrinium waits inline."""

    code = "rate_limited"

    def __init__(self, wait_seconds: float) -> None:
        super().__init__()
        self.wait_seconds = wait_seconds


class TrackerResponseError(TrackerError):
    """An answer that is not what the API documents."""

    code = "bad_response"


@dataclass(frozen=True)
class HttpAnswer:
    status: int
    headers: Mapping[str, str]
    body: bytes


@dataclass(frozen=True)
class Team:
    id: str
    key: str
    name: str


@dataclass(frozen=True)
class Issue:
    external_id: str
    key: str
    title: str
    team_id: str
    previous_keys: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    ticket_type: str = ""
    status: str = ""
    status_category: str = ""
    url: str = ""
    opened_at: datetime | None = None
    changed_at: datetime | None = None
    closed_at: datetime | None = None
    trashed: bool = False
    # From the issue's attachments: what the provider itself links (annex C.4, "explicit").
    commits: list[str] = field(default_factory=list)
    pull_requests: list[str] = field(default_factory=list)
    # Only when the connection allows reading text (ADR-014, decision 6): never the authors.
    description: str | None = None
    comments: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IssuePage:
    issues: list[Issue]
    # Nodes that could not be read: counted, never fatal.
    skipped: int
    end_cursor: str | None
    has_next: bool


class TrackerClient(Protocol):
    """What the sync and the console ask of any tracker. Read only, but for `add_comment`, which
    is only called after a person confirms it (ADR-014, decision 1)."""

    requests: int

    def organization_and_teams(self) -> tuple[str, list[Team]]: ...

    def issues(
        self, team_ids: list[str], since: datetime | None, after: str | None
    ) -> IssuePage: ...

    def issue_ids(
        self, team_ids: list[str], after: str | None
    ) -> tuple[list[str], str | None, bool]: ...

    def add_comment(self, issue_id: str, body: str) -> None: ...


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def error_codes(payload: Any) -> set[str]:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list) or not errors:
        return set()
    codes = set()
    for error in errors:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        code = extensions.get("code") if isinstance(extensions, dict) else None
        codes.add(code if isinstance(code, str) else "UNKNOWN")
    return codes


def nodes_of(connection: Any, strict: bool = False) -> list[dict[str, Any]]:
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list):
        if strict:
            raise TrackerResponseError
        return []
    return [node for node in nodes if isinstance(node, dict)]


def page_info(connection: Any) -> tuple[str | None, bool]:
    info = connection.get("pageInfo") if isinstance(connection, dict) else None
    if not isinstance(info, dict):
        return None, False
    cursor = info.get("endCursor")
    cursor = cursor if isinstance(cursor, str) and 0 < len(cursor) <= 500 else None
    return cursor, info.get("hasNextPage") is True and cursor is not None


def clean_text(value: Any, kind: str, lines: bool = False) -> str:
    """A string from the tracker, cut to its kind's length. Control characters out: a title is
    shown as one line; with `lines`, a description keeps its line breaks and nothing else."""
    if not isinstance(value, str):
        return ""
    keep = "\n" if lines else ""
    clean = "".join(char for char in value if char.isprintable() or char in keep).strip()
    return clean[: MAX_TEXT[kind]]


def parse_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number >= 0 and number == number else None
