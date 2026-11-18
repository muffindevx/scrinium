"""HTTP routes of the local runtime."""

import os
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session as DbSession

from scrinium import __version__
from scrinium.api.middleware import SESSION_COOKIE
from scrinium.api.questions import build_questions_router
from scrinium.api.search import build_search_router
from scrinium.api.sources import build_sources_router
from scrinium.application import workspaces
from scrinium.application.ai.answering import QuestionRunner
from scrinium.application.ai.providers import remote_destinations
from scrinium.application.ai.service import LlmService
from scrinium.application.ai.setup import read_setup
from scrinium.application.engine import engine_info
from scrinium.application.environment import LocalEnvironment
from scrinium.application.ingestion.sources import IngestionRunner
from scrinium.application.privacy import pending_purge
from scrinium.core.config import LOOPBACK_HOST, RuntimeConfig
from scrinium.core.data_dir import DataDirPermissions
from scrinium.core.disk_encryption import DiskEncryption
from scrinium.core.session import LocalSessionManager, Session
from scrinium.domain.enums import ProjectStatus, WorkspaceType
from scrinium.infrastructure.sqlite.database import Database


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    # So `scrinium start` knows whether the busy port belongs to another Scrinium.
    service: Literal["scrinium"] = "scrinium"


class ReadyResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["ok", "unavailable"]
    # R8: the vector index is required; the models are reported, not required (the writing
    # model may be a provider, and search falls back to words without embeddings).
    vectors: Literal["ok", "unavailable"] = "ok"
    embeddings: str = "unavailable"
    chat: str = "unavailable"
    purges_pending: int = 0


class SessionResponse(BaseModel):
    csrf_token: str


class RuntimeStatus(BaseModel):
    mode: Literal["local"] = "local"
    version: str
    bind_address: str
    port: int
    running_as_admin: bool


ModelState = Literal["ready", "model_missing", "unavailable"]


class ProviderStatus(BaseModel):
    llm: ModelState
    llm_model: str
    embeddings: ModelState
    embedding_model: str


class LlmCheckResponse(BaseModel):
    status: Literal["ok", "model_missing", "unavailable", "failed"]
    model: str
    duration_ms: int


class StorageStatus(BaseModel):
    data_dir_permissions: DataDirPermissions
    disk_encryption: DiskEncryption
    secret_store: Literal["available", "unavailable"]
    secret_store_backend: str | None


class SecurityStatusResponse(BaseModel):
    runtime: RuntimeStatus
    providers: ProviderStatus
    storage: StorageStatus
    ai_mode: Literal["local_only", "remote_allowed", "isolated"] = "local_only"
    # ADR-013: "isolated" blocks every egress; `profile_managed` if the environment forces it.
    profile: Literal["standard", "isolated"] = "standard"
    profile_managed: bool = False
    # The first-run choice: provider, local_server, isolated, or None if not made yet.
    ai_setup: str | None = None
    connected_sources: int
    limits: list[str]


class DataFlowResponse(BaseModel):
    ai_mode: Literal["local_only", "remote_allowed", "isolated"] = "local_only"
    remote_ai_providers: list[str]
    outbound_destinations: list[str]
    stored_locally: list[str]


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    workspace_type: WorkspaceType
    created_at: datetime


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: str
    project_key: str
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]


class CreateProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    project_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=workspaces.PROJECT_KEY_MAX_LENGTH,
        pattern=workspaces.PROJECT_KEY_PATTERN,
    )
    description: str = Field(default="", max_length=2000)


_INVALID_LAUNCH_PAGE = """<!doctype html><html lang="es"><meta charset="utf-8">
<title>Scrinium</title><h1>Enlace de arranque no valido</h1>
<p>Este enlace ya se uso o caduco. Reinicia Scrinium con <code>scrinium start</code>.</p></html>"""


def _set_session_cookie(response: Response, session_id: str) -> None:
    # No Secure: Scrinium uses http on loopback, and browsers do not treat that cookie alike.
    response.set_cookie(
        SESSION_COOKIE, session_id, httponly=True, samesite="strict", path="/", secure=False
    )


def _running_as_admin() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return geteuid is not None and geteuid() == 0


def _current_session(request: Request) -> Session:
    session: Session | None = getattr(request.state, "session", None)
    if session is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session_required")
    return session


_DISK_ENCRYPTION_LIMITS = {
    DiskEncryption.ENCRYPTED: (
        "El cifrado de disco protege el equipo apagado, no uno encendido y desbloqueado."
    ),
    DiskEncryption.NOT_DETECTED: (
        "No se detecto cifrado de disco: quien se lleve el equipo podria leer los datos de "
        "Scrinium."
    ),
    DiskEncryption.UNKNOWN: "No se pudo comprobar si el disco esta cifrado.",
}


def _limits(environment: LocalEnvironment) -> list[str]:
    limits = [
        "No detecta malware ni protege frente a programas que ya controlan tu cuenta.",
        "Scrinium no cifra su base de datos: depende del cifrado de disco del sistema.",
        _DISK_ENCRYPTION_LIMITS[environment.disk_encryption],
    ]
    if environment.secret_store.backend_name is None:
        limits.append(
            "No hay un keyring seguro disponible: no se podran conectar fuentes que necesiten "
            "credenciales. En Linux hace falta un servicio de Secret Service en marcha, como "
            "GNOME Keyring o KWallet, dentro de una sesion de escritorio."
        )
    return limits


def _request_id(request: Request) -> str | None:
    request_id: str | None = getattr(request.state, "request_id", None)
    return request_id


def _ai_mode(db: DbSession) -> Literal["local_only", "remote_allowed", "isolated"]:
    if read_setup(db).isolated:
        return "isolated"
    return "remote_allowed" if remote_destinations(db) else "local_only"


def build_router(
    config: RuntimeConfig,
    sessions: LocalSessionManager,
    database: Database,
    environment: LocalEnvironment,
    runner: IngestionRunner,
    llm: LlmService,
    questions: QuestionRunner,
) -> APIRouter:
    router = APIRouter()
    router.include_router(build_sources_router(config, database, runner))
    router.include_router(build_search_router(database, runner))
    router.include_router(build_questions_router(database, questions))

    def db_session() -> Iterator[DbSession]:
        with database.session() as session:
            yield session

    Db = Annotated[DbSession, Depends(db_session)]

    @router.get("/health")
    def health() -> HealthResponse:
        return HealthResponse()

    @router.get("/ready", response_model=ReadyResponse)
    def ready() -> JSONResponse:
        database_ok = database.is_ready()
        vectors_ok = runner.indexer.vector_index.is_ready()
        ok = database_ok and vectors_ok
        body = ReadyResponse(
            status="ready" if ok else "not_ready",
            database="ok" if database_ok else "unavailable",
            vectors="ok" if vectors_ok else "unavailable",
            embeddings=runner.indexer.provider_status().value,
            chat=llm.status().value,
            purges_pending=(
                len(pending_purge.pending(config.data_dir)) if config.data_dir.is_dir() else 0
            ),
        )
        return JSONResponse(body.model_dump(), status_code=200 if ok else 503)

    @router.get("/auth/launch", response_model=None)
    def launch(token: Annotated[str, Query(max_length=128)] = "") -> Response:
        session_id = sessions.redeem_launch_token(token)
        if session_id is None:
            return HTMLResponse(_INVALID_LAUNCH_PAGE, status_code=status.HTTP_403_FORBIDDEN)
        response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
        _set_session_cookie(response, session_id)
        return response

    @router.get("/api/v1/session")
    def read_session(request: Request) -> SessionResponse:
        return SessionResponse(csrf_token=_current_session(request).csrf_token)

    @router.post("/api/v1/session/rotate")
    def rotate_session(
        response: Response,
        session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
    ) -> SessionResponse:
        new_id = sessions.rotate(session_cookie) if session_cookie else None
        session = sessions.validate(new_id)
        if new_id is None or session is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "session_required")
        _set_session_cookie(response, new_id)
        return SessionResponse(csrf_token=session.csrf_token)

    @router.post("/api/v1/session/logout", status_code=status.HTTP_204_NO_CONTENT)
    def logout(response: Response) -> None:
        sessions.end()
        response.delete_cookie(SESSION_COOKIE, httponly=True, samesite="strict", path="/")

    @router.get("/api/v1/workspace")
    def read_workspace(db: Db) -> WorkspaceResponse:
        return WorkspaceResponse.model_validate(workspaces.local_workspace(db))

    @router.get("/api/v1/projects")
    def list_projects(db: Db) -> ProjectListResponse:
        projects = workspaces.list_projects(db, workspaces.local_workspace(db))
        return ProjectListResponse(projects=[ProjectResponse.model_validate(p) for p in projects])

    @router.post("/api/v1/projects", status_code=status.HTTP_201_CREATED)
    def create_project(body: CreateProjectRequest, request: Request, db: Db) -> ProjectResponse:
        try:
            project = workspaces.create_project(
                db,
                workspaces.local_workspace(db),
                name=body.name,
                project_key=body.project_key,
                description=body.description,
                request_id=_request_id(request),
            )
        except workspaces.ProjectKeyTakenError:
            raise HTTPException(status.HTTP_409_CONFLICT, "project_key_taken") from None
        return ProjectResponse.model_validate(project)

    @router.get("/api/v1/system/security-status")
    def security_status(db: Db) -> SecurityStatusResponse:
        backend = environment.secret_store.backend_name
        setup = read_setup(db)
        return SecurityStatusResponse(
            runtime=RuntimeStatus(
                version=__version__,
                bind_address=LOOPBACK_HOST,
                port=config.port,
                running_as_admin=_running_as_admin(),
            ),
            providers=ProviderStatus(
                llm=llm.status().value,
                llm_model=llm.provider.model_id,
                embeddings=runner.indexer.provider_status().value,
                embedding_model=runner.indexer.provider.model_id,
            ),
            storage=StorageStatus(
                data_dir_permissions=environment.data_dir_permissions,
                disk_encryption=environment.disk_encryption,
                secret_store="available" if backend else "unavailable",
                secret_store_backend=backend,
            ),
            ai_mode=_ai_mode(db),
            profile="isolated" if setup.isolated else "standard",
            profile_managed=setup.managed,
            ai_setup=setup.choice.value if setup.choice else None,
            connected_sources=workspaces.count_connections(db, workspaces.local_workspace(db)),
            limits=_limits(environment),
        )

    @router.get("/api/v1/system/engine")
    def engine() -> dict[str, Any]:
        """Local AI engine, memory and installed models. Read only."""
        return engine_info(config)

    # POST with CSRF: it uses the GPU for seconds, and another site must not be able to trigger it.
    @router.post("/api/v1/llm/check")
    def check_llm() -> LlmCheckResponse:
        result = llm.check()
        return LlmCheckResponse(
            status=result.status,  # type: ignore[arg-type]
            model=llm.provider.model_id,
            duration_ms=result.duration_ms,
        )

    @router.get("/api/v1/privacy/data-flow")
    def data_flow(db: Db) -> DataFlowResponse:
        # Embeddings and search never leave; only the tasks a project consented to (ADR-010).
        remote = remote_destinations(db)
        return DataFlowResponse(
            ai_mode=_ai_mode(db),
            remote_ai_providers=remote,
            outbound_destinations=remote,
            stored_locally=[
                "Workspace, proyectos y registro de auditoria, en SQLite",
                "Texto de los archivos indexados de tus fuentes, en SQLite",
                "Fragmentos e indice de busqueda textual (FTS5), en SQLite",
                "Vectores de los fragmentos (sqlite-vec), en la misma base SQLite",
            ],
        )

    return router
