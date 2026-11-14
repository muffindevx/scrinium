"""Each task of each project goes to the model its consent names, or stays local (ADR-010).

The remote adapter is a fake built by the router's factory: nothing leaves this machine.
"""

import json
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from scrinium.application.ai.routing import Router
from scrinium.application.analysis.extraction import answered_by
from scrinium.infrastructure.llm.base import ChatMessage, Completion, ModelRefusedError
from scrinium.infrastructure.local_http import ModelStatus
from scrinium.infrastructure.sqlite.models import AiRequest, AnalysisRun
from tests.conftest import ORIGIN, Runtime
from tests.integration.test_questions import GH_TOKEN, _ask, _reply, _setup, repo

__all__ = ["repo"]


class FakeRemote:
    """A remote adapter that answers like a provider, and remembers what it was sent."""

    def __init__(self, model: str, reply: str) -> None:
        self.model = model
        self.reply = reply
        self.received: list[list[ChatMessage]] = []
        self.fail: Exception | None = None

    @property
    def model_id(self) -> str:
        return f"Nube:{self.model}"

    @property
    def is_local(self) -> bool:
        return False

    def status(self) -> ModelStatus:
        return ModelStatus.READY

    def complete(
        self, messages: list[ChatMessage], max_tokens: int = 2048, json_schema: Any = None
    ) -> Completion:
        self.received.append(messages)
        if self.fail is not None:
            raise self.fail
        return Completion(self.reply, self.model_id, 900, 30, 5, cached_tokens=600)


def _remote(runtime: Runtime, reply: str) -> FakeRemote:
    remote = FakeRemote("modelo-remoto", reply)
    runtime.llm.router = Router(
        runtime.database, runtime.environment.secret_store,
        factory=lambda provider, consent, task, key: remote,
    )  # fmt: skip
    return remote


def _consent(runtime: Runtime, csrf: str, project: str, tasks: list[str]) -> None:
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    provider = runtime.client.post("/api/v1/model-providers", headers=headers, json={
        "name": "Nube", "kind": "openai_compatible", "base_url": "https://api.example.com/v1",
        "api_key": "sk-nube-0123456789"}).json()  # fmt: skip
    consents = f"/api/v1/projects/{project}/ai/consents"
    response = runtime.client.post(consents, headers=headers, json={
        "provider_id": provider["id"], "model": "modelo-remoto", "tasks": tasks,
        "retention_acknowledged": True})  # fmt: skip
    assert response.status_code == 201, response.text


def _requests(runtime: Runtime) -> list[AiRequest]:
    with runtime.database.session() as session:
        rows = list(session.scalars(select(AiRequest).order_by(AiRequest.created_at)))
        session.expunge_all()
        return rows


def test_a_project_without_consent_never_reaches_the_router(runtime: Runtime, repo: Path) -> None:
    remote = _remote(runtime, _reply())
    runtime.chat.reply = _reply(summary="local")
    csrf, project = _setup(runtime, repo)
    assert _ask(runtime, csrf, project, "¿Cómo se calcula el total?")["status"] == "completed"
    assert remote.received == [] and _requests(runtime) == []


def test_a_consented_task_goes_remote_redacted_and_recorded(runtime: Runtime, repo: Path) -> None:
    remote = _remote(runtime, _reply(summary="remoto"))
    csrf, project = _setup(runtime, repo)
    _consent(runtime, csrf, project, ["questions"])
    local_calls = len(runtime.chat.received)

    detail = _ask(runtime, csrf, project, f"¿Cómo se calcula el total? mi token es {GH_TOKEN}")
    assert detail["status"] == "completed"
    # Sent to the consented model only, and without the secret.
    assert len(remote.received) == 1 and len(runtime.chat.received) == local_calls
    sent = json.dumps([m.content for m in remote.received[0]])
    assert GH_TOKEN not in sent and "calculate_invoice_total" in sent
    # The run says which model answered it.
    with runtime.database.session() as session:
        run = session.get_one(AnalysisRun, uuid.UUID(detail["id"]))
        assert run.model_name == "Nube:modelo-remoto"
    # What left is recorded: where, which model, which passages and how much; never content.
    [request] = _requests(runtime)
    assert (request.task, request.host, request.model, request.outcome) == (
        "questions",
        "api.example.com",
        "modelo-remoto",
        "completed",
    )
    assert request.answered_by == "Nube:modelo-remoto"
    assert any(source.startswith("src/billing.py:") for source in request.sources)
    assert (request.prompt_tokens, request.cached_tokens, request.completion_tokens) == (
        900,
        600,
        30,
    )
    assert request.sent_chars > 0 and GH_TOKEN not in json.dumps(request.sources)


def test_a_failure_is_recorded_and_revoking_brings_the_task_home(
    runtime: Runtime, repo: Path
) -> None:
    remote = _remote(runtime, _reply())
    csrf, project = _setup(runtime, repo)
    _consent(runtime, csrf, project, ["questions"])
    remote.fail = ModelRefusedError()
    assert _ask(runtime, csrf, project, "¿Cómo se calcula el total?")["status"] == "failed"
    assert [r.outcome for r in _requests(runtime)] == ["refused"]
    # Never retried on another model: the local one was not asked.
    local_calls = len(runtime.chat.received)

    response = runtime.client.request(
        "DELETE", f"/api/v1/projects/{project}/ai/consents",
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )  # fmt: skip
    assert response.json()["ai_mode"] == "local_only"
    runtime.chat.reply = _reply(summary="local")
    assert _ask(runtime, csrf, project, "¿Cómo se calcula el total?")["status"] == "completed"
    assert len(runtime.chat.received) == local_calls + 1 and len(remote.received) == 1
    assert len(_requests(runtime)) == 1


def test_only_the_consented_tasks_leave(runtime: Runtime, repo: Path) -> None:
    """Consent for rule statements does not send questions."""
    remote = _remote(runtime, _reply())
    runtime.chat.reply = _reply(summary="local")
    csrf, project = _setup(runtime, repo)
    _consent(runtime, csrf, project, ["rule_statements"])
    assert _ask(runtime, csrf, project, "¿Cómo se calcula el total?")["status"] == "completed"
    assert remote.received == [] and _requests(runtime) == []


def test_deleting_the_project_takes_its_record_of_requests(runtime: Runtime, repo: Path) -> None:
    _remote(runtime, _reply(summary="remoto"))
    csrf, project = _setup(runtime, repo)
    _consent(runtime, csrf, project, ["questions"])
    _ask(runtime, csrf, project, "¿Cómo se calcula el total?")
    assert len(_requests(runtime)) == 1
    key = runtime.client.get("/api/v1/projects").json()["projects"][0]["project_key"]
    response = runtime.client.request(
        "DELETE", f"/api/v1/projects/{project}", json={"confirmation": key},
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )  # fmt: skip
    assert response.status_code == 200, response.text
    assert response.json()["ai_requests_deleted"] == 1
    assert _requests(runtime) == []


def test_provenance_names_the_model_that_answered() -> None:
    """N6: rules and statements carry the model that wrote them, not the run's default."""
    run = AnalysisRun(model_provider="llama", model_name="llama:qwen3-4b")
    assert answered_by(run, "Nube:modelo-remoto") == ("Nube", "Nube:modelo-remoto")
    assert answered_by(run, None) == ("llama", "llama:qwen3-4b")
