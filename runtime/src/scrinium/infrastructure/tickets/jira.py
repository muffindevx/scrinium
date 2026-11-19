"""Jira's REST API, read-only but for one confirmed comment (ADR-014).

- **Cloud** (`https://<site>.atlassian.net`): REST v3, an API token with the account e-mail
  (HTTP Basic). **Data Center / Server**: REST v2, a personal access token (Bearer). The token is
  asked for on every request and never kept here.
- Only the base URL the connection was made with, over HTTPS with certificate checks and no
  redirects: a redirect would carry the token to whatever host it names.
- Issues by JQL, `updated >=` the cursor, oldest first; Cloud pages with `nextPageToken`, Data
  Center with `startAt`. Projects are the "teams" a connection chooses.
- Jira has issue types: a `Bug` (or a bug label) is a bug. It does not say a moved issue's old
  key; the sync keeps the key it had before as a previous one.
- Rate limits come as 429 with `Retry-After`: short waits are slept, long ones go back to the
  job queue.
- Descriptions and comments only when the connection allows reading text; Cloud's rich text
  (ADF) is flattened to plain text, and comments come without authors.
- The answer is untrusted input: an issue that cannot be read is skipped, never fatal.
"""

import base64
import ipaddress
import json
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Final
from urllib.parse import quote, urlsplit

from scrinium.infrastructure.tickets.base import (
    BUG_LABELS,
    MAX_COMMENTS,
    MAX_RESPONSE_BYTES,
    MAX_WAIT_SECONDS,
    TIMEOUT_SECONDS,
    HttpAnswer,
    Issue,
    IssuePage,
    NoRedirect,
    Team,
    TrackerAuthError,
    TrackerRateLimitedError,
    TrackerResponseError,
    TrackerUnavailableError,
    clean_text,
    parse_date,
    parse_json,
    parse_number,
)

PAGE_SIZE: Final = 50
SWEEP_PAGE_SIZE: Final = 100
_MAX_RETRIES: Final = 3
# Jira issue types that are bugs, in the names teams give them.
BUG_TYPES: Final = frozenset(
    {"bug", "defect", "error", "fallo", "incidencia", "defecto", "incident"}
)
_CATEGORIES: Final = {"new": "open", "indeterminate": "started", "done": "done"}
_FIELDS: Final = ["summary", "issuetype", "status", "labels", "created", "updated",
                  "resolutiondate", "project"]  # fmt: skip
_TEXT_FIELDS: Final = ["description", "comment"]

# (method, url, body, headers) -> answer
Transport = Callable[[str, str, bytes | None, dict[str, str]], HttpAnswer]


def check_base_url(url: str) -> str:
    """HTTPS to a named host, no user or password, no query: a Jira site's address."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("unsupported url")
    if parts.query or parts.fragment or "." not in parts.hostname:
        raise ValueError("unsupported url")
    try:
        ipaddress.ip_address(parts.hostname.strip("[]"))
    except ValueError:
        pass
    else:
        # A named service, as with model providers: an address is a different case (ADR-010).
        raise ValueError("unsupported url")
    return f"https://{parts.netloc.lower()}{parts.path.rstrip('/')}"


def https_transport(base_url: str) -> Transport:
    """Requests only below `base_url`, honouring HTTPS_PROXY, never following a redirect."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect()
    )

    def send(method: str, url: str, body: bytes | None, headers: dict[str, str]) -> HttpAnswer:
        if not url.startswith(f"{base_url}/"):
            raise TrackerResponseError
        request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                return HttpAnswer(response.status, dict(response.headers.items()), raw)
        except urllib.error.HTTPError as error:
            try:
                raw = error.read(MAX_RESPONSE_BYTES + 1)
            except OSError:
                raw = b""
            finally:
                error.close()
            return HttpAnswer(error.code, dict(error.headers.items()), raw)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TrackerUnavailableError from None

    return send


def authorization(token: str, auth: str, email: str) -> str:
    """Cloud: Basic with the account e-mail and the API token. Data Center: Bearer PAT."""
    if auth == "basic":
        return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()
    return f"Bearer {token}"


class JiraClient:
    def __init__(
        self,
        base_url: str,
        token: Callable[[], str],
        auth: str = "basic",
        email: str = "",
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        read_text: bool = False,
    ) -> None:
        """`auth` "basic" is Jira Cloud (REST v3); "pat" is Data Center (REST v2)."""
        self._base = check_base_url(base_url)
        self._token = token
        self._auth = auth
        self._email = email
        self._cloud = auth == "basic"
        self._api = f"{self._base}/rest/api/{3 if self._cloud else 2}"
        self._transport = transport or https_transport(self._base)
        self._sleep = sleep
        self._read_text = read_text
        self.requests = 0

    def organization_and_teams(self) -> tuple[str, list[Team]]:
        info = self._call("GET", "/serverInfo")
        name = clean_text(info.get("serverTitle"), "title") if isinstance(info, dict) else ""
        if self._cloud:
            listed = self._call("GET", "/project/search?maxResults=100")
            rows = listed.get("values") if isinstance(listed, dict) else None
        else:
            rows = self._call("GET", "/project")
        teams = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            project_id = clean_text(row.get("id"), "id")
            key = clean_text(row.get("key"), "label")
            if project_id and key:
                teams.append(Team(project_id, key, clean_text(row.get("name"), "title") or key))
        return name or urlsplit(self._base).hostname or "", teams

    def issues(self, team_ids: list[str], since: datetime | None, after: str | None) -> IssuePage:
        jql = self._jql(team_ids, since)
        fields = _FIELDS + (_TEXT_FIELDS if self._read_text else [])
        data = self._search(jql, fields, PAGE_SIZE, after)
        raw = data.get("issues")
        if not isinstance(raw, list):
            raise TrackerResponseError
        issues = [issue for node in raw if (issue := self.parse_issue(node)) is not None]
        cursor, more = self._next(data, after, len(raw))
        return IssuePage(issues, len(raw) - len(issues), cursor, more)

    def issue_ids(
        self, team_ids: list[str], after: str | None
    ) -> tuple[list[str], str | None, bool]:
        data = self._search(self._jql(team_ids, None), ["id"], SWEEP_PAGE_SIZE, after)
        raw = data.get("issues")
        if not isinstance(raw, list):
            raise TrackerResponseError
        ids = [str(node["id"]) for node in raw if isinstance(node, dict) and node.get("id")]
        cursor, more = self._next(data, after, len(raw))
        return ids, cursor, more

    def add_comment(self, issue_id: str, body: str) -> None:
        """A comment on an issue, after a person confirmed it, on a connection that allows it."""
        text = body[:10_000]
        payload: Any = (
            {"body": {"type": "doc", "version": 1, "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": line}]}
                for line in text.splitlines() if line.strip()
            ]}}
            if self._cloud
            else {"body": text}
        )  # fmt: skip
        self._call("POST", f"/issue/{quote(issue_id, safe='')}/comment", payload, expect=(201, 200))

    def parse_issue(self, node: Any) -> Issue | None:
        fields = _dict(node.get("fields")) if isinstance(node, dict) else {}
        if not fields:
            return None
        external_id = clean_text(node.get("id"), "id")
        key = clean_text(node.get("key"), "label")
        project_id = clean_text(_dict(fields.get("project")).get("id"), "id")
        if not external_id or not key or "-" not in key or not project_id:
            return None
        labels = [
            label for value in _list(fields.get("labels")) if (label := clean_text(value, "label"))
        ]
        labels = labels[:20]
        type_name = clean_text(_dict(fields.get("issuetype")).get("name"), "label").casefold()
        status = _dict(fields.get("status"))
        category = _dict(status.get("statusCategory"))
        bug = type_name in BUG_TYPES or any(label.casefold() in BUG_LABELS for label in labels)
        description = None
        comments: list[str] = []
        if self._read_text:
            description = flatten(fields.get("description")) or None
            listed = _list(_dict(fields.get("comment")).get("comments"))
            comments = [
                text for item in listed[:MAX_COMMENTS]
                if (text := flatten(_dict(item).get("body"), "comment"))
            ]  # fmt: skip
        return Issue(
            external_id=external_id,
            key=key,
            title=clean_text(fields.get("summary"), "title"),
            team_id=project_id,
            labels=labels,
            ticket_type="bug" if bug else "",
            status=clean_text(status.get("name"), "status"),
            status_category=_CATEGORIES.get(str(category.get("key", "")), ""),
            url=f"{self._base}/browse/{quote(key)}",
            opened_at=parse_date(_iso(fields.get("created"))),
            changed_at=parse_date(_iso(fields.get("updated"))),
            closed_at=parse_date(_iso(fields.get("resolutiondate"))),
            description=description,
            comments=comments,
        )

    # --- requests ---------------------------------------------------------------------------

    def _jql(self, team_ids: list[str], since: datetime | None) -> str:
        projects = ", ".join(str(int(team)) for team in team_ids if team.isdigit())
        if not projects:
            raise TrackerResponseError
        jql = f"project in ({projects})"
        if since is not None:
            # JQL has minutes; a minute earlier re-reads at most a few, and upserts are idempotent.
            moment = (since.astimezone(UTC) - timedelta(minutes=1)).strftime("%Y/%m/%d %H:%M")
            jql += f' AND updated >= "{moment}"'
        return jql + " ORDER BY updated ASC, id ASC"

    def _search(self, jql: str, fields: list[str], size: int, after: str | None) -> dict[str, Any]:
        if self._cloud:
            body: dict[str, Any] = {"jql": jql, "fields": fields, "maxResults": size}
            if after:
                body["nextPageToken"] = after
            data = self._call("POST", "/search/jql", body)
        else:
            start = int(after) if after and after.isdigit() else 0
            body = {"jql": jql, "fields": fields, "maxResults": size, "startAt": start}
            data = self._call("POST", "/search", body)
        if not isinstance(data, dict):
            raise TrackerResponseError
        return data

    def _next(self, data: dict[str, Any], after: str | None, count: int) -> tuple[str | None, bool]:
        if self._cloud:
            token = data.get("nextPageToken")
            more = isinstance(token, str) and bool(token) and data.get("isLast") is not True
            return (token if more else None), more
        start = int(after) if after and after.isdigit() else 0
        total = parse_number(str(data.get("total", "")))
        more = count > 0 and total is not None and start + count < total
        return (str(start + count) if more else None), more

    def _call(
        self, method: str, path: str, body: Any = None, expect: tuple[int, ...] = (200,)
    ) -> Any:
        data = None if body is None else json.dumps(body).encode()
        for attempt in range(_MAX_RETRIES + 1):
            headers = {
                "Accept": "application/json",
                "Authorization": authorization(self._token(), self._auth, self._email),
            }
            if data is not None:
                headers["Content-Type"] = "application/json"
            self.requests += 1
            answer = self._transport(method, f"{self._api}{path}", data, headers)
            if answer.status == 429:
                waits = {name.lower(): value for name, value in answer.headers.items()}
                wait = parse_number(waits.get("retry-after")) or 5.0
                if wait > MAX_WAIT_SECONDS or attempt == _MAX_RETRIES:
                    raise TrackerRateLimitedError(wait)
                self._sleep(wait)
                continue
            if answer.status in (401, 403):
                raise TrackerAuthError
            if answer.status >= 500 or answer.status in (408, 425):
                raise TrackerUnavailableError
            if answer.status not in expect or len(answer.body) > MAX_RESPONSE_BYTES:
                raise TrackerResponseError
            return parse_json(answer.body) if answer.body else {}
        raise TrackerRateLimitedError(MAX_WAIT_SECONDS)  # pragma: no cover


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _iso(value: Any) -> Any:
    """Jira writes offsets without a colon (`+0000`); ISO wants one (`+00:00`)."""
    if isinstance(value, str) and len(value) > 5 and value[-5] in "+-" and value[-4:].isdigit():
        return f"{value[:-2]}:{value[-2:]}"
    return value


def flatten(value: Any, kind: str = "description") -> str:
    """Plain text of a Jira body: a string (Data Center) or Atlassian Document Format (Cloud)."""
    if isinstance(value, str):
        return clean_text(value, kind, lines=True)
    parts: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "text" and isinstance(node.get("text"), str):
                parts.append(node["text"])
            for child in node.get("content", []) if isinstance(node.get("content"), list) else []:
                walk(child)
            if node.get("type") in ("paragraph", "heading", "listItem", "codeBlock"):
                parts.append("\n")

    walk(value)
    return clean_text("".join(parts), kind, lines=True)
