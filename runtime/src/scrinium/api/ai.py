"""Remote model providers and each project's consent to use them (ADR-010).

The API key is accepted once, on registration, and never returned: responses carry only
whether a key is set and its last four characters.
"""

import uuid
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from scrinium.application.ai import providers
from scrinium.application.ai.providers import ProviderError
from scrinium.application.environment import LocalEnvironment
from scrinium.application.ingestion import sources
from scrinium.domain.enums import AiTask, ProviderKind
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import ModelProvider, Project


class ProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1, max_length=providers.MAX_KEY_LENGTH, repr=False)


class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: uuid.UUID
    model: str = Field(min_length=1, max_length=200)
    tasks: list[AiTask] = Field(min_length=1, max_length=len(AiTask))
    retention_acknowledged: bool


_CODES = {
    "keyring_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "name_taken": status.HTTP_409_CONFLICT,
}


def _error(error: ProviderError) -> HTTPException:
    return HTTPException(_CODES.get(error.code, status.HTTP_422_UNPROCESSABLE_CONTENT), error.code)


def _project(session: Session, project_id: uuid.UUID) -> Project:
    try:
        return sources.project_of(session, project_id)
    except sources.SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def build_ai_router(database: Database, environment: LocalEnvironment) -> APIRouter:
    router = APIRouter()
    store = environment.secret_store

    @router.get("/api/v1/model-providers")
    def list_providers() -> dict[str, Any]:
        with database.session() as session:
            return {
                "providers": providers.list_providers(session),
                "keyring": store.backend_name is not None,
            }

    @router.post("/api/v1/model-providers", status_code=status.HTTP_201_CREATED)
    def register(body: ProviderBody, request: Request) -> dict[str, Any]:
        with database.session() as session:
            try:
                provider = providers.register_provider(
                    session, store, body.name, body.kind, body.base_url, body.api_key,
                    _request_id(request),
                )  # fmt: skip
            except ProviderError as error:
                raise _error(error) from None
            return providers.describe(provider)

    @router.delete("/api/v1/model-providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
    def remove(provider_id: uuid.UUID, request: Request) -> Response:
        with database.session() as session:
            try:
                providers.remove_provider(session, store, provider_id, _request_id(request))
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except ProviderError as error:
                raise _error(error) from None
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/api/v1/model-providers/{provider_id}/retention")
    def retention(provider_id: uuid.UUID, model: str) -> dict[str, Any]:
        """What the provider says it keeps, for this model: shown before consenting."""
        with database.session() as session:
            provider = session.get(ModelProvider, provider_id)
            if provider is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            return providers.retention_notice(provider, model[:200], date.today())

    @router.get("/api/v1/projects/{project_id}/ai")
    def project_ai(project_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            project = _project(session, project_id)
            return providers.project_ai(session, project)

    @router.post("/api/v1/projects/{project_id}/ai/consents", status_code=201)
    def consent(project_id: uuid.UUID, body: ConsentBody, request: Request) -> dict[str, Any]:
        with database.session() as session:
            project = _project(session, project_id)
            try:
                given = providers.give_consent(
                    session, project, body.provider_id, body.model, body.tasks,
                    body.retention_acknowledged, date.today(), _request_id(request),
                )  # fmt: skip
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except ProviderError as error:
                raise _error(error) from None
            return providers.describe_consent(given)

    @router.delete("/api/v1/projects/{project_id}/ai/consents")
    def revoke(project_id: uuid.UUID, request: Request) -> dict[str, Any]:
        """Back to local. What the provider already received cannot be recalled."""
        with database.session() as session:
            project = _project(session, project_id)
            revoked = providers.revoke_all(session, project, _request_id(request))
            return {"revoked": revoked, "ai_mode": project.ai_mode.value}

    return router
