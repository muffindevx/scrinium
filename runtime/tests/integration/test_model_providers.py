"""Remote model providers and per-project consent (ADR-010).

No request leaves this machine here: providers are only registered and consented to.
"""

import json
import uuid
from datetime import date
from typing import Any

import pytest
from sqlalchemy import select

from scrinium.application.ai.providers import ProviderError, consent_for_task, provider_key
from scrinium.application.ai.retention import STALE_AFTER, UNVERIFIED_NOTICE, notice_for
from scrinium.application.privacy import export
from scrinium.domain.enums import AiMode, AiTask, ProviderKind
from scrinium.infrastructure.sqlite.models import AiConsent, AuditEvent, ModelProvider, Project
from tests.conftest import ORIGIN, Runtime

KEY = "sk-prueba-0123456789abcdef"


class Client:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.headers = {"Origin": ORIGIN, "X-CSRF-Token": runtime.login()}
        self.project_id = self.send("POST", "/api/v1/projects", {"name": "Pagos"}, 201)["id"]

    def send(self, method: str, path: str, body: Any = None, expected: int = 200) -> Any:
        response = self.runtime.client.request(method, path, json=body, headers=self.headers)
        assert response.status_code == expected, response.text
        assert KEY not in response.text
        return response.json() if response.content else None

    def register(self, **fields: Any) -> dict[str, Any]:
        body = {"name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com",
                "api_key": KEY, **fields}  # fmt: skip
        result: dict[str, Any] = self.send("POST", "/api/v1/model-providers", body, 201)
        return result


@pytest.fixture
def client(runtime: Runtime) -> Client:
    return Client(runtime)


def _mode(state: dict[str, Any]) -> tuple[str, list[Any]]:
    return state["ai_mode"], state["consents"]


def _provider(runtime: Runtime, provider_id: str) -> ModelProvider:
    with runtime.database.session() as session:
        provider = session.get_one(ModelProvider, uuid.UUID(provider_id))
        session.expunge(provider)
        return provider


def test_a_key_goes_to_the_keyring_and_never_comes_back(client: Client) -> None:
    created = client.register(base_url="https://api.anthropic.com/")
    assert created["has_key"] and created["key_hint"] == "cdef"
    assert (
        created["base_url"] == "https://api.anthropic.com"
        and created["host"] == "api.anthropic.com"
    )
    assert "secret_ref" not in created and "api_key" not in created
    stored = _provider(client.runtime, created["id"])
    store = client.runtime.environment.secret_store
    assert stored.secret_ref and stored.secret_ref.startswith("keyring:")
    assert store.get(stored.secret_ref) == KEY
    listing = client.send("GET", "/api/v1/model-providers")
    assert listing["keyring"] is True and [p["id"] for p in listing["providers"]] == [created["id"]]
    # The audit log says what was registered, never the key.
    with client.runtime.database.session() as session:
        events = session.scalars(
            select(AuditEvent).where(AuditEvent.action.like("model_provider%"))
        )
        assert KEY not in json.dumps([event.metadata_ for event in events])
    # The key is only handed out for the host it was registered for.
    assert provider_key(store, stored, "api.anthropic.com") == KEY
    with pytest.raises(ProviderError, match="key_not_for_this_host"):
        provider_key(store, stored, "evil.example.com")


@pytest.mark.parametrize(
    "base_url",
    [
        "http://api.anthropic.com",
        "https://user:pass@api.anthropic.com",
        "https://203.0.113.5/v1",
        "https://[::1]/v1",
        "https://localhost",
        "https://api.example.com/v1?key=x",
        "ftp://api.example.com",
    ],
)
def test_only_https_to_a_named_host(client: Client, base_url: str) -> None:
    error = client.send(
        "POST", "/api/v1/model-providers",
        {"name": "X", "kind": "openai_compatible", "base_url": base_url, "api_key": KEY}, 422,
    )  # fmt: skip
    assert error == {"error": "invalid_base_url"}


def test_a_bad_request_never_echoes_the_key(client: Client) -> None:
    """Validation errors give the field and the type only."""
    error = client.send(
        "POST", "/api/v1/model-providers",
        {"name": "X", "kind": "no-such-kind", "base_url": "https://a.example.com", "api_key": KEY},
        422,
    )  # fmt: skip
    assert error["error"] == "invalid_request"
    client.send(
        "POST", "/api/v1/model-providers",
        {"name": "X", "kind": "anthropic", "base_url": "https://a.example.com",
         "api_key": "con espacios dentro"}, 422,
    )  # fmt: skip
    client.register()
    assert client.send("POST", "/api/v1/model-providers", {
        "name": "Claude", "kind": "anthropic", "base_url": "https://api.anthropic.com",
        "api_key": KEY}, 409) == {"error": "name_taken"}  # fmt: skip


def test_retention_is_shown_only_as_checked() -> None:
    today = date(2026, 11, 14)
    general = notice_for(ProviderKind.ANTHROPIC, "api.anthropic.com", "claude-opus-5-5", today)
    covered = notice_for(ProviderKind.ANTHROPIC, "api.anthropic.com", "claude-fable-5-2", today)
    assert general["verified"] and general["key"] == "anthropic-api"
    assert covered["key"] == "anthropic-covered-models" and "30 días" in covered["notice"]
    assert all(source.startswith("https://") for source in general["sources"])
    glm = notice_for(ProviderKind.OPENAI_COMPATIBLE, "api.z.ai", "glm-5", today)
    assert glm["verified"] and "Singapur" in glm["notice"]
    # Not checked: said so, with no reassurance. A look-alike host does not match.
    for host in ("api.deepseek.com", "api.z.ai.example.com", "notz.ai"):
        unknown = notice_for(ProviderKind.OPENAI_COMPATIBLE, host, "m", today)
        assert unknown == {"verified": False, "key": None, "notice": UNVERIFIED_NOTICE,
                           "sources": [], "checked_on": None, "stale": False}  # fmt: skip
    later = notice_for(ProviderKind.ANTHROPIC, "api.anthropic.com", "m", today + STALE_AFTER * 2)
    assert later["stale"] is True


def test_a_project_sends_nothing_until_someone_consents(client: Client) -> None:
    provider = client.register()
    project = f"/api/v1/projects/{client.project_id}/ai"
    assert _mode(client.send("GET", project)) == ("local_only", [])
    notice = client.send(
        "GET", f"/api/v1/model-providers/{provider['id']}/retention?model=claude-opus-5-5"
    )
    assert notice["verified"] is True
    body = {"provider_id": provider["id"], "model": "claude-opus-5-5",
            "tasks": ["questions", "rule_statements"], "retention_acknowledged": False}  # fmt: skip
    assert client.send("POST", f"{project}/consents", body, 422) == {
        "error": "retention_not_acknowledged"
    }
    consent = client.send(
        "POST", f"{project}/consents", body | {"retention_acknowledged": True}, 201
    )
    # What is recorded is what Scrinium showed, not what the request said.
    assert consent["retention"] == notice and consent["host"] == "api.anthropic.com"
    assert consent["tasks"] == ["questions", "rule_statements"]
    state = client.send("GET", project)
    assert state["ai_mode"] == "remote_allowed" and len(state["consents"]) == 1

    # A task given to another model moves; the rest of the first consent stays.
    other = client.register(
        name="GLM", kind="openai_compatible", base_url="https://api.z.ai/api/paas/v4"
    )
    client.send("POST", f"{project}/consents", {
        "provider_id": other["id"], "model": "glm-5", "tasks": ["rule_statements"],
        "retention_acknowledged": True}, 201)  # fmt: skip
    with client.runtime.database.session() as session:
        stored = session.get_one(Project, uuid.UUID(client.project_id))
        assert consent_for_task(session, stored, AiTask.RULE_STATEMENTS).model == "glm-5"  # type: ignore[union-attr]
        assert consent_for_task(session, stored, AiTask.QUESTIONS).model == "claude-opus-5-5"  # type: ignore[union-attr]
        assert consent_for_task(session, stored, AiTask.RULE_MAP) is None

    revoked = client.send("DELETE", f"{project}/consents")
    assert revoked == {"revoked": 2, "ai_mode": "local_only"}
    assert _mode(client.send("GET", project)) == ("local_only", [])
    with client.runtime.database.session() as session:
        # Revoked consents are kept: they record what was allowed, and when. The first one, its
        # remainder after rule statements moved to GLM, and GLM's.
        consents = session.scalars(select(AiConsent).order_by(AiConsent.created_at)).all()
        assert [(c.model, c.tasks) for c in consents] == [
            ("claude-opus-5-5", ["questions", "rule_statements"]),
            ("claude-opus-5-5", ["questions"]),
            ("glm-5", ["rule_statements"]),
        ]
        assert all(c.revoked_at is not None for c in consents)


def test_removing_a_provider_deletes_its_key_and_revokes_its_consents(client: Client) -> None:
    provider = client.register()
    ref = _provider(client.runtime, provider["id"]).secret_ref
    client.send("POST", f"/api/v1/projects/{client.project_id}/ai/consents", {
        "provider_id": provider["id"], "model": "claude-opus-5-5", "tasks": ["questions"],
        "retention_acknowledged": True}, 201)  # fmt: skip
    client.send("DELETE", f"/api/v1/model-providers/{provider['id']}", None, 204)
    assert ref is not None and client.runtime.environment.secret_store.get(ref) is None
    state = client.send("GET", f"/api/v1/projects/{client.project_id}/ai")
    assert _mode(state) == ("local_only", [])
    with client.runtime.database.session() as session:
        kept = session.scalars(select(AiConsent)).one()
        # The record still says where data could go, even without the provider.
        assert kept.provider_id is None and kept.host == "api.anthropic.com"


def test_exports_and_deletes_leave_no_key_behind(client: Client) -> None:
    provider = client.register()
    ref = _provider(client.runtime, provider["id"]).secret_ref
    client.send("POST", f"/api/v1/projects/{client.project_id}/ai/consents", {
        "provider_id": provider["id"], "model": "claude-opus-5-5", "tasks": ["questions"],
        "retention_acknowledged": True}, 201)  # fmt: skip
    with client.runtime.database.session() as session:
        document = export.collect(session)
    rows = document["tables"]["model_providers"]
    assert rows and all("secret_ref" not in row for row in rows)
    # Deleting the project takes its consents; the provider stays.
    client.send("DELETE", f"/api/v1/projects/{client.project_id}", {"confirmation": "pagos"})
    with client.runtime.database.session() as session:
        assert session.scalars(select(AiConsent)).all() == []
        assert session.scalars(select(ModelProvider)).one().name == "Claude"
    # Deleting everything takes the key out of the keyring too.
    client.send("DELETE", "/api/v1/privacy/workspace-data", {"confirmation": "BORRAR TODO"})
    assert ref is not None and client.runtime.environment.secret_store.get(ref) is None


def test_a_consent_needs_a_project_mode_to_take_effect(client: Client) -> None:
    """Even with a consent row, a project marked local sends nothing."""
    provider = client.register()
    client.send("POST", f"/api/v1/projects/{client.project_id}/ai/consents", {
        "provider_id": provider["id"], "model": "claude-opus-5-5", "tasks": ["questions"],
        "retention_acknowledged": True}, 201)  # fmt: skip
    with client.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(client.project_id))
        project.ai_mode = AiMode.LOCAL_ONLY
        assert consent_for_task(session, project, AiTask.QUESTIONS) is None
