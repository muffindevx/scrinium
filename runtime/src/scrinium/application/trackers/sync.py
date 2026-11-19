"""Incremental, read-only sync of a tracker's tickets (phase 10.3, ADR-014, annex C.3).

- **Cursor:** each connection remembers the newest `updatedAt` it has stored. The next sync asks
  only for tickets changed at or after it. "At or after" re-reads the last one: upserts are
  idempotent, so that costs one row and never misses a ticket changed in the same instant.
- **One transaction per page:** an interrupted sync keeps the pages it stored. The cursor only
  moves at the end, because Linear does not promise ascending order: the next run starts from
  the old cursor and finds nothing new in the pages already stored.
- **Identity:** the provider's immutable id. A ticket that moved between teams keeps its row;
  its old keys go to `previous_keys`, so commits that name them still resolve.
- **Tombstones:** a trashed ticket is marked, not deleted, so links say what happened. The weekly
  sweep reads every id still visible and marks the rest (deleted, or no longer visible with this
  token).
- **Explicit links:** commits and pull requests the provider attaches to a ticket (annex C.4).
- **Errors:** a rejected token stops the connection (`auth`) and is not retried; a network
  failure or a long rate limit is raised, so the job queue retries it later.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from scrinium.application.ai.setup import is_isolated
from scrinium.application.trackers.connections import (
    ClientFactory,
    connection_token,
    default_client,
    target_of,
)
from scrinium.domain.enums import ConnectionStatus, TicketLinkLevel
from scrinium.infrastructure.secret_store import SecretStore
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import Ticket, TicketConnection, TicketLink, utc_now
from scrinium.infrastructure.tickets.base import (
    Issue,
    TrackerAuthError,
    TrackerClient,
    TrackerError,
)

logger = logging.getLogger("scrinium.trackers")

SWEEP_EVERY: Final = timedelta(days=7)
# Polling, not webhooks (a local app receives no calls): a sync asked for sooner than this after
# the last one is skipped unless it is forced.
MIN_INTERVAL: Final = timedelta(minutes=10)
# Adaptive polling (annex C.3): a connection with nothing new waits twice as long, up to this.
MAX_INTERVAL: Final = timedelta(minutes=60)
PROVIDER_ORIGIN: Final = "linear:attachment"
# A safety net against a provider that keeps saying "there is another page".
MAX_PAGES: Final = 2000


@dataclass
class SyncSummary:
    pages: int = 0
    stored: int = 0
    # Tickets changed after the cursor: re-reading the one at the cursor is not a change.
    changed: int = 0
    skipped: int = 0
    tombstoned: int = 0
    swept: bool = False
    error: str | None = None
    ran: bool = True


def sync_connection(
    database: Database,
    store: SecretStore,
    connection_id: uuid.UUID,
    client_factory: ClientFactory = default_client,
    force: bool = False,
    clock: Callable[[], datetime] = utc_now,
) -> SyncSummary:
    with database.session() as session:
        connection = session.get(TicketConnection, connection_id)
        # Isolated mode (ADR-013) blocks every egress, connected trackers included.
        if is_isolated(session):
            return SyncSummary(ran=False)
        if connection is None or connection.status not in (
            ConnectionStatus.ACTIVE, ConnectionStatus.ERROR,
        ) or not connection.scope:  # fmt: skip
            return SyncSummary(ran=False)
        recent = connection.last_sync_at is not None and (
            clock() - connection.last_sync_at < interval_of(connection)
        )
        if recent and not force:
            return SyncSummary(ran=False)
        teams = [str(team["id"]) for team in connection.scope]
        since = _parse(connection.cursor.get("updated_at"))
        due_sweep = connection.last_sweep_at is None or (
            clock() - connection.last_sweep_at >= SWEEP_EVERY
        )
        client = client_factory(target_of(connection), connection_token(store, connection))
        read_text = bool((connection.settings or {}).get("read_text"))
    summary = SyncSummary()
    try:
        newest = _pull(database, connection_id, client, teams, since, summary, read_text)
        if due_sweep:
            summary.tombstoned = _sweep(database, connection_id, client, teams)
            summary.swept = True
    except TrackerAuthError:
        summary.error = TrackerAuthError.code
        _finish(database, connection_id, ConnectionStatus.ERROR, summary.error, None, False, clock)
        logger.warning("tickets_sync_stopped code=auth")
        return summary
    except TrackerError as error:
        _finish(database, connection_id, ConnectionStatus.ERROR, error.code, None, False, clock)
        logger.warning("tickets_sync_failed code=%s", error.code)
        raise
    _finish(
        database, connection_id, ConnectionStatus.ACTIVE, None, newest, summary.swept, clock,
        summary.changed,
    )  # fmt: skip
    logger.info(
        "tickets_synced pages=%d stored=%d skipped=%d tombstoned=%d",
        summary.pages, summary.stored, summary.skipped, summary.tombstoned,
    )  # fmt: skip
    return summary


def _pull(
    database: Database,
    connection_id: uuid.UUID,
    client: TrackerClient,
    teams: list[str],
    since: datetime | None,
    summary: SyncSummary,
    read_text: bool = False,
) -> datetime | None:
    newest = since
    after: str | None = None
    for _ in range(MAX_PAGES):
        page = client.issues(teams, since, after)
        summary.pages += 1
        summary.skipped += page.skipped
        wanted = [issue for issue in page.issues if issue.team_id in teams]
        with database.session() as session:
            connection = session.get_one(TicketConnection, connection_id)
            store_issues(session, connection, wanted, read_text)
        summary.stored += len(wanted)
        for issue in wanted:
            if issue.changed_at is not None and (since is None or issue.changed_at > since):
                summary.changed += 1
            if issue.changed_at is not None and (newest is None or issue.changed_at > newest):
                newest = issue.changed_at
        if not page.has_next:
            break
        after = page.end_cursor
    return newest


def store_issues(
    session: Session, connection: TicketConnection, issues: list[Issue], read_text: bool = False
) -> None:
    """Upserts by the provider's id, and replaces each ticket's explicit links."""
    if not issues:
        return
    existing = {
        ticket.external_id: ticket
        for ticket in session.scalars(
            select(Ticket).where(
                Ticket.connection_id == connection.id,
                Ticket.external_id.in_([issue.external_id for issue in issues]),
            )
        )
    }
    now = utc_now()
    for issue in issues:
        ticket = existing.get(issue.external_id)
        if ticket is None:
            ticket = Ticket(
                workspace_id=connection.workspace_id,
                project_id=connection.project_id,
                connection_id=connection.id,
                external_id=issue.external_id,
                previous_keys=[],
            )
            session.add(ticket)
            existing[issue.external_id] = ticket
        # The key it had here before counts as a previous key too: a move can arrive without
        # the provider listing it yet.
        previous = list(ticket.previous_keys or [])
        if ticket.key and ticket.key != issue.key:
            previous.append(ticket.key)
        previous += issue.previous_keys
        ticket.previous_keys = [key for key in dict.fromkeys(previous) if key != issue.key][:20]
        ticket.key = issue.key
        ticket.container_id = issue.team_id
        ticket.title = issue.title
        ticket.ticket_type = issue.ticket_type
        ticket.status = issue.status
        ticket.status_category = issue.status_category
        ticket.labels = issue.labels
        ticket.url = issue.url
        ticket.opened_at = issue.opened_at
        ticket.changed_at = issue.changed_at
        ticket.closed_at = issue.closed_at
        ticket.tombstone = issue.trashed
        ticket.synced_at = now
        # Text only with the connection's permission; without it, nothing is kept (10.6).
        ticket.description = issue.description if read_text else None
        ticket.comments = list(issue.comments) if read_text and issue.comments else None
    session.flush()
    _replace_explicit_links(session, connection, issues, existing)


def _replace_explicit_links(
    session: Session,
    connection: TicketConnection,
    issues: list[Issue],
    tickets: dict[str, Ticket],
) -> None:
    ids = [tickets[issue.external_id].id for issue in issues]
    session.execute(
        delete(TicketLink).where(
            TicketLink.ticket_id.in_(ids), TicketLink.origin == PROVIDER_ORIGIN
        )
    )
    for issue in issues:
        ticket = tickets[issue.external_id]
        refs = [("commit", sha) for sha in issue.commits]
        refs += [("pull_request", url) for url in issue.pull_requests]
        for kind, ref in refs:
            session.add(
                TicketLink(
                    workspace_id=connection.workspace_id,
                    project_id=connection.project_id,
                    ticket_id=ticket.id,
                    from_kind=kind,
                    from_ref=ref,
                    level=TicketLinkLevel.EXPLICIT,
                    origin=PROVIDER_ORIGIN,
                )
            )


def _sweep(
    database: Database, connection_id: uuid.UUID, client: TrackerClient, teams: list[str]
) -> int:
    """Marks as tombstones the tickets the provider no longer shows. Returns how many."""
    visible: set[str] = set()
    after: str | None = None
    for _ in range(MAX_PAGES):
        ids, after, has_next = client.issue_ids(teams, after)
        visible.update(ids)
        if not has_next:
            break
    else:
        # The sweep did not end: marking now would tombstone tickets it never reached.
        return 0
    with database.session() as session:
        stored = session.execute(
            select(Ticket.id, Ticket.external_id).where(
                Ticket.connection_id == connection_id, Ticket.tombstone.is_(False)
            )
        ).all()
        gone = [ticket_id for ticket_id, external_id in stored if external_id not in visible]
        for start in range(0, len(gone), 500):
            session.execute(
                update(Ticket)
                .where(Ticket.id.in_(gone[start : start + 500]))
                .values(tombstone=True, synced_at=utc_now())
            )
    return len(gone)


def _finish(
    database: Database,
    connection_id: uuid.UUID,
    status: ConnectionStatus,
    error: str | None,
    newest: datetime | None,
    swept: bool,
    clock: Callable[[], datetime],
    changed: int = 0,
) -> None:
    with database.session() as session:
        connection = session.get(TicketConnection, connection_id)
        if connection is None:
            return
        connection.status = status
        connection.last_error = error
        if error is None:
            connection.last_sync_at = clock()
            # Something new: poll again soon. Nothing (only the ticket at the cursor read again):
            # wait twice as long, up to an hour.
            before = int(connection.cursor.get("interval_minutes") or MIN_INTERVAL.seconds // 60)
            changed = changed > 0 or connection.cursor.get("updated_at") is None
            minutes = (
                MIN_INTERVAL.seconds // 60
                if changed
                else min(before * 2, MAX_INTERVAL.seconds // 60)
            )
            cursor = dict(connection.cursor)
            if newest is not None:
                cursor["updated_at"] = newest.isoformat()
            cursor["interval_minutes"] = minutes
            connection.cursor = cursor
            if swept:
                connection.last_sweep_at = clock()


def interval_of(connection: TicketConnection) -> timedelta:
    """How long this connection waits between polls (adaptive, 10 to 60 minutes)."""
    minutes = connection.cursor.get("interval_minutes") if connection.cursor else None
    if not isinstance(minutes, int):
        return MIN_INTERVAL
    return min(max(timedelta(minutes=minutes), MIN_INTERVAL), MAX_INTERVAL)


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
