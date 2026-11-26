"""The first-run choice and isolated mode (ADR-013, phase 5.1).

No request leaves this machine: providers are registered but never called.
"""

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from scrinium.application.ai.routing import ModelNotPermittedError, Router
from scrinium.application.ai.setup import PROFILE_ENV
from scrinium.domain.enums import AiTask
from scrinium.infrastructure.sqlite.models import AiConsent, AuditEvent
from tests.conftest import ORIGIN, Runtime

KEY = "sk-prueba-0123456789abcdef"
CLAUDE = {"name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com",
          "api_key": KEY}  # fmt: skip


class Client:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.headers = {"Origin": ORIGIN, "X-CSRF-Token": runtime.login()}
        self.project_id = self.send("POST", "/api/v1/projects", {"name": "Pagos"}, 201)["id"]

    def send(self, method: str, path: str, body: Any = None, expected: int = 200) -> Any:
        response = self.runtime.client.request(method, path, json=body, headers=self.headers)
        assert response.status_code == expected, response.text
        return response.json() if response.content else None

    def consent(self, provider_id: str) -> None:
        self.send("POST", f"/api/v1/projects/{self.project_id}/ai/consents", {
            "provider_id": provider_id, "model": "claude-opus-5-5", "tasks": ["questions"],
            "retention_acknowledged": True}, 201)  # fmt: skip


@pytest.fixture
def client(runtime: Runtime) -> Client:
    return Client(runtime)


def test_nothing_is_chosen_until_a_person_chooses(client: Client) -> None:
    setup = client.send("GET", "/api/v1/ai-setup")
    assert setup["choice"] is None and setup["isolated"] is False and setup["managed"] is False
    status = client.send("GET", "/api/v1/system/security-status")
    assert status["ai_setup"] is None and status["profile"] == "standard"
    assert status["ai_mode"] == "local_only"


def test_choosing_a_provider_sends_nothing_by_itself(client: Client) -> None:
    chosen = client.send("PUT", "/api/v1/ai-setup", {"choice": "provider"})
    assert chosen["choice"] == "provider" and chosen["consents_revoked"] == 0
    # The project still sends nothing until someone consents for it (ADR-010).
    state = client.send("GET", f"/api/v1/projects/{client.project_id}/ai")
    assert state["ai_mode"] == "local_only" and state["consents"] == []


def test_isolated_mode_revokes_every_consent_and_refuses_providers(client: Client) -> None:
    provider = client.send("POST", "/api/v1/model-providers", CLAUDE, 201)
    client.consent(provider["id"])
    chosen = client.send("PUT", "/api/v1/ai-setup", {"choice": "isolated"})
    assert chosen["isolated"] is True and chosen["consents_revoked"] == 1
    state = client.send("GET", f"/api/v1/projects/{client.project_id}/ai")
    assert state["ai_mode"] == "local_only" and state["task_models"] == {}

    # Nothing can be registered, consented to, edited or tried.
    refused = {"error": "isolated_mode"}
    other = {**CLAUDE, "name": "Otro"}
    assert client.send("POST", "/api/v1/model-providers", other, 403) == refused
    body = {"provider_id": provider["id"], "model": "claude-opus-5-5", "tasks": ["questions"],
            "retention_acknowledged": True}  # fmt: skip
    path = f"/api/v1/projects/{client.project_id}/ai/consents"
    assert client.send("POST", path, body, 403) == refused
    patch = {"base_url": "https://api.example.com", "api_key": KEY}
    assert client.send("PATCH", f"/api/v1/model-providers/{provider['id']}", patch, 403) == refused
    check = {"model": "claude-opus-5-5"}
    assert (
        client.send("POST", f"/api/v1/model-providers/{provider['id']}/check", check, 403)
        == refused
    )

    # It is visible and auditable.
    status = client.send("GET", "/api/v1/system/security-status")
    assert status["profile"] == "isolated" and status["ai_mode"] == "isolated"
    assert client.send("GET", "/api/v1/privacy/data-flow")["ai_mode"] == "isolated"
    with client.runtime.database.session() as session:
        actions = list(session.scalars(select(AuditEvent.action)))
        assert "ai_setup.isolated" in actions
        assert session.scalars(select(AiConsent).where(AiConsent.revoked_at.is_(None))).all() == []


def test_the_router_never_sends_in_isolated_mode(client: Client) -> None:
    provider = client.send("POST", "/api/v1/model-providers", CLAUDE, 201)
    client.consent(provider["id"])
    runtime = client.runtime
    router = Router(runtime.database, runtime.environment.secret_store)
    project = uuid.UUID(client.project_id)
    assert router.route(project, AiTask.QUESTIONS) is not None
    client.send("PUT", "/api/v1/ai-setup", {"choice": "isolated"})
    assert router.route(project, AiTask.QUESTIONS) is None
    assert router.route(project, AiTask.QUESTIONS, "local") is None
    with pytest.raises(ModelNotPermittedError):
        router.route(project, AiTask.QUESTIONS, f"{provider['id']} claude-opus-5-5")


def test_isolated_mode_forced_by_the_environment(
    client: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(PROFILE_ENV, "isolated")
    setup = client.send("GET", "/api/v1/ai-setup")
    assert setup["isolated"] is True and setup["managed"] is True
    assert client.send("PUT", "/api/v1/ai-setup", {"choice": "provider"}, 403) == {
        "error": "isolated_managed"
    }
    assert client.send("POST", "/api/v1/model-providers", CLAUDE, 403) == {"error": "isolated_mode"}
    status = client.send("GET", "/api/v1/system/security-status")
    assert status["profile"] == "isolated" and status["profile_managed"] is True


@pytest.mark.parametrize(
    "url", ["http://192.168.1.20:11434", "https://api.example.com", "http://127.0.0.1:8080/v1"]
)
def test_a_local_server_must_be_on_this_machine(client: Client, url: str) -> None:
    body = {"choice": "local_server", "local_server": {"url": url, "model": "qwen3"}}
    assert client.send("PUT", "/api/v1/ai-setup", body, 422) == {
        "error": "local_server_not_loopback"
    }
    assert client.send("PUT", "/api/v1/ai-setup", {"choice": "local_server"}, 422) == {
        "error": "local_server_required"
    }


def test_a_local_server_becomes_the_local_model(client: Client) -> None:
    body = {"choice": "local_server",
            "local_server": {"url": "http://127.0.0.1:9", "model": "qwen3:8b"}}  # fmt: skip
    chosen = client.send("PUT", "/api/v1/ai-setup", body)
    assert chosen["local_server"] == {"url": "http://127.0.0.1:9", "model": "qwen3:8b"}
    assert client.send("GET", "/api/v1/ai-setup")["local_model"] == "local:qwen3:8b"
    # Port 9 (discard) answers nothing: the server is unavailable, and nothing was sent away.
    checked = client.send("POST", "/api/v1/ai-setup/check-local", body["local_server"])
    assert checked == {"status": "unavailable"}
    # Back to a provider: the local model is Scrinium's own again.
    client.send("PUT", "/api/v1/ai-setup", {"choice": "provider"})
    assert client.send("GET", "/api/v1/ai-setup")["local_model"] != "local:qwen3:8b"


def test_the_language_changes_what_the_model_is_asked_to_write(client: Client) -> None:
    from scrinium.application.ai.language import localize
    from scrinium.infrastructure.llm.base import ChatMessage

    assert client.send("GET", "/api/v1/workspace/language") == {"language": "es"}
    assert client.send("PUT", "/api/v1/workspace/language", {"language": "en"}) == {
        "language": "en"
    }
    assert client.send("PUT", "/api/v1/workspace/language", {"language": "fr"}, 422)
    router = Router(client.runtime.database, client.runtime.environment.secret_store)
    assert router.language() == "en"
    system = ChatMessage("system", "5. Escribe siempre en espanol, aunque el codigo no.")
    user = ChatMessage("user", "texto del repositorio: en espanol")
    localized = localize([system, user], "en")
    assert localized[0].content == "5. Escribe siempre en ingles (English), aunque el codigo no."
    # The repository's text is never rewritten.
    assert localized[1] == user
    assert localize([system], "es") == [system]
