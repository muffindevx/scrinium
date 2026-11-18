"""Ticket trackers connected to a project and the tickets behind a rule (phase 10, ADR-014).

Calls to the tracker happen outside any database transaction: a slow provider never holds
SQLite's writer. The token never comes back in an answer, a log or an error.
"""

import uuid
from typing import Any, Final, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
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
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import KnowledgeEntity, Project, Ticket
from scrinium.infrastructure.tickets.linear import (
    Team,
    TrackerAuthError,
    TrackerError,
    TrackerRateLimitedError,
)

SYNC_JOB: Final = "ticket_sync"
_CODES: Final = {
    "keyring_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "isolated_mode": status.HTTP_403_FORBIDDEN,
    "isolated_managed": status.HTTP_403_FORBIDDEN,
}


class ConnectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["linear"] = "linear"
    token: str = Field(min_length=1, max_length=connections.MAX_TOKEN_LENGTH)


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
                connections.connection_token(store, connection), connection.auth
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
            except TrackerSetupError as error:
                raise _setup_error(error) from None
        # The token must answer before it is stored.
        client = factory(request)(lambda: token, "api_key")
        try:
            organization, teams = client.organization_and_teams()
        except TrackerError as error:
            raise _tracker_error(error) from None
        with database.session() as session:
            try:
                connection = connections.create_connection(
                    session, store, project_id, token, organization, _request_id(request)
                )
            except TrackerSetupError as error:
                raise _setup_error(error) from None
            return {**connections.describe(connection, 0), "teams": _teams(teams)}

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
            summary = connections.remove_connection(
                session, store, connection, _request_id(request)
            )
        # A personal key is still valid in the tracker: the interface tells the person.
        return {**summary, "revoke_at_provider": True}

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
