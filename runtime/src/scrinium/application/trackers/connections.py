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
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from scrinium.application.ai.providers import ProviderError, refuse_if_isolated
from scrinium.application.ingestion.sources import project_of
from scrinium.application.workspaces import record_audit
from scrinium.domain.enums import AuditResult, ConnectionStatus, TicketProvider
from scrinium.infrastructure.secret_store import SecretStore, SecretStoreUnavailableError
from scrinium.infrastructure.sqlite.models import Ticket, TicketConnection, TicketLink
from scrinium.infrastructure.tickets.base import Team, TrackerAuthError, TrackerClient
from scrinium.infrastructure.tickets.jira import JiraClient, check_base_url
from scrinium.infrastructure.tickets.linear import LINEAR_HOST, LinearClient

MIN_TOKEN_LENGTH: Final = 16
MAX_TOKEN_LENGTH: Final = 4096
MAX_TEAMS: Final = 50
# How each provider authenticates: Linear with a personal key or OAuth; Jira Cloud with an API
# token and the account e-mail ("basic"); Jira Data Center with a personal access token.
AUTHS: Final = {TicketProvider.LINEAR: ("api_key", "oauth"), TicketProvider.JIRA: ("basic", "pat")}


@dataclass(frozen=True)
class TrackerTarget:
    """Where a client talks to and how: everything but the token."""

    provider: TicketProvider
    base_url: str
    auth: str
    email: str = ""
    read_text: bool = False


ClientFactory = Callable[[TrackerTarget, Callable[[], str]], TrackerClient]


class TrackerSetupError(ValueError):
    """A request about tracker connections that Scrinium refuses. `code` is stable."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def default_client(target: TrackerTarget, token: Callable[[], str]) -> TrackerClient:
    if target.provider is TicketProvider.JIRA:
        return JiraClient(target.base_url, token, target.auth, target.email,
                          read_text=target.read_text)  # fmt: skip
    return LinearClient(token, target.auth, read_text=target.read_text)


def target_of(connection: TicketConnection) -> TrackerTarget:
    settings = connection.settings or {}
    return TrackerTarget(
        connection.provider,
        str(settings.get("base_url") or f"https://{LINEAR_HOST}"),
        connection.auth,
        str(settings.get("email") or ""),
        bool(settings.get("read_text")),
    )


def new_target(
    provider: TicketProvider, auth: str, base_url: str = "", email: str = ""
) -> TrackerTarget:
    """A target from what a person typed, checked: HTTPS to a named host for Jira, an e-mail
    with Jira Cloud's tokens, and an authentication the provider has."""
    if auth not in AUTHS[provider]:
        raise TrackerSetupError("invalid_auth")
    if provider is TicketProvider.LINEAR:
        return TrackerTarget(provider, f"https://{LINEAR_HOST}", auth)
    try:
        url = check_base_url(base_url)
    except ValueError:
        raise TrackerSetupError("invalid_base_url") from None
    email = email.strip()
    if auth == "basic" and (
        "@" not in email or len(email) > 320 or any(c.isspace() for c in email)
    ):
        raise TrackerSetupError("invalid_email")
    return TrackerTarget(provider, url, auth, email if auth == "basic" else "")


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
    settings = connection.settings or {}
    return {
        "id": str(connection.id),
        "project_id": str(connection.project_id),
        "provider": connection.provider.value,
        "host": connection.host,
        "base_url": settings.get("base_url") or f"https://{LINEAR_HOST}",
        "display_name": connection.display_name,
        "status": connection.status.value,
        "auth": connection.auth,
        "has_token": connection.secret_ref is not None,
        "key_hint": connection.key_hint,
        "scope": connection.scope,
        "read_text": bool(settings.get("read_text")),
        "allow_comments": bool(settings.get("allow_comments")),
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
    target: TrackerTarget | None = None,
) -> TicketConnection:
    """Stores a token that already answered (`organization` is what the tracker reported). For
    OAuth, `token` is the tokens as `oauth.encode_secret` writes them: one keyring secret."""
    check_allowed(session)
    project = project_of(session, project_id)
    target = target or TrackerTarget(TicketProvider.LINEAR, f"https://{LINEAR_HOST}", "api_key")
    token = clean_token(token)
    try:
        ref = store.put(token)
    except SecretStoreUnavailableError:
        raise TrackerSetupError("keyring_unavailable") from None
    settings: dict[str, Any] = {}
    if target.provider is TicketProvider.JIRA:
        settings = {"base_url": target.base_url, "email": target.email}
    connection = TicketConnection(
        workspace_id=project.workspace_id,
        project_id=project.id,
        provider=target.provider,
        host=(urlsplit(target.base_url).hostname or "").lower(),
        display_name=organization[:200],
        status=ConnectionStatus.PENDING,
        auth=target.auth,
        secret_ref=ref,
        key_hint=token[-4:] if target.auth != "oauth" else "",
        scope=[],
        cursor={},
        settings=settings,
    )
    session.add(connection)
    session.flush()
    record_audit(
        session, project.workspace_id, "ticket_connection.create", "ticket_connection",
        str(connection.id), AuditResult.SUCCESS, request_id,
        {"provider": connection.provider.value, "host": connection.host, "auth": target.auth},
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
    """Reads the token from the keyring on each call, for this connection's host only: the host
    it was stored for must still be the one its address names."""
    ref, host, auth = connection.secret_ref, connection.host, connection.auth
    expected = (urlsplit(target_of(connection).base_url).hostname or "").lower()

    def read() -> str:
        if ref is None or not host or host != expected:
            raise TrackerAuthError
        try:
            token = store.get(ref)
        except SecretStoreUnavailableError:
            raise TrackerAuthError from None
        if not token:
            raise TrackerAuthError
        if auth == "oauth":
            return _oauth_access(store, ref, token)
        return token

    return read


def _oauth_access(store: SecretStore, ref: str, stored: str) -> str:
    """The access token of an OAuth connection, refreshed first when it is about to expire;
    the rotated refresh token replaces the old one under the same keyring reference."""
    import time

    from scrinium.infrastructure.tickets import oauth

    tokens = oauth.decode_secret(stored)
    if tokens is None:
        raise TrackerAuthError
    due = tokens.expires_at is not None and time.time() >= tokens.expires_at - (
        oauth.REFRESH_MARGIN_SECONDS
    )
    if not due:
        return tokens.access
    client = oauth.client_id()
    if not tokens.refresh or client is None:
        raise TrackerAuthError
    fresh = oauth.refresh(tokens.refresh, client)
    if fresh.refresh is None:
        fresh = oauth.Tokens(fresh.access, tokens.refresh, fresh.expires_at)
    try:
        store.replace(ref, oauth.encode_secret(fresh))
    except SecretStoreUnavailableError:
        raise TrackerAuthError from None
    return fresh.access


def update_options(
    session: Session,
    connection: TicketConnection,
    read_text: bool | None,
    allow_comments: bool | None,
    request_id: str | None = None,
) -> None:
    """Reading descriptions and comments (10.6) and commenting (10.7): off by default, each a
    person's choice. Turning reading off deletes the text already stored, at once."""
    check_allowed(session)
    settings = dict(connection.settings or {})
    if read_text is not None:
        settings["read_text"] = read_text
        if not read_text:
            session.execute(
                update(Ticket)
                .where(Ticket.connection_id == connection.id)
                .values(description=None, comments=None)
            )
        else:
            # Tickets read before have no text: read everything again.
            connection.cursor = {}
    if allow_comments is not None:
        settings["allow_comments"] = allow_comments
    connection.settings = settings
    record_audit(
        session, connection.workspace_id, "ticket_connection.options", "ticket_connection",
        str(connection.id), AuditResult.SUCCESS, request_id,
        {"read_text": settings.get("read_text", False),
         "allow_comments": settings.get("allow_comments", False)},
    )  # fmt: skip


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
    """Each connected tracker, as "provider (host)" and what goes there: the queries only, or
    also the comments a person confirms (10.7). For the privacy summary."""
    found: dict[str, bool] = {}
    for connection in session.scalars(select(TicketConnection)):
        label = f"{connection.provider.value.capitalize()} ({connection.host})"
        allow = bool((connection.settings or {}).get("allow_comments"))
        found[label] = found.get(label, False) or allow
    return [
        f"{label}, lectura de tickets y comentarios que confirmes" if allow
        else f"{label}, solo lectura de tickets"
        for label, allow in sorted(found.items())
    ]  # fmt: skip
