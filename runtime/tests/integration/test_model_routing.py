"""Each task of each project goes to the model its consent names, or stays local (ADR-010).

The remote adapter is a fake built by the router's factory: nothing leaves this machine.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from scrinium.application.ai.routing import Router
from scrinium.application.analysis import extraction_queue as queue
from scrinium.application.analysis.extraction import answered_by
from scrinium.infrastructure.llm.base import ChatMessage, Completion, ModelRefusedError
from scrinium.infrastructure.local_http import ModelStatus
from scrinium.infrastructure.sqlite.models import AiRequest, AnalysisRun
from tests.conftest import ORIGIN, Runtime
from tests.integration.test_analyses import Api
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
        factory=lambda provider, model, task, key: remote,
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


def test_the_record_and_the_privacy_summary_say_what_left(runtime: Runtime, repo: Path) -> None:
    _remote(runtime, _reply(summary="remoto"))
    csrf, project = _setup(runtime, repo)
    client = runtime.client
    assert client.get("/api/v1/privacy/data-flow").json()["ai_mode"] == "local_only"
    _consent(runtime, csrf, project, ["questions"])
    flow = client.get("/api/v1/privacy/data-flow").json()
    assert flow["ai_mode"] == "remote_allowed"
    assert flow["remote_ai_providers"] == ["Nube (api.example.com)"]
    assert client.get("/api/v1/system/security-status").json()["ai_mode"] == "remote_allowed"

    _ask(runtime, csrf, project, "¿Cómo se calcula el total?")
    [entry] = client.get(f"/api/v1/projects/{project}/ai/requests").json()["requests"]
    assert (entry["task"], entry["host"], entry["outcome"]) == (
        "questions", "api.example.com", "completed",
    )  # fmt: skip
    assert any(source.startswith("src/billing.py:") for source in entry["sources"])
    # Passages and counts only: never what was sent.
    assert not {"content", "messages", "text"} & set(entry)


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


def _settings(
    runtime: Runtime, csrf: str, project: str, body: dict[str, Any], code: int = 200
) -> Any:
    response = runtime.client.put(
        f"/api/v1/projects/{project}/ai/settings", json=body,
        headers={"Origin": ORIGIN, "X-CSRF-Token": csrf},
    )  # fmt: skip
    assert response.status_code == code, response.text
    return response.json()


def test_never_send_paths_keep_that_request_local(runtime: Runtime, repo: Path) -> None:
    remote = _remote(runtime, _reply(summary="remoto"))
    runtime.chat.reply = _reply(summary="local")
    csrf, project = _setup(runtime, repo)
    _consent(runtime, csrf, project, ["questions"])
    state = _settings(runtime, csrf, project, {"never_send": ["src/"]})
    assert state["never_send"] == ["src/"]
    local_calls = len(runtime.chat.received)
    # The passages come from src/: the question runs here, and nothing leaves.
    assert _ask(runtime, csrf, project, "¿Cómo se calcula el total?")["status"] == "completed"
    assert remote.received == [] and _requests(runtime) == []
    assert len(runtime.chat.received) == local_calls + 1
    # Another path does not stop it.
    _settings(runtime, csrf, project, {"never_send": ["contracts/", "*.sql"]})
    _ask(runtime, csrf, project, "¿Cómo se calcula el total?")
    assert len(remote.received) == 1


def test_a_spent_budget_sends_nothing(runtime: Runtime, repo: Path) -> None:
    remote = _remote(runtime, _reply(summary="remoto"))
    csrf, project = _setup(runtime, repo)
    _consent(runtime, csrf, project, ["questions"])
    _settings(runtime, csrf, project, {"remote_token_budget": 1000})
    _ask(runtime, csrf, project, "¿Cómo se calcula el total?")
    state = runtime.client.get(f"/api/v1/projects/{project}/ai").json()
    # 900 in and 30 out: under 1000, so this one went.
    assert state["remote_tokens_this_month"] == 930 and len(remote.received) == 1
    _ask(runtime, csrf, project, "¿Cómo se calcula el total?")
    detail = _ask(runtime, csrf, project, "¿Y los descuentos?")
    # 1860 spent after the second: the third is refused before sending anything.
    assert detail["status"] == "failed" and detail["error_summary"] == "remote_budget_spent"
    assert len(remote.received) == 2 and len(_requests(runtime)) == 2


def test_settings_are_checked(runtime: Runtime, repo: Path) -> None:
    csrf, project = _setup(runtime, repo)
    assert _settings(runtime, csrf, project, {"remote_token_budget": -1}, 422)["error"] == (
        "invalid_request"
    )
    assert _settings(runtime, csrf, project, {"never_send": [""]}, 422) == {
        "error": "invalid_patterns"
    }
    state = _settings(
        runtime, csrf, project, {"never_send": ["/contracts/"], "remote_token_budget": 5}
    )
    assert state["never_send"] == ["contracts/"] and state["remote_token_budget"] == 5


def test_check_sends_one_fixed_request(
    runtime: Runtime, repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = FakeRemote("modelo-remoto", "listo")

    class Checked(FakeRemote):
        def check(self) -> ModelStatus:
            self.complete([ChatMessage("user", "Responde solo con la palabra: listo")], 16)
            return ModelStatus.READY

    checked = Checked("modelo-remoto", "listo")
    monkeypatch.setattr("scrinium.api.ai.build_adapter", lambda *args: checked)
    csrf, _project = _setup(runtime, repo)
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    provider = runtime.client.post("/api/v1/model-providers", headers=headers, json={
        "name": "Nube", "kind": "openai_compatible", "base_url": "https://api.example.com/v1",
        "api_key": "sk-nube-0123456789"}).json()  # fmt: skip
    response = runtime.client.post(
        f"/api/v1/model-providers/{provider['id']}/check", headers=headers,
        json={"model": "modelo-remoto"},
    )  # fmt: skip
    assert response.json() == {"status": "ready"}
    assert [[m.content for m in sent] for sent in checked.received] == [
        ["Responde solo con la palabra: listo"]
    ]
    assert remote.received == []


def test_a_spent_budget_pauses_extraction_and_keeps_its_queue(runtime: Runtime, repo: Path) -> None:
    """T6: the rule map stops without sending; its tasks wait for budget, none fail."""
    remote = _remote(runtime, _reply())
    api = Api(runtime, repo)
    csrf = api.headers["X-CSRF-Token"]
    _consent(runtime, csrf, api.project_id, ["rule_map"])
    _settings(runtime, csrf, api.project_id, {"remote_token_budget": 0})
    done = api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    assert done["status"] == "partial", done
    assert done["error_summary"] == "remote_budget_spent"
    assert done["result"]["remote_budget_spent"] is True and done["result"]["batches"] == 0
    assert remote.received == [] and _requests(runtime) == []
    with runtime.database.session() as session:
        left = queue.counts(session, uuid.UUID(api.project_id))
    assert left["pending"] > 0 and left["failed"] == 0


def test_masking_hides_personal_data_and_the_answer_gets_it_back(
    runtime: Runtime, repo: Path
) -> None:
    remote = _remote(
        runtime,
        _reply(
            summary="Escribe a <EMAIL_1>.",
            claims=[{"text": "calculate_invoice_total avisa a <EMAIL_1>", "sources": [1]}],
        ),
    )
    csrf, project = _setup(runtime, repo)
    _consent(runtime, csrf, project, ["questions"])
    state = _settings(runtime, csrf, project, {"mask_personal": True})
    assert (state["mask_personal"], state["mask_literals"]) == (True, False)

    detail = _ask(runtime, csrf, project, "¿Avisa a ana@example.com el cálculo del total?")
    sent = json.dumps([m.content for m in remote.received[0]], ensure_ascii=False)
    assert "ana@example.com" not in sent and "<EMAIL_1>" in sent
    assert "calculate_invoice_total" in sent
    # What is stored has the real address back; the record counts, never says which.
    assert detail["result"]["summary"] == "Escribe a ana@example.com."
    assert "ana@example.com" in detail["result"]["claims"][0]["text"]
    [request] = _requests(runtime)
    assert request.masked == 1
    entry = runtime.client.get(f"/api/v1/projects/{project}/ai/requests").json()["requests"][0]
    assert entry["masked"] == 1 and "ana@example.com" not in json.dumps(entry)
