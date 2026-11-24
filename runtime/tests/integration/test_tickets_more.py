"""The rest of phase 10 (ADR-014): Jira, reading text, inferred proposals, comments, polling and
OAuth. No request leaves this machine: Linear and Jira answer from memory.
"""

import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from scrinium.application.trackers.connections import TrackerTarget
from scrinium.application.trackers.poller import enqueue_due
from scrinium.application.trackers.sync import sync_connection
from scrinium.infrastructure.sqlite.models import (
    AuditEvent,
    KnowledgeEntity,
    Ticket,
    TicketConnection,
)
from scrinium.infrastructure.tickets import oauth
from scrinium.infrastructure.tickets.base import HttpAnswer, TrackerClient
from scrinium.infrastructure.tickets.jira import JiraClient
from scrinium.infrastructure.tickets.linear import LinearClient
from tests.integration.test_tickets_linear import TOKEN, Client, FakeLinear

JIRA = "https://acme.atlassian.net"


class TextLinear(FakeLinear):
    """Linear with descriptions, comments and the comment mutation."""

    def __init__(self) -> None:
        super().__init__()
        self.commented: list[tuple[str, str]] = []

    def __call__(self, body: bytes, headers: dict[str, str]) -> HttpAnswer:
        request = json.loads(body)
        if "mutation Comment" in request["query"]:
            if headers.get("Authorization") != self.token:
                return HttpAnswer(401, {}, b"")
            variables = request["variables"]
            self.commented.append((variables["issueId"], variables["body"]))
            return HttpAnswer(200, {}, b'{"data": {"commentCreate": {"success": true}}}')
        answer = super().__call__(body, headers)
        if "description" not in request["query"] or answer.status != 200:
            return answer
        data = json.loads(answer.body)
        for node in data["data"]["issues"]["nodes"]:
            node["description"] = f"Detalle de {node['identifier']}"
            node["comments"] = {"nodes": [{"body": "Visto", "user": {"name": "Ana"}}]}
        return HttpAnswer(200, {}, json.dumps({"data": data["data"]}).encode())


class FakeJira:
    """Jira Cloud in memory: server info, projects and a search."""

    def __init__(self) -> None:
        self.issues = [
            {"id": "100", "key": "PAY-1", "fields": {
                "summary": "Comisión de venta mal calculada", "issuetype": {"name": "Bug"},
                "status": {"name": "Done", "statusCategory": {"key": "done"}}, "labels": [],
                "created": "2026-10-01T09:00:00.000+0000",
                "updated": "2026-10-02T09:00:00.000+0000",
                "project": {"id": "10000"}}},
        ]  # fmt: skip
        self.auth: list[str] = []

    def __call__(
        self, method: str, url: str, body: bytes | None, headers: dict[str, str]
    ) -> HttpAnswer:
        self.auth.append(headers["Authorization"])
        path = url.removeprefix(f"{JIRA}/rest/api/3")
        if path == "/serverInfo":
            return HttpAnswer(200, {}, b'{"serverTitle": "Acme Jira"}')
        if path.startswith("/project/search"):
            return HttpAnswer(
                200, {}, b'{"values": [{"id": "10000", "key": "PAY", "name": "Pagos"}]}'
            )
        if path == "/search/jql":
            return HttpAnswer(200, {}, json.dumps({"issues": self.issues, "isLast": True}).encode())
        return HttpAnswer(404, {}, b"")


@pytest.fixture
def client(runtime: Any) -> Client:
    linear = TextLinear()
    linear.add("i1", "SHOP-221", "team-shop", 10, title="Comisión por venta en pedidos grandes")
    linear.add("i2", "SHOP-230", "team-shop", 20, title="Rediseño del logo")
    made = Client(runtime, linear)
    jira = FakeJira()

    def factory(target: TrackerTarget, token: Callable[[], str]) -> TrackerClient:
        if target.provider.value == "jira":
            return JiraClient(target.base_url, token, target.auth, target.email, jira,
                              read_text=target.read_text)  # fmt: skip
        return LinearClient(token, target.auth, linear, sleep=lambda _s: None,
                            read_text=target.read_text)  # fmt: skip

    runtime.app.state.ticket_clients = factory
    made.factory = factory  # type: ignore[method-assign, assignment]
    made.jira = jira  # type: ignore[attr-defined]
    return made


def _connection(client: Client) -> TicketConnection:
    with client.runtime.database.session() as session:
        found = session.scalars(select(TicketConnection)).one()
        session.expunge(found)
        return found


def test_jira_cloud_connects_with_an_email_and_syncs(client: Client) -> None:
    base = client.base
    jira = {"provider": "jira", "token": TOKEN, "base_url": JIRA}
    bad = client.send("POST", f"{base}/ticket-connections", {**jira, "email": "no"}, 422)
    assert bad["error"] == "invalid_email"
    created = client.send(
        "POST", f"{base}/ticket-connections", {**jira, "email": "ana@acme.com"}, 201
    )
    assert created["provider"] == "jira" and created["host"] == "acme.atlassian.net"
    assert created["base_url"] == JIRA and created["teams"][0]["key"] == "PAY"
    client.send("PUT", f"{base}/ticket-connections/{created['id']}/scope", {"team_ids": ["10000"]})
    pay = client.tickets()["PAY-1"]
    assert (
        pay.ticket_type == "bug" and pay.status_category == "done" and pay.container_id == "10000"
    )
    assert all(header.startswith("Basic ") for header in client.jira.auth)  # type: ignore[attr-defined]
    for url in ("http://acme.atlassian.net", "https://127.0.0.1"):
        refused = client.send("POST", f"{base}/ticket-connections",
                              {"provider": "jira", "token": TOKEN, "base_url": url,
                               "email": "a@b.co"}, 422)  # fmt: skip
        assert refused["error"] == "invalid_base_url"


def test_reading_text_is_a_choice_and_stops_cleanly(client: Client) -> None:
    connection_id = client.connect(["team-shop"])
    ticket = client.tickets()["SHOP-221"]
    assert ticket.description is None and ticket.comments is None
    path = f"{client.base}/ticket-connections/{connection_id}/options"
    options = client.send("PUT", path, {"read_text": True})
    assert options["read_text"] is True and options["allow_comments"] is False
    ticket = client.tickets()["SHOP-221"]
    assert ticket.description == "Detalle de SHOP-221" and ticket.comments == ["Visto"]
    client.send("PUT", path, {"read_text": False})
    ticket = client.tickets()["SHOP-221"]
    assert ticket.description is None and ticket.comments is None


def _rule(client: Client, title: str, statement: str) -> str:
    client.send("PUT", "/api/v1/profile", {"display_name": "Ana"})
    rule = client.send(
        "POST", f"{client.base}/rules", {"title": title, "statement": statement}, 201
    )
    return str(rule.get("id") or rule["entity_id"])


def test_inferred_proposals_can_be_confirmed_or_dismissed(client: Client) -> None:
    client.connect(["team-shop"])
    entity_id = _rule(client, "Comisión por venta",
                      "Las ventas en pedidos grandes pagan una comisión del 2 %.")  # fmt: skip
    path = f"{client.base}/rules/{entity_id}/tickets"
    found = client.send("GET", path)
    assert found["tickets"] == [] and [t["key"] for t in found["proposed"]] == ["SHOP-221"]
    assert found["proposed"][0]["level"] == "inferred" and found["proposed"][0]["shared_words"]
    dismissed = client.send("POST", f"{path}/{found['proposed'][0]['id']}/dismiss")
    assert dismissed["proposed"] == []
    assert client.send("GET", path)["proposed"] == []  # never proposed again


def test_comments_need_permission_and_a_confirmed_text(client: Client) -> None:
    connection_id = client.connect(["team-shop"])
    ticket = client.tickets()["SHOP-221"]
    entity_id = _rule(client, "Comisión por venta", "Las ventas grandes pagan comisión.")
    client.send(
        "POST", f"{client.base}/rules/{entity_id}/tickets", {"ticket_id": str(ticket.id)}, 201
    )
    draft = client.send("GET", f"{client.base}/tickets/{ticket.id}/comment-draft")
    assert draft["allowed"] is False and "Comisión por venta" in draft["body"]
    comment_path = f"{client.base}/tickets/{ticket.id}/comment"
    refused = client.send("POST", comment_path, {"body": draft["body"]}, 403)
    assert refused["error"] == "comments_not_allowed" and client.linear.commented == []  # type: ignore[attr-defined]
    client.send(
        "PUT", f"{client.base}/ticket-connections/{connection_id}/options", {"allow_comments": True}
    )
    sent = client.send("POST", comment_path, {"body": "Reglas: comisión"}, 201)
    assert sent == {"sent": True, "key": "SHOP-221"}
    assert client.linear.commented == [("i1", "Reglas: comisión")]  # type: ignore[attr-defined]
    with client.runtime.database.session() as session:
        audit = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "ticket.comment")
        ).one()
        assert "Reglas" not in json.dumps(audit.metadata_)  # the text never goes to the audit
    flow = client.send("GET", "/api/v1/privacy/data-flow")
    assert any("comentarios que confirmes" in d for d in flow["outbound_destinations"])


def test_polling_waits_longer_while_nothing_changes(client: Client) -> None:
    connection_id = client.connect(["team-shop"])
    queued: list[str] = []
    assert enqueue_due(client.runtime.database, queued.append) == 1 and queued == [connection_id]
    database, store = client.runtime.database, client.runtime.environment.secret_store
    intervals = []
    for _ in range(4):
        sync_connection(database, store, uuid.UUID(connection_id), client.factory, force=True)
        intervals.append(_connection(client).cursor["interval_minutes"])
    assert intervals == [20, 40, 60, 60]
    # Not forced and within its interval: the poll skips it.
    assert not sync_connection(database, store, uuid.UUID(connection_id), client.factory).ran
    client.linear.add("i2", "SHOP-230", "team-shop", 90)
    with database.session() as session:
        found = session.get_one(TicketConnection, uuid.UUID(connection_id))
        assert found.last_sync_at is not None
        found.last_sync_at = found.last_sync_at - timedelta(hours=2)
    assert sync_connection(database, store, uuid.UUID(connection_id), client.factory).ran
    assert _connection(client).cursor["interval_minutes"] == 10
    client.send("PUT", "/api/v1/ai-setup", {"choice": "isolated"})
    assert enqueue_due(database, queued.append) == 0


def test_oauth_with_pkce_end_to_end(client: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    start = f"{client.base}/ticket-connections/oauth/linear"
    assert client.send("GET", "/api/v1/ticket-oauth/linear")["available"] is False
    assert client.send("POST", start, None, 409)["error"] == "oauth_not_configured"
    monkeypatch.setenv("SCRINIUM_LINEAR_CLIENT_ID", "app-123")
    url = client.send("POST", start)["authorize_url"]
    query = parse_qs(urlsplit(url).query)
    assert query["code_challenge_method"] == ["S256"] and query["client_id"] == ["app-123"]
    assert query["redirect_uri"][0].endswith("/oauth/linear/callback")
    sent: list[tuple[str, dict[str, str]]] = []

    def token_endpoint(url: str, form: dict[str, str], headers: dict[str, str]) -> HttpAnswer:
        sent.append((url, form))
        if url == oauth.REVOKE_URL:
            return HttpAnswer(200, {}, b"")
        # The first access token is about to expire; the refreshed one lasts an hour.
        body = {"access_token": f"access-{len(sent)}", "refresh_token": f"refresh-{len(sent)}",
                "expires_in": 1 if len(sent) == 1 else 3600}  # fmt: skip
        return HttpAnswer(200, {}, json.dumps(body).encode())

    monkeypatch.setattr(oauth, "https_form_transport", lambda: token_endpoint)
    client.linear.token = "Bearer access-1"
    callback = urlsplit(query["redirect_uri"][0]).path
    wrong = client.runtime.client.get(callback, params={"state": "x", "code": "c"},
                                      headers=client.headers, follow_redirects=False)  # fmt: skip
    assert wrong.headers["location"] == "/integraciones?linear=expired"
    done = client.runtime.client.get(callback, params={"state": query["state"][0], "code": "c1"},
                                     headers=client.headers, follow_redirects=False)  # fmt: skip
    assert done.status_code == 303 and done.headers["location"] == "/integraciones?linear=connected"
    form = sent[0][1]
    assert (
        form["code_verifier"]
        and form["grant_type"] == "authorization_code"
        and "client_secret" not in form
    )
    again = client.runtime.client.get(callback, params={"state": query["state"][0], "code": "c1"},
                                      headers=client.headers, follow_redirects=False)  # fmt: skip
    assert again.headers["location"] == "/integraciones?linear=expired"  # a state works once
    connection = _connection(client)
    assert connection.auth == "oauth" and connection.key_hint == ""
    stored = client.runtime.environment.secret_store.get(connection.secret_ref or "")
    assert stored is not None and "access-1" in stored and TOKEN not in stored
    # The access token expires in a second: the next request refreshes it and rotates both.
    client.linear.token = "Bearer access-2"
    client.send(
        "PUT",
        f"{client.base}/ticket-connections/{connection.id}/scope",
        {"team_ids": ["team-shop"]},
    )
    assert (
        sent[1][1]["grant_type"] == "refresh_token" and sent[1][1]["refresh_token"] == "refresh-1"
    )
    assert "refresh-2" in (
        client.runtime.environment.secret_store.get(connection.secret_ref or "") or ""
    )
    removed = client.send("DELETE", f"{client.base}/ticket-connections/{connection.id}")
    assert removed["revoked"] is True and removed["revoke_at_provider"] is False
    assert sent[-1][0] == oauth.REVOKE_URL


def test_a_proposal_uses_rule_words_not_quoted_identifiers(client: Client) -> None:
    client.connect(["team-shop"])
    entity_id = _rule(client, "Rediseño", "Usa `logo_del_rediseno` del logo.")
    found = client.send("GET", f"{client.base}/rules/{entity_id}/tickets")
    keys = [t["key"] for t in found["proposed"]]
    assert keys in ([], ["SHOP-230"])  # never a match only through the quoted identifier
    with client.runtime.database.session() as session:
        assert session.get(KnowledgeEntity, uuid.UUID(entity_id)) is not None
        assert session.scalars(select(Ticket)).first() is not None
