"""Linear's GraphQL API, read-only (ADR-014).

- One endpoint, `https://api.linear.app/graphql`, over HTTPS with certificate checks and no
  redirects: a redirect would carry the token to whatever host it names.
- The token is asked for on every request and never kept here. A personal API key goes as it is
  in `Authorization`; an OAuth token goes after `Bearer`.
- Only queries: nothing here can change a ticket.
- Linear signals rate limits with HTTP 429 or with a GraphQL error `RATELIMITED` (ADR-014,
  amendment 2). Short waits are slept here; a long one is raised to the caller, which leaves
  the job to the queue.
- The answer is untrusted input: `parse_issue` accepts any JSON and returns None for a node it
  cannot read, instead of failing the whole page.
- Errors never carry what was sent or received, nor the token.
"""

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

LINEAR_HOST: Final = "api.linear.app"
ENDPOINT: Final = f"https://{LINEAR_HOST}/graphql"
MAX_RESPONSE_BYTES: Final = 8 * 1024 * 1024
TIMEOUT_SECONDS: Final = 30.0
# Small pages keep each query under Linear's complexity limit with labels and attachments.
PAGE_SIZE: Final = 50
SWEEP_PAGE_SIZE: Final = 250
# Waits up to this long are slept; longer ones go back to the job queue.
MAX_WAIT_SECONDS: Final = 60.0
_MAX_RETRIES: Final = 3

# Label names that make a Linear issue a bug (ADR-014, amendment 4).
BUG_LABELS: Final = frozenset({"bug", "bugs", "error", "defect", "fallo", "incidencia"})
# Linear's workflow state types, as categories every tracker can share.
_CATEGORIES: Final = {
    "triage": "open", "backlog": "open", "unstarted": "open",
    "started": "started", "completed": "done", "canceled": "canceled",
}  # fmt: skip
_COMMIT_URL: Final = re.compile(r"/commits?/([0-9a-f]{40})(?![0-9a-f])")
_PULL_URL: Final = re.compile(r"/(?:pull|pulls|merge_requests)/\d+(?:$|[/?#])")
_KEY: Final = re.compile(r"^[A-Z][A-Z0-9]{0,9}-[1-9]\d{0,8}$")
_MAX_TEXT: Final = {"title": 500, "status": 100, "label": 100, "url": 500, "id": 100}


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


Transport = Callable[[bytes, dict[str, str]], HttpAnswer]


@dataclass(frozen=True)
class Team:
    id: str
    key: str
    name: str


@dataclass(frozen=True)
class LinearIssue:
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


@dataclass(frozen=True)
class IssuePage:
    issues: list[LinearIssue]
    # Nodes that could not be read: counted, never fatal.
    skipped: int
    end_cursor: str | None
    has_next: bool


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def https_transport() -> Transport:
    """POSTs to Linear's endpoint, honouring HTTPS_PROXY, never following a redirect."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect()
    )

    def send(body: bytes, headers: dict[str, str]) -> HttpAnswer:
        request = urllib.request.Request(ENDPOINT, data=body, headers=headers, method="POST")
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                return HttpAnswer(response.status, dict(response.headers.items()), raw)
        except urllib.error.HTTPError as error:
            try:
                raw = error.read(MAX_RESPONSE_BYTES + 1)
                return HttpAnswer(error.code, dict(error.headers.items()), raw)
            except OSError:
                return HttpAnswer(error.code, dict(error.headers.items()), b"")
            finally:
                error.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TrackerUnavailableError from None

    return send


def authorization(token: str, auth: str) -> str:
    """Linear takes a personal API key as it is, and an OAuth token after `Bearer`."""
    return f"Bearer {token}" if auth == "oauth" else token


_TEAMS_QUERY: Final = """
query Teams {
  organization { name }
  teams(first: 100) { nodes { id key name } }
}
"""

_ISSUES_QUERY: Final = """
query Issues($filter: IssueFilter, $after: String, $first: Int!) {
  issues(first: $first, after: $after, includeArchived: true, filter: $filter,
         orderBy: updatedAt) {
    nodes {
      id identifier previousIdentifiers title url
      createdAt updatedAt completedAt canceledAt trashed
      state { name type }
      team { id }
      labels(first: 20) { nodes { name } }
      attachments(first: 20) { nodes { url } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

_SWEEP_QUERY: Final = """
query Sweep($filter: IssueFilter, $after: String, $first: Int!) {
  issues(first: $first, after: $after, includeArchived: true, filter: $filter) {
    nodes { id }
    pageInfo { hasNextPage endCursor }
  }
}
"""


class LinearClient:
    def __init__(
        self,
        token: Callable[[], str],
        auth: str = "api_key",
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ) -> None:
        """`token` is called for every request: that is where the keyring is read."""
        self._token = token
        self._auth = auth
        self._transport = transport or https_transport()
        self._sleep = sleep
        self._now = now
        self.requests = 0

    def organization_and_teams(self) -> tuple[str, list[Team]]:
        data = self.query(_TEAMS_QUERY, {})
        organization = data.get("organization")
        name = _text(organization.get("name"), "title") if isinstance(organization, dict) else ""
        teams = []
        for node in _nodes(data.get("teams")):
            team_id, key = _text(node.get("id"), "id"), _text(node.get("key"), "label")
            if team_id and key:
                teams.append(Team(team_id, key, _text(node.get("name"), "title") or key))
        return name or "", teams

    def issues(self, team_ids: list[str], since: datetime | None, after: str | None) -> IssuePage:
        """One page of the issues of these teams changed at or after `since`, archived ones
        included. With `since` None, all of them (the first sync)."""
        filters: dict[str, Any] = {"team": {"id": {"in": team_ids}}}
        if since is not None:
            filters["updatedAt"] = {"gte": since.astimezone(UTC).isoformat()}
        data = self.query(_ISSUES_QUERY, {"filter": filters, "after": after, "first": PAGE_SIZE})
        connection = data.get("issues")
        raw = connection.get("nodes") if isinstance(connection, dict) else None
        if not isinstance(raw, list):
            raise TrackerResponseError
        issues = [issue for node in raw if (issue := parse_issue(node)) is not None]
        skipped = len(raw) - len(issues)
        cursor, has_next = _page_info(connection)
        return IssuePage(issues, skipped, cursor, has_next)

    def issue_ids(
        self, team_ids: list[str], after: str | None
    ) -> tuple[list[str], str | None, bool]:
        """One page of the ids of every issue still visible in these teams (the sweep)."""
        filters = {"team": {"id": {"in": team_ids}}}
        data = self.query(_SWEEP_QUERY, {"filter": filters, "after": after,
                                         "first": SWEEP_PAGE_SIZE})  # fmt: skip
        connection = data.get("issues")
        ids = [
            issue_id
            for node in _nodes(connection, strict=True)
            if (issue_id := _text(node.get("id"), "id"))
        ]
        cursor, has_next = _page_info(connection)
        return ids, cursor, has_next

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode()
        for attempt in range(_MAX_RETRIES + 1):
            headers = {
                "Content-Type": "application/json",
                "Authorization": authorization(self._token(), self._auth),
            }
            self.requests += 1
            answer = self._transport(body, headers)
            wait = self._rate_limit_wait(answer)
            if wait is None:
                return self._data(answer)
            if wait > MAX_WAIT_SECONDS or attempt == _MAX_RETRIES:
                raise TrackerRateLimitedError(wait)
            self._sleep(wait)
        raise TrackerRateLimitedError(MAX_WAIT_SECONDS)  # pragma: no cover

    def _rate_limit_wait(self, answer: HttpAnswer) -> float | None:
        """Seconds to wait if this answer is a rate limit; None if it is not one."""
        limited = answer.status == 429 or "RATELIMITED" in _error_codes(_json(answer.body))
        if not limited:
            return None
        headers = {name.lower(): value for name, value in answer.headers.items()}
        waits = [1.0]
        retry_after = _number(headers.get("retry-after"))
        if retry_after is not None:
            waits.append(retry_after)
        for name in ("x-ratelimit-requests-reset", "x-ratelimit-complexity-reset"):
            reset = _number(headers.get(name))
            # Milliseconds since the epoch, when the window opens again.
            if reset is not None and reset > 1e11:
                waits.append(reset / 1000 - self._now())
        return min(max(waits), 3600.0)

    def _data(self, answer: HttpAnswer) -> dict[str, Any]:
        if len(answer.body) > MAX_RESPONSE_BYTES:
            raise TrackerResponseError
        payload = _json(answer.body)
        codes = _error_codes(payload)
        if answer.status in (401, 403) or codes & {"AUTHENTICATION_ERROR", "FORBIDDEN"}:
            raise TrackerAuthError
        if answer.status >= 500 or answer.status in (408, 425):
            raise TrackerUnavailableError
        if answer.status != 200 or codes or not isinstance(payload, dict):
            raise TrackerResponseError
        data = payload.get("data")
        if not isinstance(data, dict):
            raise TrackerResponseError
        return data


def parse_issue(node: Any) -> LinearIssue | None:
    """A Linear issue node, or None if its identity is missing or malformed."""
    if not isinstance(node, dict):
        return None
    external_id = _text(node.get("id"), "id")
    key = _text(node.get("identifier"), "label")
    title = _text(node.get("title"), "title")
    team = node.get("team")
    team_id = _text(team.get("id"), "id") if isinstance(team, dict) else ""
    if not external_id or not _KEY.match(key) or not team_id:
        return None
    previous = node.get("previousIdentifiers")
    previous_keys = [
        old for old in (previous if isinstance(previous, list) else [])
        if isinstance(old, str) and _KEY.match(old) and old != key
    ][:20]  # fmt: skip
    labels = [
        label for item in _nodes(node.get("labels"))
        if (label := _text(item.get("name"), "label"))
    ]  # fmt: skip
    raw_state = node.get("state")
    state: dict[str, Any] = raw_state if isinstance(raw_state, dict) else {}
    raw_type = state.get("type")
    state_type = raw_type if isinstance(raw_type, str) else ""
    commits: list[str] = []
    pulls: list[str] = []
    for attachment in _nodes(node.get("attachments")):
        url = _text(attachment.get("url"), "url")
        if not url.startswith("https://"):
            continue
        if match := _COMMIT_URL.search(url):
            commits.append(match.group(1))
        elif _PULL_URL.search(url):
            pulls.append(url)
    closed = _date(node.get("completedAt")) or _date(node.get("canceledAt"))
    url = _text(node.get("url"), "url")
    return LinearIssue(
        external_id=external_id,
        key=key,
        title=title,
        team_id=team_id,
        previous_keys=list(dict.fromkeys(previous_keys)),
        labels=labels,
        ticket_type="bug" if any(label.casefold() in BUG_LABELS for label in labels) else "",
        status=_text(state.get("name"), "status"),
        status_category=_CATEGORIES.get(state_type, ""),
        url=url if url.startswith("https://") else "",
        opened_at=_date(node.get("createdAt")),
        changed_at=_date(node.get("updatedAt")),
        closed_at=closed,
        trashed=node.get("trashed") is True,
        commits=list(dict.fromkeys(commits)),
        pull_requests=list(dict.fromkeys(pulls)),
    )


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        return None


def _error_codes(payload: Any) -> set[str]:
    errors = payload.get("errors") if isinstance(payload, dict) else None
    if not isinstance(errors, list) or not errors:
        return set()
    codes = set()
    for error in errors:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        code = extensions.get("code") if isinstance(extensions, dict) else None
        codes.add(code if isinstance(code, str) else "UNKNOWN")
    return codes


def _nodes(connection: Any, strict: bool = False) -> list[dict[str, Any]]:
    nodes = connection.get("nodes") if isinstance(connection, dict) else None
    if not isinstance(nodes, list):
        if strict:
            raise TrackerResponseError
        return []
    return [node for node in nodes if isinstance(node, dict)]


def _page_info(connection: Any) -> tuple[str | None, bool]:
    info = connection.get("pageInfo") if isinstance(connection, dict) else None
    if not isinstance(info, dict):
        return None, False
    cursor = info.get("endCursor")
    cursor = cursor if isinstance(cursor, str) and 0 < len(cursor) <= 500 else None
    return cursor, info.get("hasNextPage") is True and cursor is not None


def _text(value: Any, kind: str) -> str:
    if not isinstance(value, str):
        return ""
    # Control characters out: a title is shown as one line, and logs never see it.
    clean = "".join(char for char in value if char.isprintable()).strip()
    return clean[: _MAX_TEXT[kind]]


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if number >= 0 and number == number else None
