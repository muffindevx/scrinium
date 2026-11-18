"""Linear connected to a project, end to end through the API (phase 10, ADR-014).

A fake Linear answers the three queries Scrinium sends (teams, issues, sweep) from memory, with
real pagination and the `updatedAt` filter. No request leaves this machine.
"""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from scrinium.application.trackers.sync import sync_connection
from scrinium.infrastructure.sqlite.models import (
    AuditEvent,
    KnowledgeEntity,
    Ticket,
    TicketConnection,
    TicketLink,
)
from scrinium.infrastructure.tickets.linear import HttpAnswer, LinearClient
from tests.conftest import ORIGIN, Runtime

TOKEN = "lin_api_0123456789abcdefghijklmnopqrstuv"
SHA_GOLD = "4f1e2d3c4b5a69788766554433221100ffeeddcc"
SHA_VIP = "a" * 40
T0 = datetime(2026, 10, 1, tzinfo=UTC)


class FakeLinear:
    """Linear in memory: teams, issues by id, and pages of `page_size`."""

    def __init__(self, page_size: int = 2) -> None:
        self.teams = [
            {"id": "team-shop", "key": "SHOP", "name": "Tienda"},
            {"id": "team-ops", "key": "OPS", "name": "Operaciones"},
        ]
        self.issues: dict[str, dict[str, Any]] = {}
        self.page_size = page_size
        self.token = TOKEN
        self.queries: list[dict[str, Any]] = []
        self.status = 200

    def add(self, issue_id: str, key: str, team: str, minutes: int, **fields: Any) -> None:
        self.issues[issue_id] = {
            "id": issue_id, "identifier": key, "previousIdentifiers": [],
            "title": f"Ticket {key}", "url": f"https://linear.app/acme/issue/{key}",
            "createdAt": T0.isoformat(),
            "updatedAt": (T0 + timedelta(minutes=minutes)).isoformat(),
            "completedAt": None, "canceledAt": None, "trashed": False,
            "state": {"name": "Todo", "type": "unstarted"}, "team": {"id": team},
            "labels": {"nodes": []}, "attachments": {"nodes": []}, **fields,
        }  # fmt: skip

    def __call__(self, body: bytes, headers: dict[str, str]) -> HttpAnswer:
        request = json.loads(body)
        self.queries.append(request)
        if headers.get("Authorization") != self.token:
            return HttpAnswer(401, {}, b'{"errors":[{"message":"unauthenticated"}]}')
        if self.status != 200:
            return HttpAnswer(self.status, {}, b"")
        query, variables = request["query"], request["variables"]
        if "query Teams" in query:
            data: dict[str, Any] = {
                "organization": {"name": "Acme"}, "teams": {"nodes": self.teams},
            }  # fmt: skip
            return HttpAnswer(200, {}, json.dumps({"data": data}).encode())
        wanted = variables["filter"]["team"]["id"]["in"]
        since = variables["filter"].get("updatedAt", {}).get("gte")
        rows = [
            issue for issue in self.issues.values()
            if issue["team"]["id"] in wanted
            and (since is None or datetime.fromisoformat(issue["updatedAt"])
                 >= datetime.fromisoformat(since))
        ]  # fmt: skip
        rows.sort(key=lambda issue: issue["id"])
        start = int(variables["after"] or 0)
        size = variables["first"] if "query Sweep" in query else self.page_size
        chunk = rows[start : start + size]
        nodes = [{"id": row["id"]} for row in chunk] if "query Sweep" in query else chunk
        more = start + size < len(rows)
        data = {"issues": {"nodes": nodes, "pageInfo": {
            "hasNextPage": more, "endCursor": str(start + size) if more else None}}}  # fmt: skip
        return HttpAnswer(200, {}, json.dumps({"data": data}).encode())


class Client:
    def __init__(self, runtime: Runtime, linear: FakeLinear) -> None:
        self.runtime = runtime
        self.linear = linear
        runtime.app.state.ticket_clients = self.factory
        self.headers = {"Origin": ORIGIN, "X-CSRF-Token": runtime.login()}
        self.project_id = self.send("POST", "/api/v1/projects", {"name": "Tienda"}, 201)["id"]
        self.base = f"/api/v1/projects/{self.project_id}"

    def factory(self, token: Callable[[], str], auth: str) -> LinearClient:
        return LinearClient(token, auth, self.linear, sleep=lambda _seconds: None)

    def send(self, method: str, path: str, body: Any = None, expected: int = 200) -> Any:
        response = self.runtime.client.request(method, path, json=body, headers=self.headers)
        assert response.status_code == expected, response.text
        assert TOKEN not in response.text
        return response.json() if response.content else None

    def connect(self, teams: list[str] | None = None) -> str:
        created = self.send("POST", f"{self.base}/ticket-connections", {"token": TOKEN}, 201)
        connection_id: str = created["id"]
        if teams is not None:
            self.send("PUT", f"{self.base}/ticket-connections/{connection_id}/scope",
                      {"team_ids": teams})  # fmt: skip
        return connection_id

    def tickets(self) -> dict[str, Ticket]:
        with self.runtime.database.session() as session:
            rows = session.scalars(select(Ticket)).all()
            for row in rows:
                session.expunge(row)
        return {row.key: row for row in rows}

    def sync(self, connection_id: str) -> Any:
        return sync_connection(
            self.runtime.database, self.runtime.environment.secret_store,
            uuid.UUID(connection_id), self.factory, force=True,
        )  # fmt: skip


@pytest.fixture
def linear() -> FakeLinear:
    fake = FakeLinear()
    fake.add(
        "i1",
        "SHOP-221",
        "team-shop",
        10,
        labels={"nodes": [{"name": "Feature"}]},
        attachments={"nodes": [{"url": f"https://github.com/acme/shop/commit/{SHA_GOLD}"}]},
    )
    fake.add("i2", "SHOP-230", "team-shop", 20, labels={"nodes": [{"name": "Bug"}]})
    fake.add("i3", "SHOP-231", "team-shop", 30)
    fake.add("i4", "OPS-7", "team-ops", 40)
    return fake  # fmt: skip


@pytest.fixture
def client(runtime: Runtime, linear: FakeLinear) -> Client:
    return Client(runtime, linear)


def test_connecting_stores_the_token_in_the_keyring_only(client: Client) -> None:
    created = client.send("POST", f"{client.base}/ticket-connections", {"token": TOKEN}, 201)
    assert created["display_name"] == "Acme" and created["status"] == "pending"
    assert created["key_hint"] == TOKEN[-4:] and created["has_token"] is True
    assert [team["key"] for team in created["teams"]] == ["SHOP", "OPS"]
    with client.runtime.database.session() as session:
        connection = session.scalars(select(TicketConnection)).one()
        assert connection.secret_ref is not None and TOKEN not in json.dumps(
            {"ref": connection.secret_ref, "scope": connection.scope}
        )
        assert client.runtime.environment.secret_store.get(connection.secret_ref) == TOKEN
        audit = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "ticket_connection.create")
        ).one()
        assert TOKEN not in json.dumps(audit.metadata_)
    # Nothing is read until a person chooses the teams.
    assert client.tickets() == {}


def test_a_token_the_tracker_rejects_is_never_stored(client: Client) -> None:
    client.linear.token = "lin_api_another_valid_looking_token"
    answer = client.send("POST", f"{client.base}/ticket-connections", {"token": TOKEN}, 422)
    assert answer["error"] == "token_rejected"
    with client.runtime.database.session() as session:
        assert session.scalars(select(TicketConnection)).first() is None


def test_isolated_mode_refuses_trackers(client: Client) -> None:
    client.send("PUT", "/api/v1/ai-setup", {"choice": "isolated"})
    answer = client.send("POST", f"{client.base}/ticket-connections", {"token": TOKEN}, 403)
    assert answer["error"] == "isolated_mode" and client.linear.queries == []


def test_choosing_the_scope_syncs_only_those_teams_page_by_page(client: Client) -> None:
    client.connect(["team-shop"])
    tickets = client.tickets()
    assert sorted(tickets) == ["SHOP-221", "SHOP-230", "SHOP-231"]
    assert tickets["SHOP-230"].ticket_type == "bug" and tickets["SHOP-221"].ticket_type == ""
    issue_queries = [q for q in client.linear.queries if "query Issues" in q["query"]]
    # Three tickets in pages of two.
    assert len(issue_queries) == 2
    with client.runtime.database.session() as session:
        link = session.scalars(select(TicketLink)).one()
        assert (link.from_kind, link.from_ref, link.level.value) == ("commit", SHA_GOLD, "explicit")
        connection = session.scalars(select(TicketConnection)).one()
        assert connection.status.value == "active" and connection.last_sweep_at is not None
        assert connection.cursor == {"updated_at": (T0 + timedelta(minutes=30)).isoformat()}


def test_an_incremental_sync_reads_only_what_changed_and_follows_moves(client: Client) -> None:
    connection_id = client.connect(["team-shop", "team-ops"])
    first_id = client.tickets()["SHOP-231"].id
    client.linear.queries.clear()
    # SHOP-231 moves to OPS and becomes OPS-8; Linear lists its old key.
    client.linear.add("i3", "OPS-8", "team-ops", 50, previousIdentifiers=["SHOP-231"])
    summary = client.sync(connection_id)
    assert summary.stored == 2  # the one that changed, and the last one re-read at the cursor
    tickets = client.tickets()
    assert "SHOP-231" not in tickets
    assert tickets["OPS-8"].id == first_id and tickets["OPS-8"].previous_keys == ["SHOP-231"]
    sent = next(q for q in client.linear.queries if "query Issues" in q["query"])
    assert sent["variables"]["filter"]["updatedAt"]["gte"].startswith("2026-10-01T00:40")


def test_trashed_and_vanished_tickets_become_tombstones(client: Client) -> None:
    connection_id = client.connect(["team-shop"])
    client.linear.add("i2", "SHOP-230", "team-shop", 60, trashed=True)
    del client.linear.issues["i3"]
    client.sync(connection_id)
    # The deleted one is found by the weekly sweep.
    with client.runtime.database.session() as session:
        connection = session.scalars(select(TicketConnection)).one()
        assert connection.last_sweep_at is not None
        connection.last_sweep_at = connection.last_sweep_at - timedelta(days=8)
    summary = client.sync(connection_id)
    tickets = client.tickets()
    assert tickets["SHOP-230"].tombstone and tickets["SHOP-231"].tombstone
    assert not tickets["SHOP-221"].tombstone and summary.swept and summary.tombstoned == 1


def test_a_team_that_leaves_the_scope_takes_its_tickets(client: Client) -> None:
    connection_id = client.connect(["team-shop", "team-ops"])
    assert "OPS-7" in client.tickets()
    answer = client.send(
        "PUT",
        f"{client.base}/ticket-connections/{connection_id}/scope",
        {"team_ids": ["team-shop"]},
    )
    assert answer["tickets_removed"] == 1 and "OPS-7" not in client.tickets()
    client.send(
        "PUT", f"{client.base}/ticket-connections/{connection_id}/scope",
        {"team_ids": ["team-unknown"]}, 422,
    )  # fmt: skip


def test_a_revoked_token_stops_the_connection_without_retrying(client: Client) -> None:
    connection_id = client.connect(["team-shop"])
    client.linear.token = "lin_api_revoked_and_replaced_token"
    summary = client.sync(connection_id)
    assert summary.error == "auth"
    listed = client.send("GET", f"{client.base}/ticket-connections")["connections"][0]
    assert listed["status"] == "error" and listed["last_error"] == "auth"
    assert listed["tickets"] == 3  # what was read stays readable


def test_an_unavailable_tracker_is_left_to_the_queue(client: Client) -> None:
    from scrinium.infrastructure.tickets.linear import TrackerUnavailableError

    connection_id = client.connect(["team-shop"])
    client.linear.status = 503
    with pytest.raises(TrackerUnavailableError):
        client.sync(connection_id)


def _rule(client: Client, metadata: dict[str, Any]) -> str:
    client.send("PUT", "/api/v1/profile", {"display_name": "Ana"})
    rule = client.send("POST", f"{client.base}/rules", {
        "title": "Envío gratis", "statement": "El envío es gratis desde 40 EUR."}, 201)  # fmt: skip
    entity_id: str = rule["id"] if "id" in rule else rule["entity_id"]
    with client.runtime.database.session() as session:
        entity = session.get_one(KnowledgeEntity, uuid.UUID(entity_id))
        entity.metadata_ = {**entity.metadata_, **metadata}
    return entity_id


def test_a_rule_reaches_its_tickets_by_level(client: Client) -> None:
    client.linear.add("i3", "OPS-8", "team-ops", 50, previousIdentifiers=["SHOP-231"])
    client.connect(["team-shop", "team-ops"])
    # Blame found two commits: one Linear attaches to SHOP-221, one whose message names an old
    # key (SHOP-231, now OPS-8), and a key no ticket has.
    entity_id = _rule(client, {
        "tickets": ["SHOP-231", "PAY-9"],
        "ticket_commits": {"SHOP-231": [SHA_VIP], "PAY-9": [SHA_VIP]},
        "commits": [SHA_GOLD, SHA_VIP],
    })  # fmt: skip
    path = f"{client.base}/rules/{entity_id}/tickets"
    why = client.send("GET", path)
    assert [(t["key"], t["level"]) for t in why["tickets"]] == [
        ("SHOP-221", "explicit"), ("OPS-8", "mentioned"),
    ]  # fmt: skip
    assert why["tickets"][0]["commits"] == [SHA_GOLD]
    assert why["tickets"][1]["commits"] == [SHA_VIP] and why["unmatched_keys"] == ["PAY-9"]
    # Metadata only: no description, comment or person.
    assert set(why["tickets"][0]) >= {"title", "status", "type", "url"}
    assert not {"description", "assignee", "creator"} & set(why["tickets"][0])

    bug = client.tickets()["SHOP-230"]
    linked = client.send("POST", path, {"ticket_id": str(bug.id)}, 201)
    assert (
        linked["tickets"][0]["key"] == "SHOP-230" and linked["tickets"][0]["level"] == "confirmed"
    )
    client.send("DELETE", f"{path}/{bug.id}", expected=204)
    # A link from history cannot be removed by hand.
    client.send("DELETE", f"{path}/{client.tickets()['SHOP-221'].id}", expected=404)


def test_the_privacy_card_lists_the_tracker(client: Client) -> None:
    client.connect(["team-shop"])
    flow = client.send("GET", "/api/v1/privacy/data-flow")
    assert any("api.linear.app" in destination for destination in flow["outbound_destinations"])


def test_removing_the_connection_removes_tickets_links_and_token(client: Client) -> None:
    connection_id = client.connect(["team-shop"])
    with client.runtime.database.session() as session:
        ref = session.scalars(select(TicketConnection.secret_ref)).one()
    answer = client.send("DELETE", f"{client.base}/ticket-connections/{connection_id}")
    assert answer == {"tickets_deleted": 3, "links_deleted": 1, "token_deleted": 1,
                      "revoke_at_provider": True}  # fmt: skip
    assert ref is not None and client.runtime.environment.secret_store.get(ref) is None
    with client.runtime.database.session() as session:
        assert session.scalars(select(TicketLink)).first() is None


def test_deleting_the_project_and_wiping_take_the_token_too(client: Client) -> None:
    client.connect(["team-shop"])
    with client.runtime.database.session() as session:
        ref = session.scalars(select(TicketConnection.secret_ref)).one()
    client.send("DELETE", client.base, {"confirmation": _project_key(client)})
    assert ref is not None and client.runtime.environment.secret_store.get(ref) is None
    assert client.tickets() == {}

    other = client.send("POST", "/api/v1/projects", {"name": "Otra"}, 201)["id"]
    client.base = f"/api/v1/projects/{other}"
    client.connect(["team-shop"])
    with client.runtime.database.session() as session:
        ref = session.scalars(select(TicketConnection.secret_ref)).one()
    client.send("DELETE", "/api/v1/privacy/workspace-data", {"confirmation": "BORRAR TODO"})
    assert ref is not None and client.runtime.environment.secret_store.get(ref) is None


def _project_key(client: Client) -> str:
    projects = client.send("GET", "/api/v1/projects")
    rows = projects["projects"] if isinstance(projects, dict) else projects
    key: str = next(p for p in rows if p["id"] == client.project_id)["project_key"]
    return key


def test_no_token_reaches_the_logs(client: Client, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    caplog.set_level(logging.DEBUG)
    connection_id = client.connect(["team-shop"])
    client.linear.token = "lin_api_revoked_and_replaced_token"
    client.sync(connection_id)
    client.linear.token, client.linear.status = TOKEN, 503
    with pytest.raises(Exception):  # noqa: B017  the queue retries it
        client.sync(connection_id)
    assert caplog.records and TOKEN not in caplog.text
