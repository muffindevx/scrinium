import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterator
from concurrent.futures import Executor, Future
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from scrinium.application.ai.service import LlmService
from scrinium.application.environment import LocalEnvironment
from scrinium.application.ingestion.sources import IngestionRunner
from scrinium.application.retrieval.indexing import Indexer
from scrinium.core import language
from scrinium.core.config import RuntimeConfig
from scrinium.core.session import LocalSessionManager
from scrinium.infrastructure.llm.base import ChatMessage, Completion
from scrinium.infrastructure.llm.switchable import SwitchableChat
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.secret_store import KeyringSecretStore
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.vector_index.sqlite_vec import SqliteVecIndex
from scrinium.main import create_app, open_runtime

TEST_PORT = 48123


@pytest.fixture(autouse=True)
def _spanish_console() -> Iterator[None]:
    """A command run in the test process switches its messages to English: undo it after."""
    yield
    language._english = False


ORIGIN = f"http://127.0.0.1:{TEST_PORT}"


class MemoryKeyring(KeyringBackend):
    """Fake in-memory keyring. Goes through the real KeyringSecretStore code."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()  # type: ignore[no-untyped-call]
        self.entries: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.entries.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.entries[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        if self.entries.pop((service, username), None) is None:
            raise PasswordDeleteError


def memory_secret_store() -> KeyringSecretStore:
    return KeyringSecretStore(MemoryKeyring(), "Keyring de prueba")


class InlineExecutor(Executor):
    """Runs immediately: tests see the sync finished when the request returns."""

    def submit[T](self, fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> Future[T]:
        future: Future[T] = Future()
        future.set_result(fn(*args, **kwargs))
        return future


class FakeEmbeddings:
    """Deterministic vectors from the words: texts sharing words are
    similar. Enough to test the flow, not semantic quality."""

    dimension = 64

    def __init__(self) -> None:
        self.state = ModelStatus.READY
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "fake:bag-of-words"

    def status(self) -> ModelStatus:
        return self.state

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimension
        for word in re.findall(r"\w+", unicodedata.normalize("NFKD", text.lower())):
            vector[int(hashlib.sha256(word.encode()).hexdigest(), 16) % self.dimension] += 1.0
        norm = sum(value * value for value in vector) ** 0.5 or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.state is not ModelStatus.READY:
            raise ModelError
        self.calls += 1
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        if self.state is not ModelStatus.READY:
            raise ModelError
        return self._vector(text)


class FakeChat:
    """Fake LLM: answers a fixed text and records what it receives."""

    def __init__(self, reply: str = "listo", is_local: bool = True) -> None:
        self.reply = reply
        self.state = ModelStatus.READY
        self.received: list[list[ChatMessage]] = []
        self._is_local = is_local
        # Extraction phase 2 (statements) fails: the rules stay in the map.
        self.fail_statements = False

    @property
    def model_id(self) -> str:
        return "fake:chat"

    @property
    def is_local(self) -> bool:
        return self._is_local

    def status(self) -> ModelStatus:
        return self.state

    def complete(
        self,
        messages: list[ChatMessage],
        max_tokens: int = 2048,
        json_schema: dict[str, Any] | None = None,
    ) -> Completion:
        if self.state is not ModelStatus.READY:
            raise ModelError
        self.received.append(messages)
        return Completion(self._answer(json_schema), self.model_id, 10, 5, 12)

    def _answer(self, json_schema: dict[str, Any] | None) -> str:
        """Extraction phase 2 (statements) answers by itself: one statement per reference
        the schema asks for. Everything else gets the fixed text."""
        statements = (json_schema or {}).get("properties", {}).get("s", {}).get("prefixItems")
        if not statements:
            return self.reply
        if self.fail_statements:
            return "esto no es JSON"
        answers = []
        for item in statements:
            ref = item["properties"]["i"]["const"]
            answer = {"i": ref, "s": f"Enunciado de {ref}."}
            if "t" in item["properties"]:  # a title phase 1 cut: written again, whole
                answer["t"] = f"Titulo completo de {ref}"
            answers.append(answer)
        return json.dumps({"s": answers})


@dataclass
class Runtime:
    app: FastAPI
    config: RuntimeConfig
    sessions: LocalSessionManager
    database: Database
    environment: LocalEnvironment
    runner: IngestionRunner
    llm: LlmService
    client: TestClient

    @property
    def chat(self) -> FakeChat:
        provider = self.llm.provider
        # The local model sits behind the first-run switch (ADR-013).
        if isinstance(provider, SwitchableChat):
            provider = provider.current
        assert isinstance(provider, FakeChat)
        return provider

    @property
    def embeddings(self) -> FakeEmbeddings:
        provider = self.runner.indexer.provider
        assert isinstance(provider, FakeEmbeddings)
        return provider

    def login(self) -> str:
        """Signs in and returns the CSRF token."""
        token = self.sessions.issue_launch_token()
        response = self.client.get("/auth/launch", params={"token": token}, follow_redirects=False)
        assert response.status_code == 303
        csrf: str = self.client.get("/api/v1/session").json()["csrf_token"]
        return csrf


@pytest.fixture
def make_runtime(tmp_path: Path) -> Iterator[Callable[..., Runtime]]:
    """Each call simulates a new start on the same data directory (or on `data_dir`)."""
    databases: list[Database] = []

    def factory(data_dir: Path | None = None) -> Runtime:
        config = RuntimeConfig(
            port=TEST_PORT, data_dir=data_dir or tmp_path / "data", web_dist=tmp_path / "no-ui"
        )
        sessions = LocalSessionManager()
        database, environment = open_runtime(config, memory_secret_store())
        databases.append(database)
        indexer = Indexer(database, FakeEmbeddings(), SqliteVecIndex(database))
        runner = IngestionRunner(database, config.data_dir, indexer, InlineExecutor())
        chat = FakeChat()
        app = create_app(config, sessions, database, environment, runner, chat, InlineExecutor())
        client = TestClient(app, base_url=ORIGIN)
        llm = app.state.questions.llm
        return Runtime(app, config, sessions, database, environment, runner, llm, client)

    yield factory
    for database in databases:
        database.dispose()


@pytest.fixture
def runtime(make_runtime: Callable[[], Runtime]) -> Runtime:
    return make_runtime()
