"""Building the local runtime's ASGI application."""

import contextlib
import uuid
from collections.abc import Callable
from concurrent.futures import Executor

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from scrinium import __version__
from scrinium.api.ai import apply_setup, build_ai_router
from scrinium.api.analyses import build_analyses_router
from scrinium.api.changes import build_changes_router
from scrinium.api.control import build_control_router
from scrinium.api.errors import install_error_handlers
from scrinium.api.knowledge import build_knowledge_router
from scrinium.api.middleware import (
    BodyLimitMiddleware,
    ETagMiddleware,
    LocalGuardMiddleware,
    RequestContextMiddleware,
)
from scrinium.api.privacy import build_privacy_router
from scrinium.api.routes import build_router
from scrinium.api.static import InterfaceFiles
from scrinium.api.tickets import SYNC_JOB, build_tickets_router
from scrinium.application.ai.answering import QuestionRunner, mark_interrupted_questions
from scrinium.application.ai.memory_guard import MemoryGuard
from scrinium.application.ai.routing import Router
from scrinium.application.ai.service import LlmService
from scrinium.application.analysis import extraction
from scrinium.application.analysis.runs import AnalysisRunner
from scrinium.application.environment import LocalEnvironment
from scrinium.application.ingestion.sources import IngestionRunner, mark_interrupted_runs
from scrinium.application.jobs import JobQueue
from scrinium.application.knowledge.coverage import prepare_coverage
from scrinium.application.retrieval.indexing import Indexer
from scrinium.application.trackers.connections import default_client
from scrinium.application.trackers.poller import TicketPoller
from scrinium.application.trackers.sync import sync_connection
from scrinium.application.workspaces import ensure_local_workspace
from scrinium.core.config import ModelProvider, RuntimeConfig
from scrinium.core.data_dir import prepare_data_dir
from scrinium.core.disk_encryption import check_disk_encryption
from scrinium.core.session import LocalSessionManager
from scrinium.infrastructure.embeddings.base import EmbeddingProvider
from scrinium.infrastructure.embeddings.ollama import OllamaEmbeddings
from scrinium.infrastructure.embeddings.openai_compatible import OpenAICompatibleEmbeddings
from scrinium.infrastructure.llm.base import LLMProvider
from scrinium.infrastructure.llm.load_order import ChatFirst
from scrinium.infrastructure.llm.ollama import OllamaChat
from scrinium.infrastructure.llm.openai_compatible import OpenAICompatibleChat
from scrinium.infrastructure.llm.switchable import SwitchableChat
from scrinium.infrastructure.local_http import LocalHttpClient, ModelError
from scrinium.infrastructure.secret_store import KeyringSecretStore, SecretStore
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.tools.manifest import CHAT_MODEL, EMBEDDING_MODEL
from scrinium.infrastructure.vector_index.sqlite_vec import SqliteVecIndex, retire_legacy_vectors


def open_runtime(
    config: RuntimeConfig, secret_store: SecretStore | None = None
) -> tuple[Database, LocalEnvironment]:
    """Prepares the data directory, opens SQLite and checks the machine.

    Raises `UnsafeDataDirError` before writing anything if the directory cannot be private.
    """
    permissions = prepare_data_dir(config.data_dir)
    database = Database.open(config.data_dir)
    with database.session() as session:
        ensure_local_workspace(session)
        mark_interrupted_runs(session)
        mark_interrupted_questions(session)
    environment = LocalEnvironment(
        data_dir_permissions=permissions,
        disk_encryption=check_disk_encryption(config.data_dir),
        secret_store=secret_store or KeyringSecretStore.from_system(),
    )
    return database, environment


def local_models(config: RuntimeConfig) -> tuple[EmbeddingProvider, LLMProvider]:
    if config.model_provider == ModelProvider.OLLAMA:
        return (
            OllamaEmbeddings(config.ollama_url, config.embedding_model),
            OllamaChat(config.ollama_url, config.llm_model),
        )
    url, key = config.llama_url, config.llama_api_key
    if url is None:
        return (
            OpenAICompatibleEmbeddings(url, EMBEDDING_MODEL.name, key),
            OpenAICompatibleChat(url, CHAT_MODEL.name, key),
        )
    order = ChatFirst(LocalHttpClient(url, key), CHAT_MODEL.name, EMBEDDING_MODEL.name)
    return (
        OpenAICompatibleEmbeddings(
            url, EMBEDDING_MODEL.name, key, before_request=order.before_embeddings
        ),
        OpenAICompatibleChat(url, CHAT_MODEL.name, key, before_request=order.before_chat),
    )


_RELEASE_TIMEOUT_SECONDS = 10


def model_releaser(config: RuntimeConfig) -> Callable[[], None]:
    """Unloads the models to give their memory back. They reload with the next
    request that uses them: llama-server in router mode and Ollama do it by themselves."""

    def release() -> None:
        if config.model_provider == ModelProvider.OLLAMA:
            client = LocalHttpClient(config.ollama_url)
            calls = [
                ("/api/generate", {"model": config.llm_model, "keep_alive": 0}),
                ("/api/embed", {"model": config.embedding_model, "input": [], "keep_alive": 0}),
            ]
        elif config.llama_url:
            client = LocalHttpClient(config.llama_url, config.llama_api_key)
            calls = [("/models/unload", {"model": m.name}) for m in (CHAT_MODEL, EMBEDDING_MODEL)]
        else:
            return
        for path, body in calls:
            # A model that was no longer loaded answers with an error: it does not matter.
            with contextlib.suppress(ModelError):
                client.request(path, body, _RELEASE_TIMEOUT_SECONDS)

    return release


def create_app(
    config: RuntimeConfig,
    sessions: LocalSessionManager,
    database: Database,
    environment: LocalEnvironment,
    runner: IngestionRunner | None = None,
    llm: LLMProvider | None = None,
    question_executor: Executor | None = None,
    memory: MemoryGuard | None = None,
) -> FastAPI:
    """`runner`, `llm` and `memory` exist for tests; by default, this machine's models
    and the memory guard with real memory. With a test `llm` and no `memory`,
    the guard holds nothing back: tests do not depend on the machine's free memory."""
    # No /docs or /openapi.json: nobody else consumes this API and it need not be described.
    app = FastAPI(
        title="Scrinium", version=__version__, docs_url=None, redoc_url=None, openapi_url=None
    )
    # The limit sits inside the guard: a request without a session is rejected unread.
    # Innermost: it fingerprints the answer the routes built, after the guard let it through.
    app.add_middleware(ETagMiddleware)
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(LocalGuardMiddleware, config=config, sessions=sessions)
    # The last one added is the outermost: it also covers the guard's rejections.
    app.add_middleware(RequestContextMiddleware)
    embeddings, local_llm = local_models(config)
    if runner is None:
        # Phase 9.1: vectors in SQLite; LanceDB's old folder is deleted and rebuilt here.
        retire_legacy_vectors(database, config.data_dir)
        indexer = Indexer(database, embeddings, SqliteVecIndex(database))
        runner = IngestionRunner(database, config.data_dir, indexer)
    app.state.ingestion = runner
    # require_local (inside LlmService) rejects any provider that is not local.
    if memory is None:
        memory = (
            MemoryGuard(release_models=model_releaser(config))
            if llm is None
            else MemoryGuard(read_available=lambda: None)
        )
    # Remote models only for the tasks a project consented to (ADR-010).
    # The local model can be pointed at another loopback server from the first run (ADR-013).
    local_chat = SwitchableChat(llm or local_llm)
    apply_setup(database, local_chat if llm is None else None, runner, environment.secret_store)
    llm_service = LlmService(local_chat, memory, Router(database, environment.secret_store))
    app.state.questions = QuestionRunner(database, runner.indexer, llm_service, question_executor)
    app.state.analyses = AnalysisRunner(database, llm_service, question_executor)
    analyses = app.state.analyses
    analyses.indexer = runner.indexer

    def after_sync(sync_run_id: uuid.UUID) -> object:
        # Coverage's decisions per file revision (phase 8.1), while the files are fresh.
        prepare_coverage(database, sync_run_id)
        return extraction.refresh_after_sync(database, llm_service, analyses, sync_run_id)

    runner.after_sync = after_sync
    runner.jobs = JobQueue(
        database,
        {
            "after_sync": lambda payload: after_sync(uuid.UUID(payload["sync_run_id"])),
            "embed_pending": lambda _payload: runner.indexer.embed_pending(),
            # Tickets (phase 10.3): read-only, low priority, retried by the queue.
            SYNC_JOB: lambda payload: sync_connection(
                database,
                environment.secret_store,
                uuid.UUID(payload["connection_id"]),
                getattr(app.state, "ticket_clients", None) or default_client,
                force=bool(payload.get("force")),
            ),
        },
    )
    app.state.jobs = runner.jobs
    # Phase 10.3: ask the trackers every few minutes. Only for a real run: tests sync by hand.
    if llm is None:
        jobs = runner.jobs
        TicketPoller(
            database,
            lambda connection_id: jobs.enqueue(
                SYNC_JOB, {"connection_id": connection_id}, priority=-1
            ),
            runner.submit_jobs,
        ).start()
    install_error_handlers(app)
    app.include_router(
        build_router(
            config, sessions, database, environment, runner, llm_service, app.state.questions
        )
    )
    app.include_router(build_analyses_router(database, app.state.analyses, runner.indexer))
    app.include_router(build_knowledge_router(database, llm_service))
    app.include_router(build_changes_router(database))
    app.include_router(build_tickets_router(database, environment, runner))
    app.include_router(build_privacy_router(config, database, runner, environment))
    app.include_router(build_ai_router(database, environment, local_chat, runner))
    app.include_router(build_control_router(config, sessions, database, llm_service))

    if config.web_dist.is_dir():
        app.mount("/", InterfaceFiles(directory=config.web_dist, html=True), name="web")
    else:

        @app.get("/", include_in_schema=False)
        def ui_not_built() -> PlainTextResponse:
            return PlainTextResponse("Interfaz sin compilar. Ejecuta: make build")

    return app
