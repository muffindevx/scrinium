"""Linear's client (ADR-014): recorded answers, rate limits, rejected tokens and fuzzing.

No request leaves this machine: a transport answers with recorded bodies.
"""

import json
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from scrinium.infrastructure.tickets.linear import (
    HttpAnswer,
    LinearClient,
    TrackerAuthError,
    TrackerRateLimitedError,
    TrackerResponseError,
    TrackerUnavailableError,
    parse_issue,
)

FIXTURES = Path(__file__).parent / "provider_fixtures" / "linear"
TOKEN = "lin_api_0123456789abcdefghijklmnopqrstuv"


class Recorded:
    """Answers in order and remembers what was sent."""

    def __init__(self, *answers: HttpAnswer) -> None:
        self.answers = list(answers)
        self.sent: list[tuple[dict[str, Any], dict[str, str]]] = []

    def __call__(self, body: bytes, headers: dict[str, str]) -> HttpAnswer:
        self.sent.append((json.loads(body), headers))
        return self.answers.pop(0)


def ok(payload: Any) -> HttpAnswer:
    return HttpAnswer(200, {"Content-Type": "application/json"}, json.dumps(payload).encode())


def page() -> HttpAnswer:
    return HttpAnswer(200, {}, (FIXTURES / "issues_page.json").read_bytes())


def client(transport: Recorded, auth: str = "api_key", **kwargs: Any) -> LinearClient:
    return LinearClient(lambda: TOKEN, auth, transport, **kwargs)


def test_a_recorded_page_becomes_tickets_with_their_explicit_links() -> None:
    transport = Recorded(page())
    result = client(transport).issues(["team-shop"], None, None)

    gold, vip = result.issues
    assert result.has_next and result.end_cursor == "c2hvcC0yMzA=" and result.skipped == 0
    assert gold.key == "SHOP-221" and gold.previous_keys == ["ENG-12"]
    assert gold.status == "Done" and gold.status_category == "done" and gold.ticket_type == ""
    assert gold.closed_at == datetime(2026, 10, 11, 17, 42, 9, 512000, tzinfo=UTC)
    # Commits and pull requests the tracker attaches; a Slack link is neither.
    assert gold.commits == ["4f1e2d3c4b5a69788766554433221100ffeeddcc"]
    assert gold.pull_requests == ["https://github.com/acme/shop/pull/318"]
    # Linear has no issue type: a "Bug" label makes it one (ADR-014, amendment 4).
    assert vip.ticket_type == "bug" and vip.status_category == "started"
    # Read-only: the only operation sent is a query, filtered by team.
    body, _headers = transport.sent[0]
    assert body["query"].lstrip().startswith("query Issues")
    assert "mutation" not in body["query"]
    assert body["variables"]["filter"] == {"team": {"id": {"in": ["team-shop"]}}}


def test_an_incremental_page_asks_only_for_what_changed() -> None:
    transport = Recorded(page())
    since = datetime(2026, 10, 1, tzinfo=UTC)
    client(transport).issues(["team-shop"], since, "abc")
    variables = transport.sent[0][0]["variables"]
    assert variables["filter"]["updatedAt"] == {"gte": "2026-10-01T00:00:00+00:00"}
    assert variables["after"] == "abc"


def test_a_personal_key_goes_as_it_is_and_an_oauth_token_after_bearer() -> None:
    teams = ok({"data": {"organization": {"name": "Acme"}, "teams": {"nodes": []}}})
    personal, oauth = Recorded(teams), Recorded(teams)
    client(personal).organization_and_teams()
    client(oauth, auth="oauth").organization_and_teams()
    assert personal.sent[0][1]["Authorization"] == TOKEN
    assert oauth.sent[0][1]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    "answer",
    [
        HttpAnswer(401, {}, b'{"errors":[{"message":"bad"}]}'),
        HttpAnswer(400, {}, json.dumps({"errors": [{"message": "x", "extensions": {
            "code": "AUTHENTICATION_ERROR"}}]}).encode()),
        HttpAnswer(200, {}, json.dumps({"errors": [{"message": "x", "extensions": {
            "code": "FORBIDDEN"}}], "data": None}).encode()),
    ],
)  # fmt: skip
def test_a_rejected_token_is_an_auth_error_and_is_not_retried(answer: HttpAnswer) -> None:
    transport = Recorded(answer)
    with pytest.raises(TrackerAuthError) as raised:
        client(transport).organization_and_teams()
    assert len(transport.sent) == 1
    assert TOKEN not in str(raised.value) and TOKEN not in repr(raised.value)


def test_a_short_rate_limit_is_waited_and_a_long_one_goes_back_to_the_queue() -> None:
    slept: list[float] = []
    teams = ok({"data": {"organization": {"name": "Acme"}, "teams": {"nodes": []}}})
    short = Recorded(HttpAnswer(429, {"Retry-After": "2"}, b""), teams)
    client(short, sleep=slept.append).organization_and_teams()
    assert slept == [2.0] and len(short.sent) == 2

    long = Recorded(HttpAnswer(429, {"Retry-After": "900"}, b""))
    with pytest.raises(TrackerRateLimitedError) as raised:
        client(long, sleep=slept.append).organization_and_teams()
    assert raised.value.wait_seconds == 900.0 and slept == [2.0]


def test_linear_s_graphql_rate_limit_waits_until_its_reset() -> None:
    now = 1_800_000_000.0
    limited = HttpAnswer(
        400,
        {"X-RateLimit-Requests-Reset": str(int((now + 5) * 1000))},
        json.dumps({"errors": [{"message": "x", "extensions": {"code": "RATELIMITED"}}]}).encode(),
    )
    teams = ok({"data": {"organization": {"name": "Acme"}, "teams": {"nodes": []}}})
    slept: list[float] = []
    client(Recorded(limited, teams), sleep=slept.append, now=lambda: now).organization_and_teams()
    assert len(slept) == 1 and abs(slept[0] - 5.0) < 0.01


@pytest.mark.parametrize("status", [500, 502, 503])
def test_a_server_error_is_worth_retrying_later(status: int) -> None:
    with pytest.raises(TrackerUnavailableError):
        client(Recorded(HttpAnswer(status, {}, b"<html>"))).organization_and_teams()


@pytest.mark.parametrize(
    "body", [b"", b"not json", b"[]", b'{"data": null}', b'{"data": {"issues": {"nodes": 3}}}']
)
def test_an_answer_off_the_contract_is_a_response_error(body: bytes) -> None:
    with pytest.raises(TrackerResponseError):
        client(Recorded(HttpAnswer(200, {}, body))).issues(["t"], None, None)


def _random_value(rng: random.Random, depth: int = 0) -> Any:
    kinds = ["none", "bool", "int", "float", "str", "unicode"]
    if depth < 3:
        kinds += ["list", "dict"]
    kind = rng.choice(kinds)
    if kind == "none":
        return None
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "int":
        return rng.randint(-(10**12), 10**12)
    if kind == "float":
        return rng.choice([float("nan"), float("inf"), rng.random() * 1e9])
    if kind == "str":
        return rng.choice(["", "SHOP-1", "shop-1", "A-0", "2026-13-40T99:00:00Z", "x" * 5000])
    if kind == "unicode":
        return "".join(chr(rng.randint(0, 0x10FFFF)) for _ in range(rng.randint(0, 20))
                       if True).encode("utf-8", "replace").decode("utf-8", "replace")  # fmt: skip
    if kind == "list":
        return [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {
        rng.choice(["id", "identifier", "title", "team", "labels", "attachments", "state",
                    "nodes", "url", "previousIdentifiers", "updatedAt", "trashed", "name",
                    "type"]): _random_value(rng, depth + 1)
        for _ in range(rng.randint(0, 6))
    }  # fmt: skip


def test_any_node_is_either_a_ticket_or_skipped_never_a_crash() -> None:
    rng = random.Random(14)  # noqa: S311  a fuzzer that must repeat, not a secret
    recorded = json.loads((FIXTURES / "issues_page.json").read_text())
    base = recorded["data"]["issues"]["nodes"][0]
    for _ in range(3000):
        node: Any = _random_value(rng)
        if rng.random() < 0.5:
            # Mutate one field of a real node: closer to what a real API change looks like.
            node = {**base, rng.choice(list(base)): _random_value(rng)}
        issue = parse_issue(node)
        if issue is not None:
            assert issue.external_id and issue.key and issue.team_id
            assert len(issue.title) <= 500 and all(len(label) <= 100 for label in issue.labels)
            assert issue.url == "" or issue.url.startswith("https://")
            assert all(len(sha) == 40 for sha in issue.commits)


def test_a_page_with_unreadable_nodes_keeps_the_readable_ones() -> None:
    recorded = json.loads((FIXTURES / "issues_page.json").read_text())
    nodes = recorded["data"]["issues"]["nodes"]
    nodes += [{"id": "x"}, "texto", {"identifier": "SHOP-9"}, None]
    result = client(Recorded(ok(recorded))).issues(["team-shop"], None, None)
    assert [issue.key for issue in result.issues] == ["SHOP-221", "SHOP-230"]
    assert result.skipped == 4
