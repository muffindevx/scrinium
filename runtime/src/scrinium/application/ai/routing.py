"""Which model each task of each project uses (M2, ADR-010).

`LlmService.for_task(project_id, task)` returns a `TaskModel`. On every call it asks the
project's consents which model the task goes to:

- none in force: the local model, as before;
- a consented remote model: that provider's adapter, with every message redacted of secrets,
  and a local record of what was sent (never its content).

Deciding on every call means a consent revoked during an extraction takes effect at the next
batch: the rest runs locally. A failure is the task's failed batch, never a retry elsewhere.
"""

import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final

from scrinium.application.ai.providers import ProviderError, consent_for_task, provider_key
from scrinium.core.secret_patterns import redact_tokens
from scrinium.domain.enums import AiTask, ProviderKind
from scrinium.infrastructure.llm.anthropic_chat import AnthropicChat
from scrinium.infrastructure.llm.base import ChatMessage, Completion, LLMProvider
from scrinium.infrastructure.llm.remote_openai import RemoteOpenAIChat
from scrinium.infrastructure.local_http import ModelError, ModelStatus, Outcome
from scrinium.infrastructure.remote_http import ProviderAuthError
from scrinium.infrastructure.secret_store import SecretStore
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import AiConsent, AiRequest, ModelProvider, Project

MAX_SOURCES: Final = 200
# Reasoning depth for models that take it (Claude): the rule map is many short classifications.
_EFFORT: Final = {AiTask.RULE_MAP: "low"}
_DEFAULT_EFFORT: Final = "medium"

# What the current request sends, set by the task around its call (`sending`).
_SOURCES: ContextVar[tuple[str, ...]] = ContextVar("scrinium_sent_sources", default=())


@contextmanager
def sending(sources: list[str]) -> Iterator[None]:
    """The files and lines the next request carries, for the local record of what left."""
    token = _SOURCES.set(tuple(source[:300] for source in sources[:MAX_SOURCES]))
    try:
        yield
    finally:
        _SOURCES.reset(token)


def passage(path: str, start: int, end: int) -> str:
    return f"{path}:{start}-{end}" if end != start else f"{path}:{start}"


@dataclass(frozen=True)
class _Route:
    consent_id: uuid.UUID
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    host: str
    model: str
    adapter: LLMProvider


AdapterFactory = Callable[[ModelProvider, AiConsent, AiTask, Callable[[], str]], LLMProvider]


def build_adapter(
    provider: ModelProvider, consent: AiConsent, task: AiTask, api_key: Callable[[], str]
) -> LLMProvider:
    if provider.kind is ProviderKind.ANTHROPIC:
        return AnthropicChat(
            provider.name, provider.base_url, consent.model, api_key,
            effort=_EFFORT.get(task, _DEFAULT_EFFORT),
        )  # fmt: skip
    return RemoteOpenAIChat(provider.name, provider.base_url, consent.model, api_key)


class Router:
    """Builds and keeps one adapter per consent, so it keeps what it learned (JSON mode)."""

    def __init__(
        self, database: Database, store: SecretStore, factory: AdapterFactory = build_adapter
    ) -> None:
        self._database = database
        self._store = store
        self._factory = factory
        self._adapters: dict[tuple[uuid.UUID, AiTask], LLMProvider] = {}

    def route(self, project_id: uuid.UUID, task: AiTask) -> _Route | None:
        with self._database.session() as session:
            project = session.get(Project, project_id)
            consent = None if project is None else consent_for_task(session, project, task)
            if consent is None or consent.provider_id is None:
                return None
            provider = session.get(ModelProvider, consent.provider_id)
            if provider is None:
                return None
            key = (consent.id, task)
            if key not in self._adapters:
                session.expunge(provider)
                store = self._store

                def api_key(registered: ModelProvider = provider) -> str:
                    return provider_key(store, registered, registered.host)

                self._adapters[key] = self._factory(provider, consent, task, api_key)
            return _Route(
                consent.id, project_id, consent.workspace_id, consent.host, consent.model,
                self._adapters[key],
            )  # fmt: skip

    def record(
        self, route: _Route, task: AiTask, messages: list[ChatMessage],
        completion: Completion | None, outcome: Outcome,
    ) -> None:  # fmt: skip
        with self._database.session() as session:
            session.add(
                AiRequest(
                    workspace_id=route.workspace_id,
                    project_id=route.project_id,
                    consent_id=route.consent_id,
                    task=task.value,
                    host=route.host,
                    model=route.model,
                    answered_by=completion.model_id if completion is not None else "",
                    outcome=outcome.value,
                    sources=list(_SOURCES.get()),
                    sent_chars=sum(len(m.content) for m in messages),
                    prompt_tokens=completion.prompt_tokens if completion else 0,
                    cached_tokens=completion.cached_tokens if completion else 0,
                    completion_tokens=completion.completion_tokens if completion else 0,
                )
            )


class TaskModel:
    """The model of one task of one project, decided on every call (see the module)."""

    def __init__(
        self, local: LLMProvider, router: Router | None, project_id: uuid.UUID, task: AiTask
    ) -> None:
        self._local = local
        self._router = router
        self._project_id = project_id
        self._task = task

    def _route(self) -> _Route | None:
        return None if self._router is None else self._router.route(self._project_id, self._task)

    @property
    def model_id(self) -> str:
        route = self._route()
        return self._local.model_id if route is None else route.adapter.model_id

    @property
    def is_local(self) -> bool:
        return self._route() is None

    def status(self) -> ModelStatus:
        route = self._route()
        return self._local.status() if route is None else route.adapter.status()

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        route = self._route()
        if route is None or self._router is None:
            return self._local.complete(messages, max_tokens, json_schema)
        # Secrets in a format Scrinium knows never leave, whatever passed ingestion.
        sent = [ChatMessage(m.role, redact_tokens(m.content)) for m in messages]
        try:
            completion = route.adapter.complete(sent, max_tokens, json_schema)
        except ProviderError:
            # No key in the keyring, or not for this host: the batch fails, and nothing was
            # sent, so there is nothing to record.
            raise ProviderAuthError from None
        except ModelError as error:
            self._router.record(route, self._task, sent, None, error.outcome)
            raise
        self._router.record(route, self._task, sent, completion, Outcome.COMPLETED)
        return completion
