"""How fast tickets sync (phase 10.3): 10 000 tickets in under 5 minutes the first time and
under 10 seconds incrementally (ADR-014).

Linear is simulated in memory, so this measures Scrinium's side: parsing, upserts, links and
the sweep. To estimate a real first sync, add the provider's latency per page: with 50 tickets
per page, 10 000 tickets are 200 pages (plus 40 sweep pages of 250 ids).

    uv run python benchmarks/tickets_sync_speed.py --tickets 10000 --latency 0.4
"""

import argparse
import json
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scrinium.application.trackers.sync import sync_connection
from scrinium.application.workspaces import ensure_local_workspace
from scrinium.domain.enums import ConnectionStatus, TicketProvider
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import Project, TicketConnection
from scrinium.infrastructure.tickets.linear import HttpAnswer, LinearClient

T0 = datetime(2026, 1, 1, tzinfo=UTC)


class MemoryStore:
    """A keyring in memory: the benchmark measures the sync, not the OS keyring."""

    backend_name = "memoria"

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, value: str) -> str:
        ref = f"keyring:{len(self.values):032x}"
        self.values[ref] = value
        return ref

    def get(self, ref: str) -> str | None:
        return self.values.get(ref)

    def delete(self, ref: str) -> None:
        self.values.pop(ref, None)


class Linear:
    def __init__(self, count: int) -> None:
        self.issues = [self._issue(n, n) for n in range(count)]
        self.pages = 0

    @staticmethod
    def _issue(n: int, minutes: int) -> dict[str, Any]:
        sha = f"{n:040x}"
        return {
            "id": f"issue-{n:06d}", "identifier": f"SHOP-{n + 1}", "previousIdentifiers": [],
            "title": f"Ticket {n}", "url": f"https://linear.app/acme/issue/SHOP-{n + 1}",
            "createdAt": T0.isoformat(),
            "updatedAt": (T0 + timedelta(minutes=minutes)).isoformat(),
            "completedAt": None, "canceledAt": None, "trashed": False,
            "state": {"name": "Done", "type": "completed"}, "team": {"id": "team"},
            "labels": {"nodes": [{"name": "Bug" if n % 5 == 0 else "Feature"}]},
            "attachments": {"nodes": [{"url": f"https://github.com/acme/shop/commit/{sha}"}]},
        }  # fmt: skip

    def __call__(self, body: bytes, _headers: dict[str, str]) -> HttpAnswer:
        self.pages += 1
        request = json.loads(body)
        variables = request["variables"]
        since = variables["filter"].get("updatedAt", {}).get("gte")
        rows = self.issues
        if since is not None:
            limit = datetime.fromisoformat(since)
            rows = [row for row in rows if datetime.fromisoformat(row["updatedAt"]) >= limit]
        sweep = "query Sweep" in request["query"]
        start, size = int(variables["after"] or 0), variables["first"]
        chunk = rows[start : start + size]
        more = start + size < len(rows)
        nodes = [{"id": row["id"]} for row in chunk] if sweep else chunk
        data = {"issues": {"nodes": nodes, "pageInfo": {
            "hasNextPage": more, "endCursor": str(start + size) if more else None}}}  # fmt: skip
        return HttpAnswer(200, {}, json.dumps({"data": data}).encode())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tickets", type=int, default=10_000)
    parser.add_argument("--changed", type=int, default=100)
    parser.add_argument("--latency", type=float, default=0.0, help="seconds per page to add")
    args = parser.parse_args()
    store = MemoryStore()
    linear = Linear(args.tickets)
    with tempfile.TemporaryDirectory(prefix="scrinium-tickets-") as tmp:
        database = Database.open(Path(tmp))
        with database.session() as session:
            workspace = ensure_local_workspace(session)
            project = Project(workspace_id=workspace.id, name="Bench", project_key="bench")
            session.add(project)
            session.flush()
            connection = TicketConnection(
                workspace_id=workspace.id, project_id=project.id,
                provider=TicketProvider.LINEAR, host="api.linear.app", display_name="Acme",
                status=ConnectionStatus.ACTIVE, auth="api_key",
                secret_ref=store.put("lin_api_bench_token_0123456789"), key_hint="6789",
                scope=[{"id": "team", "key": "SHOP", "name": "Tienda"}], cursor={},
            )  # fmt: skip
            session.add(connection)
            session.flush()
            connection_id = connection.id

        def factory(token: Any, auth: str) -> LinearClient:
            return LinearClient(token, auth, linear)

        started = time.perf_counter()
        first = sync_connection(database, store, connection_id, factory, force=True)
        first_seconds = time.perf_counter() - started
        first_pages = linear.pages

        for n in range(args.changed):
            linear.issues[n] = Linear._issue(n, args.tickets + 60 + n)
        linear.pages = 0
        started = time.perf_counter()
        again = sync_connection(database, store, connection_id, factory, force=True)
        again_seconds = time.perf_counter() - started
        again_pages = linear.pages
        database.dispose()

    print(
        f"first sync: {first.stored} tickets, {first_pages} pages, {first_seconds:.1f} s here,"
        f" ~{first_seconds + first_pages * args.latency:.1f} s with {args.latency} s/page"
    )
    print(
        f"incremental: {again.stored} tickets, {again_pages} pages, {again_seconds:.2f} s here,"
        f" ~{again_seconds + again_pages * args.latency:.2f} s with {args.latency} s/page"
    )


if __name__ == "__main__":
    main()
