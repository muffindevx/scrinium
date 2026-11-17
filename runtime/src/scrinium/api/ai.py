"""Remote model providers and each project's consent to use them (ADR-010).

The API key is accepted once, on registration, and never returned: responses carry only
whether a key is set and its last four characters.
"""

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from scrinium.application.ai import providers
from scrinium.application.ai.connection import check_connection
from scrinium.application.ai.language import read_language, set_language
from scrinium.application.ai.providers import ProviderError
from scrinium.application.ai.routing import build_adapter, tokens_spent
from scrinium.application.ai.setup import (
    SetupChoice,
    SetupError,
    choose,
    clean_local_server,
    read_setup,
)
from scrinium.application.environment import LocalEnvironment
from scrinium.application.ingestion import sources
from scrinium.application.workspaces import record_audit
from scrinium.domain.enums import AiTask, AuditResult, ProviderKind
from scrinium.infrastructure.llm.switchable import SwitchableChat, local_server_chat
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import ModelProvider, Project, utc_now


class ProviderBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    kind: ProviderKind
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = Field(min_length=1, max_length=providers.MAX_KEY_LENGTH, repr=False)


class ProviderPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=100)
    kind: ProviderKind | None = None
    base_url: str | None = Field(default=None, min_length=1, max_length=500)
    # Only to replace it; empty or absent keeps the one in the keyring.
    api_key: str | None = Field(default=None, max_length=providers.MAX_KEY_LENGTH, repr=False)


class ModelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)


class SettingsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    never_send: list[str] = Field(default_factory=list, max_length=100)
    remote_token_budget: int | None = Field(default=None, ge=0)
    mask_personal: bool = False
    mask_literals: bool = False


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)


class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: uuid.UUID
    model: str = Field(min_length=1, max_length=200)
    tasks: list[AiTask] = Field(min_length=1, max_length=len(AiTask))
    retention_acknowledged: bool
    # False: only allowed, to choose it for a run; the tasks keep their default model.
    make_default: bool = True


class LocalServerBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=200)


class SetupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    choice: SetupChoice
    local_server: LocalServerBody | None = None


class LanguageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: Literal["es", "en"]


class DefaultBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_id: uuid.UUID | None = None
    model: str | None = Field(default=None, max_length=200)


_CODES = {
    "keyring_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
    "name_taken": status.HTTP_409_CONFLICT,
    "isolated_mode": status.HTTP_403_FORBIDDEN,
    "isolated_managed": status.HTTP_403_FORBIDDEN,
}


def _error(error: ProviderError | SetupError) -> HTTPException:
    return HTTPException(_CODES.get(error.code, status.HTTP_422_UNPROCESSABLE_CONTENT), error.code)


def _project(session: Session, project_id: uuid.UUID) -> Project:
    try:
        return sources.project_of(session, project_id)
    except sources.SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def apply_setup(database: Database, local_chat: SwitchableChat | None) -> None:
    """Points the local chat model at the chosen local server, or back to the default."""
    if local_chat is None:
        return
    with database.session() as session:
        server = read_setup(session).local_server
    local_chat.use(local_server_chat(server.url, server.model) if server else None)


def build_ai_router(
    database: Database, environment: LocalEnvironment, local_chat: SwitchableChat | None = None
) -> APIRouter:
    router = APIRouter()
    store = environment.secret_store

    @router.get("/api/v1/workspace/language")
    def get_language() -> dict[str, str]:
        """The console's language and the models' (phase 5.5)."""
        with database.session() as session:
            return {"language": read_language(session)}

    @router.put("/api/v1/workspace/language")
    def put_language(body: LanguageBody, request: Request) -> dict[str, str]:
        with database.session() as session:
            return {"language": set_language(session, body.language, _request_id(request))}

    @router.get("/api/v1/ai-setup")
    def get_setup() -> dict[str, Any]:
        """The first-run choice (ADR-013), and the local model in use now."""
        with database.session() as session:
            setup = read_setup(session).as_dict()
        if local_chat is not None:
            setup["local_model"] = local_chat.model_id
        return setup

    @router.put("/api/v1/ai-setup")
    def put_setup(body: SetupBody, request: Request) -> dict[str, Any]:
        try:
            server = (
                clean_local_server(body.local_server.url, body.local_server.model)
                if body.choice is SetupChoice.LOCAL_SERVER and body.local_server
                else None
            )
            with database.session() as session:
                setup, revoked = choose(session, body.choice, server, _request_id(request))
        except SetupError as error:
            raise _error(error) from None
        apply_setup(database, local_chat)
        return {**setup.as_dict(), "consents_revoked": revoked}

    @router.post("/api/v1/ai-setup/check-local")
    def check_local(body: LocalServerBody) -> dict[str, Any]:
        """Does this loopback server answer, and does it have the model? Nothing leaves."""
        try:
            server = clean_local_server(body.url, body.model)
        except SetupError as error:
            raise _error(error) from None
        return {"status": local_server_chat(server.url, server.model).status().value}

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

    @router.patch("/api/v1/model-providers/{provider_id}")
    def update(provider_id: uuid.UUID, body: ProviderPatch, request: Request) -> dict[str, Any]:
        with database.session() as session:
            try:
                provider, revoked = providers.update_provider(
                    session, store, provider_id, name=body.name, kind=body.kind,
                    base_url=body.base_url, api_key=body.api_key or None,
                    request_id=_request_id(request),
                )  # fmt: skip
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except ProviderError as error:
                raise _error(error) from None
            return {**providers.describe(provider), "consents_revoked": revoked}

    @router.post("/api/v1/model-providers/{provider_id}/models", status_code=201)
    def add_model(provider_id: uuid.UUID, body: ModelBody, request: Request) -> dict[str, Any]:
        with database.session() as session:
            try:
                row = providers.add_model(session, provider_id, body.model, _request_id(request))
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except ProviderError as error:
                raise _error(error) from None
            return {"id": str(row.id), "model": row.model}

    @router.delete("/api/v1/model-providers/{provider_id}/models/{model_id}")
    def remove_model(
        provider_id: uuid.UUID, model_id: uuid.UUID, request: Request
    ) -> dict[str, Any]:
        with database.session() as session:
            try:
                revoked = providers.remove_model(
                    session, provider_id, model_id, _request_id(request)
                )
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            return {"consents_revoked": revoked}

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

    @router.post("/api/v1/model-providers/{provider_id}/check")
    def check(provider_id: uuid.UUID, body: CheckBody, request: Request) -> dict[str, Any]:
        """ "Probar conexión": one short fixed request, nothing from any project."""
        with database.session() as session:
            try:
                providers.refuse_if_isolated(session)
            except ProviderError as error:
                raise _error(error) from None
            provider = session.get(ModelProvider, provider_id)
            if provider is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            session.expunge(provider)
        if provider.secret_ref is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "key_missing")
        adapter = build_adapter(
            provider, body.model.strip(), AiTask.QUESTIONS,
            lambda: providers.provider_key(store, provider, provider.host),
        )  # fmt: skip
        try:
            result = check_connection(adapter)
        except ProviderError as error:
            raise _error(error) from None
        with database.session() as session:
            record_audit(
                session, provider.workspace_id, "model_provider.check", "model_provider",
                str(provider.id), AuditResult.SUCCESS, _request_id(request),
                {"model": body.model.strip(), "status": result["status"],
                 "reason": result["reason"], "http_status": result.get("http_status")},
            )  # fmt: skip
        return result

    @router.get("/api/v1/projects/{project_id}/ai")
    def project_ai(project_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            project = _project(session, project_id)
            spent = tokens_spent(session, project.id, utc_now())
            return providers.project_ai(session, project, spent)

    @router.get("/api/v1/projects/{project_id}/ai/requests")
    def requests(project_id: uuid.UUID, limit: int = 50) -> dict[str, Any]:
        """What this project sent to remote models, newest first (never the content)."""
        with database.session() as session:
            project = _project(session, project_id)
            return {"requests": providers.recent_requests(session, project, limit)}

    @router.put("/api/v1/projects/{project_id}/ai/settings")
    def settings(project_id: uuid.UUID, body: SettingsBody, request: Request) -> dict[str, Any]:
        """Paths this project never sends to a remote model, and its monthly token budget."""
        with database.session() as session:
            project = _project(session, project_id)
            try:
                providers.update_settings(
                    session, project, body.never_send, body.remote_token_budget,
                    _request_id(request), body.mask_personal, body.mask_literals,
                )  # fmt: skip
            except ProviderError as error:
                raise _error(error) from None
            spent = tokens_spent(session, project.id, utc_now())
            return providers.project_ai(session, project, spent)

    @router.post("/api/v1/projects/{project_id}/ai/consents", status_code=201)
    def consent(project_id: uuid.UUID, body: ConsentBody, request: Request) -> dict[str, Any]:
        with database.session() as session:
            project = _project(session, project_id)
            try:
                given = providers.give_consent(
                    session, project, body.provider_id, body.model, body.tasks,
                    body.retention_acknowledged, date.today(), _request_id(request),
                    make_default=body.make_default,
                )  # fmt: skip
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except ProviderError as error:
                raise _error(error) from None
            return providers.describe_consent(given)

    @router.put("/api/v1/projects/{project_id}/ai/tasks/{task}")
    def set_default(
        project_id: uuid.UUID, task: AiTask, body: DefaultBody, request: Request
    ) -> dict[str, Any]:
        """The model this task uses by default: local, or one already allowed for it."""
        with database.session() as session:
            project = _project(session, project_id)
            try:
                providers.set_default(
                    session, project, task, body.provider_id, body.model, _request_id(request)
                )
            except ProviderError as error:
                raise _error(error) from None
            spent = tokens_spent(session, project.id, utc_now())
            return providers.project_ai(session, project, spent)

    @router.delete("/api/v1/projects/{project_id}/ai/tasks/{task}")
    def task_to_local(project_id: uuid.UUID, task: AiTask, request: Request) -> dict[str, Any]:
        """This task uses the local model by default; the permissions stay."""
        with database.session() as session:
            project = _project(session, project_id)
            providers.set_default(session, project, task, None, None, _request_id(request))
            spent = tokens_spent(session, project.id, utc_now())
            return providers.project_ai(session, project, spent)

    @router.delete("/api/v1/projects/{project_id}/ai/consents/{consent_id}")
    def revoke_one(
        project_id: uuid.UUID, consent_id: uuid.UUID, request: Request
    ) -> dict[str, Any]:
        """One permission withdrawn; its tasks that used it by default go back to local."""
        with database.session() as session:
            project = _project(session, project_id)
            try:
                providers.revoke_consent(session, project, consent_id, _request_id(request))
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            spent = tokens_spent(session, project.id, utc_now())
            return providers.project_ai(session, project, spent)

    @router.delete("/api/v1/projects/{project_id}/ai/consents")
    def revoke(project_id: uuid.UUID, request: Request) -> dict[str, Any]:
        """Back to local. What the provider already received cannot be recalled."""
        with database.session() as session:
            project = _project(session, project_id)
            revoked = providers.revoke_all(session, project, _request_id(request))
            return {"revoked": revoked, "ai_mode": project.ai_mode.value}

    return router
