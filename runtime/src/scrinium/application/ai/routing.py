"""Which model each task of each project uses (M2, ADR-010).

`LlmService.for_task(project_id, task)` returns a `TaskModel`. On every call it asks the
project's consents which model the task goes to:

- none in force: the local model, as before;
- a consented remote model: that provider's adapter, with every message redacted of secrets,
  and a local record of what was sent (never its content).

Deciding on every call means a consent revoked during an extraction takes effect at the next
batch: the rest runs locally. A failure is the task's failed batch, never a retry elsewhere.
"""

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Final

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from scrinium.application.ai.language import localize, read_language
from scrinium.application.ai.masking import Masker
from scrinium.application.ai.providers import (
    ProviderError,
    consent_for_task,
    parse_choice,
    provider_key,
)
from scrinium.application.ai.setup import is_isolated
from scrinium.application.ingestion.policy import matches_user_pattern
from scrinium.core.secret_patterns import redact_tokens
from scrinium.domain.enums import AiTask, ProviderKind
from scrinium.infrastructure.llm.anthropic_chat import AnthropicChat
from scrinium.infrastructure.llm.base import KEY_NAME, ChatMessage, Completion, LLMProvider
from scrinium.infrastructure.llm.remote_openai import RemoteOpenAIChat
from scrinium.infrastructure.local_http import ModelError, ModelStatus, Outcome
from scrinium.infrastructure.remote_http import ProviderAuthError
from scrinium.infrastructure.secret_store import SecretStore
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    AiCacheEntry,
    AiRequest,
    ModelProvider,
    Project,
    utc_now,
)

logger = logging.getLogger("scrinium.ai")

MAX_SOURCES: Final = 200
# Reasoning depth for models that take it (Claude): the rule map is many short classifications.
_EFFORT: Final = {AiTask.RULE_MAP: "low"}
_DEFAULT_EFFORT: Final = "medium"

# T5: a remote answer is reused for the very same request for this long, and a project keeps
# at most this many (the oldest go first).
CACHE_TTL: Final = timedelta(days=30)
CACHE_MAX_ENTRIES: Final = 5000

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


class ModelNotPermittedError(ModelError):
    """The model chosen for a run is no longer allowed for its task: nothing was sent."""

    outcome: ClassVar[Outcome] = Outcome.ERROR


class BudgetExceededError(ModelError):
    """The project's monthly token budget for remote models is spent: nothing was sent (T6)."""

    outcome: ClassVar[Outcome] = Outcome.BUDGET


_PASSAGE: Final = re.compile(r"^(?P<path>.+?):\d+(?:-\d+)?$")


def path_of(source: str) -> str:
    """The file a recorded passage comes from: `src/a.py:3-9` and `src/a.py (diff)` alike."""
    source = source.removesuffix(" (diff)")
    match = _PASSAGE.match(source)
    return match.group("path") if match else source


def month_start(now: datetime) -> datetime:
    return now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def tokens_spent(session: Session, project_id: uuid.UUID, now: datetime) -> int:
    """Tokens the project's remote requests used this calendar month (UTC), in and out."""
    total = session.scalar(
        select(
            func.coalesce(func.sum(AiRequest.prompt_tokens + AiRequest.completion_tokens), 0)
        ).where(AiRequest.project_id == project_id, AiRequest.created_at >= month_start(now))
    )
    return int(total or 0)


@dataclass(frozen=True)
class _Route:
    consent_id: uuid.UUID
    project_id: uuid.UUID
    workspace_id: uuid.UUID
    host: str
    model: str
    adapter: LLMProvider
    never_send: tuple[str, ...]
    budget: int | None
    mask_personal: bool = False
    mask_literals: bool = False

    def keeps_local(self, sources: tuple[str, ...]) -> bool:
        """True if a passage of this request is in a path the project never sends."""
        return any(
            matches_user_pattern(path_of(source), pattern)
            for source in sources
            for pattern in self.never_send
        )


AdapterFactory = Callable[[ModelProvider, str, AiTask, Callable[[], str]], LLMProvider]


def build_adapter(
    provider: ModelProvider, model: str, task: AiTask, api_key: Callable[[], str]
) -> LLMProvider:
    if provider.kind is ProviderKind.ANTHROPIC:
        return AnthropicChat(
            provider.name, provider.base_url, model, api_key,
            effort=_EFFORT.get(task, _DEFAULT_EFFORT),
        )  # fmt: skip
    return RemoteOpenAIChat(provider.name, provider.base_url, model, api_key)


class Router:
    """Builds and keeps one adapter per consent, so it keeps what it learned (JSON mode)."""

    def __init__(
        self, database: Database, store: SecretStore, factory: AdapterFactory = build_adapter
    ) -> None:
        self._database = database
        self._store = store
        self._factory = factory
        self._adapters: dict[tuple[Any, ...], LLMProvider] = {}

    def route(
        self, project_id: uuid.UUID, task: AiTask, choice: str | None = None
    ) -> _Route | None:
        """Where this task goes: the run's choice, or the task's default (None: local)."""
        with self._database.session() as session:
            project = session.get(Project, project_id)
            if project is None:
                return None
            if is_isolated(session):
                # Isolated mode (ADR-013): the local model, or a refusal if a run named another.
                try:
                    remote = parse_choice(choice) not in (None, ("", ""))
                except ProviderError:
                    remote = True
                if remote:
                    raise ModelNotPermittedError
                return None
            try:
                consent = consent_for_task(session, project, task, choice)
            except ProviderError:
                # The chosen model lost its permission after the run was accepted: this
                # request is refused, never sent elsewhere.
                raise ModelNotPermittedError from None
            if consent is None or consent.provider_id is None:
                return None
            provider = session.get(ModelProvider, consent.provider_id)
            if provider is None:
                return None
            # A provider edited (kind, address) gets a new adapter.
            key = (consent.id, task, provider.kind, provider.base_url, provider.secret_ref)
            if key not in self._adapters:
                session.expunge(provider)
                store = self._store

                def api_key(registered: ModelProvider = provider) -> str:
                    return provider_key(store, registered, registered.host)

                self._adapters[key] = self._factory(provider, consent.model, task, api_key)
            return _Route(
                consent.id, project_id, consent.workspace_id, consent.host, consent.model,
                self._adapters[key], tuple(project.never_send or ()), project.remote_token_budget,
                bool(project.mask_personal), bool(project.mask_literals),
            )  # fmt: skip

    def language(self) -> str:
        """The language the models answer in (phase 5.5)."""
        with self._database.session() as session:
            return read_language(session)

    def cached(self, route: _Route, key: str, now: datetime) -> AiCacheEntry | None:
        """The answer kept for this very request, if it is recent enough; counts the reuse."""
        with self._database.session() as session:
            entry = session.scalars(
                select(AiCacheEntry).where(
                    AiCacheEntry.project_id == route.project_id,
                    AiCacheEntry.key == key,
                    AiCacheEntry.created_at >= now - CACHE_TTL,
                )
            ).first()
            if entry is None:
                return None
            session.execute(
                update(AiCacheEntry)
                .where(AiCacheEntry.id == entry.id)
                .values(hits=AiCacheEntry.hits + 1)
            )
            session.expunge(entry)
            return entry

    def remember(
        self, route: _Route, task: AiTask, key: str, completion: Completion, now: datetime
    ) -> None:
        with self._database.session() as session:
            project = route.project_id
            session.execute(delete(AiCacheEntry).where(
                AiCacheEntry.project_id == project,
                (AiCacheEntry.key == key) | (AiCacheEntry.created_at < now - CACHE_TTL),
            ))  # fmt: skip
            entry = AiCacheEntry(
                workspace_id=route.workspace_id, project_id=project, key=key,
                task=task.value, answered_by=completion.model_id, text=completion.text,
            )  # fmt: skip
            session.add(entry)
            session.flush()
            # Past the cap, the oldest go.
            surplus = list(
                session.scalars(
                    select(AiCacheEntry.id)
                    .where(AiCacheEntry.project_id == project)
                    .order_by(AiCacheEntry.created_at.desc())
                    .offset(CACHE_MAX_ENTRIES)
                )
            )
            if surplus:
                session.execute(delete(AiCacheEntry).where(AiCacheEntry.id.in_(surplus)))

    def spent(self, project_id: uuid.UUID, now: datetime) -> int:
        with self._database.session() as session:
            return tokens_spent(session, project_id, now)

    def record(
        self, route: _Route, task: AiTask, messages: list[ChatMessage],
        completion: Completion | None, outcome: Outcome, masked: int = 0,
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
                    masked=masked,
                )
            )


_KEY_LINE: Final = re.compile(rf"{re.escape(KEY_NAME)} de las marcas de esta peticion: (\S+)\n")
# A noncharacter: text from sources does not carry it.
_FENCE: Final = "\uffff"


def cache_key(
    route: _Route, task: AiTask, messages: list[ChatMessage], max_tokens: int,
    json_schema: dict[str, Any] | None,
) -> str:  # fmt: skip
    """SHA-256 of everything that decides the answer: where, which model, which task, the
    output limits and exactly what would be sent, except the request's fence key (`key_line`):
    it is random on every request, and content never holds it (each task strips it), so
    replacing it leaves two requests equal only if everything else is."""
    contents = [m.content for m in messages]
    nonce = next((match.group(1) for c in contents if (match := _KEY_LINE.match(c))), None)
    if nonce:
        contents = [c.replace(nonce, _FENCE) for c in contents]
    payload = json.dumps(
        [route.host, route.model, task.value, max_tokens, json_schema,
         [[m.role, c] for m, c in zip(messages, contents, strict=True)]],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )  # fmt: skip
    return hashlib.sha256(payload.encode()).hexdigest()


class TaskModel:
    """The model of one task of one project, decided on every call (see the module)."""

    def __init__(
        self, local: LLMProvider, router: Router | None, project_id: uuid.UUID, task: AiTask,
        choice: str | None = None,
    ) -> None:  # fmt: skip
        self._local = local
        self._router = router
        self._project_id = project_id
        self._task = task
        # "local", "<provider id> <model>", or None for the task's default.
        self._choice = choice

    def _route(self) -> _Route | None:
        if self._router is None:
            return None
        return self._router.route(self._project_id, self._task, self._choice)

    def _route_or_refused(self) -> _Route | ModelNotPermittedError | None:
        try:
            return self._route()
        except ModelNotPermittedError as error:
            return error

    @property
    def model_id(self) -> str:
        route = self._route_or_refused()
        if isinstance(route, ModelNotPermittedError):
            return (self._choice or "")[:200]
        return self._local.model_id if route is None else route.adapter.model_id

    @property
    def is_local(self) -> bool:
        return self._route_or_refused() is None

    def status(self) -> ModelStatus:
        """A chosen model that lost its permission is unavailable: the run fails, unsent."""
        route = self._route_or_refused()
        if isinstance(route, ModelNotPermittedError):
            return ModelStatus.UNAVAILABLE
        return self._local.status() if route is None else route.adapter.status()

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        if self._router is not None:
            messages = localize(messages, self._router.language())
        route = self._route()
        if route is None or self._router is None:
            return self._local.complete(messages, max_tokens, json_schema)
        if route.keeps_local(_SOURCES.get()):
            # A path the project never sends: this request runs here, and nothing leaves.
            logger.info("remote_kept_local project=%s task=%s", self._project_id, self._task.value)
            return self._local.complete(messages, max_tokens, json_schema)
        # Secrets in a format Scrinium knows never leave, whatever passed ingestion. Then, if
        # the project asked for it, personal data and long literals become placeholders.
        masker = Masker(route.mask_personal, route.mask_literals)
        sent = [ChatMessage(m.role, masker.mask(redact_tokens(m.content))) for m in messages]
        now = utc_now()
        key = cache_key(route, self._task, sent, max_tokens, json_schema)
        hit = self._router.cached(route, key, now)
        if hit is not None:
            # T5: the very same request was answered already. Nothing is sent, nothing is spent
            # and nothing is recorded as having left.
            logger.info("remote_cache_hit project=%s task=%s", self._project_id, self._task.value)
            return Completion(masker.unmask(hit.text), hit.answered_by, 0, 0, 0)
        if route.budget is not None and self._router.spent(self._project_id, now) >= route.budget:
            raise BudgetExceededError
        try:
            completion = route.adapter.complete(sent, max_tokens, json_schema)
        except ProviderError:
            # No key in the keyring, or not for this host: the batch fails, and nothing was
            # sent, so there is nothing to record.
            raise ProviderAuthError from None
        except ModelError as error:
            self._router.record(route, self._task, sent, None, error.outcome, masker.masked)
            raise
        self._router.record(route, self._task, sent, completion, Outcome.COMPLETED, masker.masked)
        self._router.remember(route, self._task, key, completion, now)
        # The real values are back before the task reads, checks or stores the answer.
        return replace(completion, text=masker.unmask(completion.text))
