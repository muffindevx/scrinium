"""Ticket trackers connected to a project and the tickets behind a rule (phase 10, ADR-014).

Calls to the tracker happen outside any database transaction: a slow provider never holds
SQLite's writer. The token never comes back in an answer, a log or an error.
"""

import uuid
from typing import Any, Final, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select

from scrinium.application.environment import LocalEnvironment
from scrinium.application.ingestion import sources
from scrinium.application.ingestion.sources import IngestionRunner
from scrinium.application.knowledge.common import KnowledgeNotFoundError, get_in_project
from scrinium.application.trackers import connections, evidence
from scrinium.application.trackers.connections import (
    ClientFactory,
    TrackerSetupError,
    default_client,
)
from scrinium.application.trackers.triage import triage_ticket
from scrinium.application.workspaces import record_audit
from scrinium.domain.enums import AuditResult, TicketProvider
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import KnowledgeEntity, Project, Ticket, TicketConnection
from scrinium.infrastructure.tickets import oauth
from scrinium.infrastructure.tickets.base import (
    Team,
    TrackerAuthError,
    TrackerError,
    TrackerRateLimitedError,
)
from scrinium.infrastructure.tickets.linear import LINEAR_HOST

SYNC_JOB: Final = "ticket_sync"
_CODES: Final = {
    "keyring_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "isolated_mode": status.HTTP_403_FORBIDDEN,
    "isolated_managed": status.HTTP_403_FORBIDDEN,
}


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["linear", "jira"] = "linear"
    token: str = Field(min_length=1, max_length=connections.MAX_TOKEN_LENGTH)
    # Linear: "api_key". Jira Cloud: "basic" (with `email`); Data Center: "pat".
    auth: Literal["api_key", "basic", "pat"] | None = None
    base_url: str = Field(default="", max_length=500)
    email: str = Field(default="", max_length=320)


class OptionsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read_text: bool | None = None
    allow_comments: bool | None = None


class CommentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=10_000)


class ScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_ids: list[str] = Field(min_length=1, max_length=connections.MAX_TEAMS)


class LinkBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: uuid.UUID


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _project(session: Any, project_id: uuid.UUID) -> Project:
    try:
        return sources.project_of(session, project_id)
    except sources.SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None


def _setup_error(error: TrackerSetupError) -> HTTPException:
    return HTTPException(_CODES.get(error.code, status.HTTP_422_UNPROCESSABLE_CONTENT), error.code)


def _tracker_error(error: TrackerError) -> HTTPException:
    """What the person sees when the tracker fails: a code, never the provider's text."""
    if isinstance(error, TrackerAuthError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "token_rejected")
    if isinstance(error, TrackerRateLimitedError):
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "rate_limited")
    return HTTPException(status.HTTP_502_BAD_GATEWAY, f"tracker_{error.code}")


def _teams(teams: list[Team]) -> list[dict[str, str]]:
    return [{"id": team.id, "key": team.key, "name": team.name} for team in teams]


def build_tickets_router(
    database: Database, environment: LocalEnvironment, runner: IngestionRunner
) -> APIRouter:
    router = APIRouter()
    store = environment.secret_store
    flows = oauth.OAuthFlows()

    def factory(request: Request) -> ClientFactory:
        # Tests put a client with recorded answers here; nothing else does.
        chosen: ClientFactory = getattr(request.app.state, "ticket_clients", None) or (
            default_client
        )
        return chosen

    def enqueue_sync(connection_id: uuid.UUID) -> None:
        if runner.jobs is None:
            return
        # Lowest priority: what the person is looking at goes first (annex C.3).
        runner.jobs.enqueue(SYNC_JOB, {"connection_id": str(connection_id), "force": True},
                            priority=-1)  # fmt: skip
        runner.submit_jobs()

    def available_teams(
        request: Request, project_id: uuid.UUID, connection_id: uuid.UUID
    ) -> list[Team]:
        with database.session() as session:
            _project(session, project_id)
            try:
                connection = connections.connection_of(session, project_id, connection_id)
                connections.check_allowed(session)
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except TrackerSetupError as error:
                raise _setup_error(error) from None
            client = factory(request)(
                connections.target_of(connection), connections.connection_token(store, connection)
            )
        try:
            return client.organization_and_teams()[1]
        except TrackerError as error:
            raise _tracker_error(error) from None

    @router.get("/api/v1/projects/{project_id}/ticket-connections")
    def list_connections(project_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            _project(session, project_id)
            return {"connections": connections.list_connections(session, project_id)}

    @router.post("/api/v1/projects/{project_id}/ticket-connections", status_code=201)
    def connect(project_id: uuid.UUID, body: ConnectBody, request: Request) -> dict[str, Any]:
        with database.session() as session:
            _project(session, project_id)
            try:
                connections.check_allowed(session)
                token = connections.clean_token(body.token)
                provider = TicketProvider(body.provider)
                auth = body.auth or ("api_key" if provider is TicketProvider.LINEAR else "basic")
                target = connections.new_target(provider, auth, body.base_url, body.email)
            except TrackerSetupError as error:
                raise _setup_error(error) from None
        # The token must answer before it is stored.
        client = factory(request)(target, lambda: token)
        try:
            organization, teams = client.organization_and_teams()
        except TrackerError as error:
            raise _tracker_error(error) from None
        with database.session() as session:
            try:
                connection = connections.create_connection(
                    session, store, project_id, token, organization, _request_id(request), target
                )
            except TrackerSetupError as error:
                raise _setup_error(error) from None
            return {**connections.describe(connection, 0), "teams": _teams(teams)}

    @router.get("/api/v1/ticket-oauth/linear")
    def oauth_available() -> dict[str, Any]:
        """Whether "Connect with Linear" can be offered: it needs a registered app's client id."""
        return {"available": oauth.client_id() is not None, "callback_path": oauth.CALLBACK_PATH}

    @router.post("/api/v1/projects/{project_id}/ticket-connections/oauth/linear")
    def oauth_start(project_id: uuid.UUID, request: Request) -> dict[str, str]:
        """Starts the flow: the URL of Linear's consent page, with PKCE and a one-time state."""
        client = oauth.client_id()
        if client is None:
            raise HTTPException(status.HTTP_409_CONFLICT, "oauth_not_configured")
        with database.session() as session:
            _project(session, project_id)
            try:
                connections.check_allowed(session)
            except TrackerSetupError as error:
                raise _setup_error(error) from None
        # Back to this Scrinium, on its own fixed port: the URL registered with the app.
        redirect_uri = f"{str(request.base_url).rstrip('/')}{oauth.CALLBACK_PATH}"
        url = flows.start(str(project_id), redirect_uri, client, oauth.READ_SCOPE)
        return {"authorize_url": url}

    @router.get(oauth.CALLBACK_PATH)
    def oauth_callback(
        request: Request, state: str = "", code: str = "", error: str = ""
    ) -> RedirectResponse:
        """Linear sends the browser back here. The state must be one this Scrinium started."""

        def back(outcome: str) -> RedirectResponse:
            return RedirectResponse(f"/fuentes?linear={outcome}", status_code=303)

        pending = flows.finish(state) if state else None
        client = oauth.client_id()
        if pending is None or client is None:
            return back("expired")
        if error or not code:
            return back("denied")
        try:
            tokens = oauth.exchange(pending, code, client)
            target = connections.TrackerTarget(
                TicketProvider.LINEAR, f"https://{LINEAR_HOST}", "oauth"
            )
            organization, _teams_found = factory(request)(
                target, lambda: tokens.access
            ).organization_and_teams()
        except TrackerError:
            return back("failed")
        with database.session() as session:
            try:
                connections.create_connection(
                    session, store, uuid.UUID(pending.project_id), oauth.encode_secret(tokens),
                    organization, _request_id(request), target,
                )  # fmt: skip
            except TrackerSetupError:
                return back("failed")
        return back("connected")

    @router.get("/api/v1/projects/{project_id}/ticket-connections/{connection_id}/teams")
    def teams(project_id: uuid.UUID, connection_id: uuid.UUID, request: Request) -> dict[str, Any]:
        return {"teams": _teams(available_teams(request, project_id, connection_id))}

    @router.put("/api/v1/projects/{project_id}/ticket-connections/{connection_id}/scope")
    def scope(
        project_id: uuid.UUID, connection_id: uuid.UUID, body: ScopeBody, request: Request
    ) -> dict[str, Any]:
        available = available_teams(request, project_id, connection_id)
        with database.session() as session:
            connection = connections.connection_of(session, project_id, connection_id)
            try:
                removed = connections.set_scope(
                    session, connection, body.team_ids, available, _request_id(request)
                )
            except TrackerSetupError as error:
                raise _setup_error(error) from None
            result = {**connections.describe(connection), "tickets_removed": removed}
        enqueue_sync(connection_id)
        return result

    @router.post(
        "/api/v1/projects/{project_id}/ticket-connections/{connection_id}/sync", status_code=202
    )
    def sync(project_id: uuid.UUID, connection_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            _project(session, project_id)
            try:
                connection = connections.connection_of(session, project_id, connection_id)
                connections.check_allowed(session)
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except TrackerSetupError as error:
                raise _setup_error(error) from None
            if not connection.scope:
                raise HTTPException(status.HTTP_409_CONFLICT, "no_scope")
        enqueue_sync(connection_id)
        with database.session() as session:
            return connections.describe(
                connections.connection_of(session, project_id, connection_id)
            )

    @router.delete(
        "/api/v1/projects/{project_id}/ticket-connections/{connection_id}", status_code=200
    )
    def remove(project_id: uuid.UUID, connection_id: uuid.UUID, request: Request) -> dict[str, Any]:
        with database.session() as session:
            _project(session, project_id)
            try:
                connection = connections.connection_of(session, project_id, connection_id)
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            oauth_token = None
            if connection.auth == "oauth" and connection.secret_ref:
                try:
                    stored = store.get(connection.secret_ref)
                except Exception:
                    stored = None
                tokens = oauth.decode_secret(stored) if stored else None
                oauth_token = tokens.access if tokens else None
            summary = connections.remove_connection(
                session, store, connection, _request_id(request)
            )
        # OAuth is revoked at Linear (best effort, outside any transaction). A personal key or
        # API token stays valid in the tracker: the interface tells the person.
        revoked = oauth.revoke(oauth_token) if oauth_token else False
        return {**summary, "revoke_at_provider": not revoked, "revoked": revoked}

    @router.put("/api/v1/projects/{project_id}/ticket-connections/{connection_id}/options")
    def options(
        project_id: uuid.UUID, connection_id: uuid.UUID, body: OptionsBody, request: Request
    ) -> dict[str, Any]:
        """Reading descriptions and comments (10.6) and commenting (10.7), each off by default."""
        with database.session() as session:
            _project(session, project_id)
            try:
                connection = connections.connection_of(session, project_id, connection_id)
                connections.update_options(
                    session, connection, body.read_text, body.allow_comments, _request_id(request)
                )
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except TrackerSetupError as error:
                raise _setup_error(error) from None
            result = connections.describe(connection)
        if body.read_text and result["scope"]:
            enqueue_sync(connection_id)
        return result

    @router.get("/api/v1/projects/{project_id}/tickets/{ticket_id}/comment-draft")
    def comment_draft(project_id: uuid.UUID, ticket_id: uuid.UUID) -> dict[str, Any]:
        """A comment listing the rules this ticket touches, for a person to edit and send."""
        with database.session() as session:
            _project(session, project_id)
            ticket = session.get(Ticket, ticket_id)
            if ticket is None or ticket.project_id != project_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            connection = session.get_one(TicketConnection, ticket.connection_id)
            rules = evidence.rules_of_ticket(session, ticket)
            return {
                "ticket": evidence.describe_ticket(ticket),
                "allowed": bool((connection.settings or {}).get("allow_comments")),
                "rules": rules,
                "body": evidence.comment_text(rules),
            }

    @router.post("/api/v1/projects/{project_id}/tickets/{ticket_id}/comment", status_code=201)
    def comment(
        project_id: uuid.UUID, ticket_id: uuid.UUID, body: CommentBody, request: Request
    ) -> dict[str, Any]:
        """Writes a comment on the ticket (10.7): only on a connection that allows it, after a
        person confirmed it in the console. Audited, never the text."""
        with database.session() as session:
            _project(session, project_id)
            ticket = session.get(Ticket, ticket_id)
            if ticket is None or ticket.project_id != project_id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            connection = session.get_one(TicketConnection, ticket.connection_id)
            try:
                connections.check_allowed(session)
            except TrackerSetupError as error:
                raise _setup_error(error) from None
            if not (connection.settings or {}).get("allow_comments"):
                raise HTTPException(status.HTTP_403_FORBIDDEN, "comments_not_allowed")
            client = factory(request)(
                connections.target_of(connection), connections.connection_token(store, connection)
            )
            external_id, key, workspace_id = ticket.external_id, ticket.key, ticket.workspace_id
        try:
            client.add_comment(external_id, body.body)
        except TrackerError as error:
            raise _tracker_error(error) from None
        with database.session() as session:
            record_audit(
                session, workspace_id, "ticket.comment", "ticket", str(ticket_id),
                AuditResult.SUCCESS, _request_id(request), {"key": key, "chars": len(body.body)},
            )  # fmt: skip
        return {"sent": True, "key": key}

    @router.get("/api/v1/projects/{project_id}/tickets")
    def search(
        project_id: uuid.UUID, q: str = "", limit: int = 20, bugs: bool = False
    ) -> dict[str, Any]:
        """Tickets by key or title, to link one to a rule by hand or to triage a bug."""
        text = q.strip()[:100]
        with database.session() as session:
            _project(session, project_id)
            query = select(Ticket).where(Ticket.project_id == project_id)
            if bugs:
                query = query.where(Ticket.ticket_type == "bug")
            if text:
                escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{escaped}%"
                query = query.where(
                    or_(
                        Ticket.key.ilike(pattern, escape="\\"),
                        Ticket.title.ilike(pattern, escape="\\"),
                    )
                )
            rows = session.scalars(
                query.order_by(Ticket.tombstone, Ticket.changed_at.desc()).limit(
                    max(1, min(limit, 50))
                )
            )
            return {"tickets": [evidence.describe_ticket(ticket) for ticket in rows]}

    @router.get("/api/v1/projects/{project_id}/tickets/{ticket_id}/triage")
    def triage(project_id: uuid.UUID, ticket_id: uuid.UUID) -> dict[str, Any]:
        """A bug ticket to its commits, files, rules and similar resolved bugs (phase 10.5).
        Local only: the database and Git."""
        with database.session() as session:
            _project(session, project_id)
            try:
                return triage_ticket(session, project_id, ticket_id)
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None

    def _rule(session: Any, project_id: uuid.UUID, entity_id: uuid.UUID) -> KnowledgeEntity:
        project = _project(session, project_id)
        try:
            return get_in_project(session, KnowledgeEntity, project, entity_id)
        except (KnowledgeNotFoundError, LookupError):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None

    @router.get("/api/v1/projects/{project_id}/rules/{entity_id}/tickets")
    def rule_tickets(project_id: uuid.UUID, entity_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            return evidence.rule_tickets(session, _rule(session, project_id, entity_id))

    @router.post("/api/v1/projects/{project_id}/rules/{entity_id}/tickets", status_code=201)
    def link(
        project_id: uuid.UUID, entity_id: uuid.UUID, body: LinkBody, request: Request
    ) -> dict[str, Any]:
        with database.session() as session:
            entity = _rule(session, project_id, entity_id)
            try:
                evidence.confirm_link(session, entity, body.ticket_id, _request_id(request))
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            return evidence.rule_tickets(session, entity)

    @router.post("/api/v1/projects/{project_id}/rules/{entity_id}/tickets/{ticket_id}/dismiss")
    def dismiss(
        project_id: uuid.UUID, entity_id: uuid.UUID, ticket_id: uuid.UUID, request: Request
    ) -> dict[str, Any]:
        """A proposed (inferred) ticket that does not explain the rule: not proposed again."""
        with database.session() as session:
            entity = _rule(session, project_id, entity_id)
            try:
                evidence.dismiss_proposal(session, entity, ticket_id, _request_id(request))
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            session.flush()
            return evidence.rule_tickets(session, entity)

    @router.delete("/api/v1/projects/{project_id}/rules/{entity_id}/tickets/{ticket_id}")
    def unlink(
        project_id: uuid.UUID, entity_id: uuid.UUID, ticket_id: uuid.UUID, request: Request
    ) -> Response:
        with database.session() as session:
            entity = _rule(session, project_id, entity_id)
            if not evidence.remove_link(session, entity, ticket_id, _request_id(request)):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
