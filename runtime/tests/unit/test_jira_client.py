"""Jira's client (ADR-014): Cloud and Data Center, recorded answers, no network."""

import base64
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from scrinium.infrastructure.tickets.base import (
    HttpAnswer,
    TrackerAuthError,
    TrackerRateLimitedError,
    TrackerResponseError,
)
from scrinium.infrastructure.tickets.jira import JiraClient, check_base_url, flatten

SITE = "https://acme.atlassian.net"
TOKEN = "ATATT3xFfGF0-jira-token-0123456789"


class Recorded:
    def __init__(self, *answers: HttpAnswer) -> None:
        self.answers = list(answers)
        self.sent: list[tuple[str, str, Any, dict[str, str]]] = []

    def __call__(
        self, method: str, url: str, body: bytes | None, headers: dict[str, str]
    ) -> HttpAnswer:
        self.sent.append((method, url, json.loads(body) if body else None, headers))
        return self.answers.pop(0)


def ok(payload: Any, status: int = 200) -> HttpAnswer:
    return HttpAnswer(status, {}, json.dumps(payload).encode())


def issue(issue_id: str, key: str, **fields: Any) -> dict[str, Any]:
    return {"id": issue_id, "key": key, "fields": {
        "summary": f"Ticket {key}", "issuetype": {"name": "Story"},
        "status": {"name": "In Progress", "statusCategory": {"key": "indeterminate"}},
        "labels": [], "created": "2026-10-01T09:00:00.000+0000",
        "updated": "2026-10-02T10:30:00.000+0200", "resolutiondate": None,
        "project": {"id": "10000", "key": "SHOP"}, **fields,
    }}  # fmt: skip


def test_cloud_reads_projects_and_pages_issues_by_token() -> None:
    transport = Recorded(
        ok({"serverTitle": "Acme"}),
        ok({"values": [{"id": "10000", "key": "SHOP", "name": "Tienda"}, {"name": "sin id"}]}),
        ok({"issues": [issue("1", "SHOP-1", issuetype={"name": "Bug"})], "nextPageToken": "t2"}),
        ok({"issues": [issue("2", "SHOP-2")], "isLast": True}),
    )
    client = JiraClient(SITE, lambda: TOKEN, "basic", "ana@acme.com", transport)
    name, teams = client.organization_and_teams()
    assert name == "Acme" and [(t.id, t.key) for t in teams] == [("10000", "SHOP")]
    since = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    first = client.issues(["10000"], since, None)
    [bug] = first.issues
    assert bug.ticket_type == "bug" and bug.status_category == "started"
    assert bug.url == f"{SITE}/browse/SHOP-1" and bug.changed_at == datetime(
        2026, 10, 2, 8, 30, tzinfo=UTC
    )
    assert first.has_next and first.end_cursor == "t2"
    second = client.issues(["10000"], since, first.end_cursor)
    assert not second.has_next and [i.key for i in second.issues] == ["SHOP-2"]
    method, url, body, headers = transport.sent[2]
    assert (method, url) == ("POST", f"{SITE}/rest/api/3/search/jql")
    assert (
        body["jql"]
        == 'project in (10000) AND updated >= "2026/10/01 11:59" ORDER BY updated ASC, id ASC'
    )
    assert "description" not in body["fields"]  # text only when allowed
    expected = base64.b64encode(f"ana@acme.com:{TOKEN}".encode()).decode()
    assert headers["Authorization"] == f"Basic {expected}"
    assert transport.sent[3][2]["nextPageToken"] == "t2"


def test_data_center_uses_v2_a_pat_and_start_at() -> None:
    transport = Recorded(
        ok({"issues": [issue("1", "OPS-1")], "startAt": 0, "total": 2}),
        ok({"issues": [issue("2", "OPS-2")], "startAt": 1, "total": 2}),
    )
    client = JiraClient(
        "https://jira.acme.internal/jira", lambda: TOKEN, "pat", transport=transport
    )
    page = client.issues(["10000"], None, None)
    assert page.has_next and page.end_cursor == "1"
    assert not client.issues(["10000"], None, "1").has_next
    _method, url, body, headers = transport.sent[0]
    assert url == "https://jira.acme.internal/jira/rest/api/2/search" and body["startAt"] == 0
    assert headers["Authorization"] == f"Bearer {TOKEN}" and "updated >=" not in body["jql"]


def test_text_is_read_only_when_allowed_and_flattened() -> None:
    adf = {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Falla al pagar"}]},
        {"type": "codeBlock", "content": [{"type": "text", "text": 'File "a.py", line 3'}]},
    ]}  # fmt: skip
    comments = {"comments": [{"body": "Visto", "author": {"displayName": "Ana"}}]}
    node = issue("1", "SHOP-1", description=adf, comment=comments)
    client = JiraClient(SITE, lambda: TOKEN, "basic", "a@b.co", Recorded(ok({"issues": [node]})),
                        read_text=True)  # fmt: skip
    [read] = client.issues(["10000"], None, None).issues
    assert read.description == 'Falla al pagar\nFile "a.py", line 3'
    assert read.comments == ["Visto"]  # without its author
    assert flatten("texto\x07 plano") == "texto plano"


def test_comments_are_written_as_adf_on_cloud() -> None:
    transport = Recorded(ok({"id": "c1"}, 201))
    JiraClient(SITE, lambda: TOKEN, "basic", "a@b.co", transport).add_comment("1", "Hola\nMundo")
    method, url, body, _ = transport.sent[0]
    assert (method, url) == ("POST", f"{SITE}/rest/api/3/issue/1/comment")
    assert [p["content"][0]["text"] for p in body["body"]["content"]] == ["Hola", "Mundo"]


@pytest.mark.parametrize("status", [401, 403])
def test_rejected_tokens_and_limits(status: int) -> None:
    with pytest.raises(TrackerAuthError):
        JiraClient(SITE, lambda: TOKEN, transport=Recorded(ok({}, status))).organization_and_teams()
    slept: list[float] = []
    limited = Recorded(HttpAnswer(429, {"Retry-After": "3"}, b""), ok({"serverTitle": "x"}),
                       ok({"values": []}))  # fmt: skip
    JiraClient(SITE, lambda: TOKEN, transport=limited, sleep=slept.append).organization_and_teams()
    assert slept == [3.0]
    with pytest.raises(TrackerRateLimitedError):
        JiraClient(SITE, lambda: TOKEN,
                   transport=Recorded(HttpAnswer(429, {"Retry-After": "600"}, b""))).issues(["1"], None, None)  # noqa: E501  # fmt: skip


def test_unreadable_issues_are_skipped_and_odd_answers_refused() -> None:
    nodes = [issue("1", "SHOP-1"), {"id": "2"}, "x", {"id": "3", "key": "NOKEY", "fields": {}}]
    page = JiraClient(SITE, lambda: TOKEN, transport=Recorded(ok({"issues": nodes}))).issues(
        ["1"], None, None
    )
    assert [i.key for i in page.issues] == ["SHOP-1"] and page.skipped == 3
    with pytest.raises(TrackerResponseError):
        JiraClient(SITE, lambda: TOKEN, transport=Recorded(ok({"x": 1}))).issues(["1"], None, None)
    with pytest.raises(TrackerResponseError):  # a project id that is not a number never reaches JQL
        JiraClient(SITE, lambda: TOKEN, transport=Recorded()).issues(["1) OR (1=1"], None, None)


@pytest.mark.parametrize(
    "url", ["http://acme.atlassian.net", "https://user:pw@acme.atlassian.net", "https://localhost",
            "https://acme.atlassian.net/?x=1"],
)  # fmt: skip
def test_only_https_to_a_named_site(url: str) -> None:
    with pytest.raises(ValueError, match="unsupported"):
        check_base_url(url)
