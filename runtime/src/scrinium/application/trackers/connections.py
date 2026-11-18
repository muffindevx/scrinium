"""Connecting a project to a ticket tracker (ADR-014).

- The token goes to the OS keyring (ADR-006); the database keeps an opaque reference, the host
  and the token's last four characters. Without a secure keyring nothing is connected.
- The token is only ever handed out for the host it was stored for (`connection_token`).
- A connection reads nothing until a person chooses its scope (which teams).
- Removing a connection removes its tickets, its links and its token. A personal API key stays
  valid in the tracker until the person revokes it there (ADR-014, amendment 1).
- Isolated mode (ADR-013) refuses all of it: a tracker is a remote host.
"""

import uuid
from collections.abc import Callable
from typing import Any, Final

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from scrinium.application.ai.providers import ProviderError, refuse_if_isolated
from scrinium.application.ingestion.sources import project_of
from scrinium.application.workspaces import record_audit
from scrinium.domain.enums import AuditResult, ConnectionStatus, TicketProvider
from scrinium.infrastructure.secret_store import SecretStore, SecretStoreUnavailableError
from scrinium.infrastructure.sqlite.models import Ticket, TicketConnection, TicketLink
from scrinium.infrastructure.tickets.linear import LINEAR_HOST, LinearClient, Team

MIN_TOKEN_LENGTH: Final = 16
MAX_TOKEN_LENGTH: Final = 512
MAX_TEAMS: Final = 50

ClientFactory = Callable[[Callable[[], str], str], LinearClient]


class TrackerSetupError(ValueError):
    """A request about tracker connections that Scrinium refuses. `code` is stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def default_client(token: Callable[[], str], auth: str) -> LinearClient:
    return LinearClient(token, auth)


def clean_token(token: str) -> str:
    value = token.strip()
    if not MIN_TOKEN_LENGTH <= len(value) <= MAX_TOKEN_LENGTH or not value.isprintable():
        raise TrackerSetupError("invalid_token")
    if any(char.isspace() for char in value):
        raise TrackerSetupError("invalid_token")
    return value


def check_allowed(session: Session) -> None:
    try:
        refuse_if_isolated(session)
    except ProviderError as error:
        raise TrackerSetupError(error.code) from None


def describe(connection: TicketConnection, tickets: int | None = None) -> dict[str, Any]:
    """What the interface may know about a connection. Never the token or its reference."""
    return {
        "id": str(connection.id),
        "project_id": str(connection.project_id),
        "provider": connection.provider.value,
        "host": connection.host,
        "display_name": connection.display_name,
        "status": connection.status.value,
        "auth": connection.auth,
        "has_token": connection.secret_ref is not None,
        "key_hint": connection.key_hint,
        "scope": connection.scope,
        "last_sync_at": _iso(connection.last_sync_at),
        "last_sweep_at": _iso(connection.last_sweep_at),
        "last_error": connection.last_error,
        "tickets": tickets,
        "created_at": connection.created_at.isoformat(),
    }


def list_connections(session: Session, project_id: uuid.UUID) -> list[dict[str, Any]]:
    from sqlalchemy import func

    project_of(session, project_id)
    counts = dict(
        session.execute(
            select(Ticket.connection_id, func.count())
            .where(Ticket.project_id == project_id, Ticket.tombstone.is_(False))
            .group_by(Ticket.connection_id)
        ).all()
    )
    return [
        describe(connection, int(counts.get(connection.id, 0)))
        for connection in session.scalars(
            select(TicketConnection)
            .where(TicketConnection.project_id == project_id)
            .order_by(TicketConnection.created_at)
        )
    ]


def create_connection(
    session: Session,
    store: SecretStore,
    project_id: uuid.UUID,
    token: str,
    organization: str,
    request_id: str | None = None,
) -> TicketConnection:
    """Stores a Linear token that already answered (`organization` is what it reported)."""
    check_allowed(session)
    project = project_of(session, project_id)
    token = clean_token(token)
    try:
        ref = store.put(token)
    except SecretStoreUnavailableError:
        raise TrackerSetupError("keyring_unavailable") from None
    connection = TicketConnection(
        workspace_id=project.workspace_id,
        project_id=project.id,
        provider=TicketProvider.LINEAR,
        host=LINEAR_HOST,
        display_name=organization[:200],
        status=ConnectionStatus.PENDING,
        auth="api_key",
        secret_ref=ref,
        key_hint=token[-4:],
        scope=[],
        cursor={},
    )
    session.add(connection)
    session.flush()
    record_audit(
        session, project.workspace_id, "ticket_connection.create", "ticket_connection",
        str(connection.id), AuditResult.SUCCESS, request_id,
        {"provider": connection.provider.value, "host": connection.host},
    )  # fmt: skip
    return connection


def connection_of(
    session: Session, project_id: uuid.UUID, connection_id: uuid.UUID
) -> TicketConnection:
    connection = session.get(TicketConnection, connection_id)
    if connection is None or connection.project_id != project_id:
        raise LookupError
    return connection


def connection_token(store: SecretStore, connection: TicketConnection) -> Callable[[], str]:
    """Reads the token from the keyring on each call, for this connection's host only."""
    ref, host = connection.secret_ref, connection.host

    def read() -> str:
        from scrinium.infrastructure.tickets.linear import TrackerAuthError

        if ref is None or host != LINEAR_HOST:
            raise TrackerAuthError
        try:
            token = store.get(ref)
        except SecretStoreUnavailableError:
            raise TrackerAuthError from None
        if not token:
            raise TrackerAuthError
        return token

    return read


def set_scope(
    session: Session,
    connection: TicketConnection,
    team_ids: list[str],
    available: list[Team],
    request_id: str | None = None,
) -> int:
    """Chooses the teams this project reads, among those the token can see. Tickets of teams
    that leave the scope are deleted with their links. Returns how many were deleted."""
    check_allowed(session)
    by_id = {team.id: team for team in available}
    chosen = list(dict.fromkeys(team_ids))
    if not chosen or len(chosen) > MAX_TEAMS or any(team not in by_id for team in chosen):
        raise TrackerSetupError("invalid_scope")
    connection.scope = [
        {"id": team, "key": by_id[team].key, "name": by_id[team].name} for team in chosen
    ]
    # A new scope is read from the start: tickets of a new team may be old.
    connection.cursor = {}
    connection.last_sweep_at = None
    connection.status = ConnectionStatus.ACTIVE
    connection.last_error = None
    leaving = select(Ticket.id).where(
        Ticket.connection_id == connection.id, Ticket.container_id.not_in(chosen)
    )
    session.execute(delete(TicketLink).where(TicketLink.ticket_id.in_(leaving)))
    removed = int(
        session.execute(
            delete(Ticket).where(
                Ticket.connection_id == connection.id, Ticket.container_id.not_in(chosen)
            )
        ).rowcount  # type: ignore[attr-defined]
    )
    record_audit(
        session, connection.workspace_id, "ticket_connection.scope", "ticket_connection",
        str(connection.id), AuditResult.SUCCESS, request_id,
        {"teams": [team["key"] for team in connection.scope], "tickets_removed": removed},
    )  # fmt: skip
    return removed


def remove_connection(
    session: Session,
    store: SecretStore,
    connection: TicketConnection,
    request_id: str | None = None,
) -> dict[str, int]:
    """Deletes the connection, its tickets, its links and its token from the keyring."""
    tickets = select(Ticket.id).where(Ticket.connection_id == connection.id)
    links = session.execute(delete(TicketLink).where(TicketLink.ticket_id.in_(tickets))).rowcount  # type: ignore[attr-defined]
    removed = session.execute(delete(Ticket).where(Ticket.connection_id == connection.id)).rowcount  # type: ignore[attr-defined]
    token_deleted = 1
    if connection.secret_ref is not None:
        try:
            store.delete(connection.secret_ref)
        except SecretStoreUnavailableError:
            # The row goes anyway: a reference to nothing is worse than an orphan secret the
            # keyring can show. The audit and the answer say so.
            token_deleted = 0
    summary = {
        "tickets_deleted": int(removed), "links_deleted": int(links),
        "token_deleted": token_deleted,
    }  # fmt: skip
    record_audit(
        session, connection.workspace_id, "ticket_connection.delete", "ticket_connection",
        str(connection.id), AuditResult.SUCCESS, request_id, summary,
    )  # fmt: skip
    session.delete(connection)
    return summary


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


def tracker_destinations(session: Session) -> list[str]:
    """Each connected tracker, as "provider (host)": for the privacy summary. Read-only, and
    nothing of the project is sent to it but the queries."""
    rows = session.execute(
        select(TicketConnection.provider, TicketConnection.host).distinct()
        .order_by(TicketConnection.provider, TicketConnection.host)
    )  # fmt: skip
    return [
        f"{provider.value.capitalize()} ({host}), solo lectura de tickets"
        for provider, host in rows
    ]
