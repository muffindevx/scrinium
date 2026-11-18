"""Workspace and projects through the API, with a real session and CSRF."""

import uuid
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import select

from scrinium.application.workspaces import derive_project_key
from scrinium.infrastructure.sqlite.models import AuditEvent, SourceConnection
from tests.conftest import ORIGIN, Runtime


def _create(runtime: Runtime, csrf: str, body: dict[str, Any]) -> Any:
    return runtime.client.post(
        "/api/v1/projects", json=body, headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )


def test_workspace_is_the_personal_local_one(runtime: Runtime) -> None:
    runtime.login()
    body = runtime.client.get("/api/v1/workspace").json()
    assert body["workspace_type"] == "personal_local"
    assert body["name"] == "Workspace personal"


def test_create_and_list_projects(runtime: Runtime) -> None:
    csrf = runtime.login()
    assert runtime.client.get("/api/v1/projects").json() == {"projects": []}

    response = _create(
        runtime, csrf, {"name": "  Pagos  ", "project_key": "pagos", "description": "API"}
    )
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "Pagos"
    assert created["project_key"] == "pagos"
    assert created["status"] == "active"

    listed = runtime.client.get("/api/v1/projects").json()["projects"]
    assert [p["id"] for p in listed] == [created["id"]]


def test_projects_survive_a_restart(make_runtime: Callable[[], Runtime]) -> None:
    first = make_runtime()
    _create(first, first.login(), {"name": "Pagos"})
    second = make_runtime()
    second.login()
    assert [p["name"] for p in second.client.get("/api/v1/projects").json()["projects"]] == [
        "Pagos"
    ]


def test_key_is_derived_from_the_name_when_missing(runtime: Runtime) -> None:
    response = _create(runtime, runtime.login(), {"name": "Pagos Móvil 2"})
    assert response.json()["project_key"] == "pagos-movil-2"


@pytest.mark.parametrize(
    ("name", "key"),
    [("Ñandú", "nandu"), ("  --  ", "proyecto"), ("日本", "proyecto"), ("a" * 80, "a" * 64)],
)
def test_derive_project_key(name: str, key: str) -> None:
    assert derive_project_key(name) == key


def test_duplicate_key_is_a_conflict(runtime: Runtime) -> None:
    csrf = runtime.login()
    assert _create(runtime, csrf, {"name": "Pagos"}).status_code == 201
    response = _create(runtime, csrf, {"name": "Otro", "project_key": "pagos"})
    assert response.status_code == 409
    assert response.json() == {"error": "project_key_taken"}
    assert len(runtime.client.get("/api/v1/projects").json()["projects"]) == 1


def test_creation_is_audited_with_the_request_id(runtime: Runtime) -> None:
    response = _create(runtime, runtime.login(), {"name": "Pagos", "description": "secreto"})
    with runtime.database.session() as session:
        event = session.scalars(
            select(AuditEvent).where(AuditEvent.action == "project.create")
        ).one()
        assert event.resource_id == response.json()["id"]
        assert event.request_id == response.headers["x-request-id"]
        assert event.result == "success"
        # The description is the person's content: it does not go to the audit log.
        assert event.metadata_ == {"project_key": "pagos"}


@pytest.mark.parametrize(
    "body",
    [
        {"name": ""},
        {"name": "x" * 201},
        {"name": "Pagos", "project_key": "Mayúsculas"},
        {"name": "Pagos", "project_key": "-pagos"},
        {"name": "Pagos", "description": "x" * 2001},
        {"name": "Pagos", "status": "archived"},
        {},
    ],
)
def test_invalid_bodies_are_rejected(runtime: Runtime, body: dict[str, Any]) -> None:
    response = _create(runtime, runtime.login(), body)
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_validation_errors_do_not_echo_the_input(runtime: Runtime) -> None:
    marker = "contenido-privado-" + "x" * 300
    response = _create(runtime, runtime.login(), {"name": marker, "extra": marker})
    assert response.status_code == 422
    assert "contenido-privado" not in response.text
    for field in response.json()["fields"]:
        assert set(field) == {"loc", "type"}


def test_malformed_json_does_not_echo_the_input(runtime: Runtime) -> None:
    response = runtime.client.post(
        "/api/v1/projects",
        content=b'{"name": "contenido-privado',
        headers={
            "Origin": ORIGIN,
            "X-CSRF-Token": runtime.login(),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 422
    assert "contenido-privado" not in response.text


def test_form_posts_are_not_accepted_as_json(runtime: Runtime) -> None:
    # An HTML form on another site cannot send JSON: if it were accepted, CSRF would be easier.
    response = runtime.client.post(
        "/api/v1/projects",
        data={"name": "Pagos"},
        headers={"Origin": ORIGIN, "X-CSRF-Token": runtime.login()},
    )
    assert response.status_code == 422


def test_ready_reports_the_database(runtime: Runtime) -> None:
    response = runtime.client.get("/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready" and body["database"] == "ok" and body["vectors"] == "ok"
    assert body["purges_pending"] == 0 and {"embeddings", "chat"} <= set(body)
    assert response.headers["cache-control"] == "no-store"


def test_ready_fails_when_the_database_is_unavailable(runtime: Runtime) -> None:
    runtime.database.is_ready = lambda: False  # type: ignore[method-assign]
    response = runtime.client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready" and body["database"] == "unavailable"


def test_security_status_counts_real_connections(runtime: Runtime) -> None:
    runtime.login()
    assert runtime.client.get("/api/v1/system/security-status").json()["connected_sources"] == 0
    workspace_id = runtime.client.get("/api/v1/workspace").json()["id"]
    with runtime.database.session() as session:
        session.add(
            SourceConnection(
                workspace_id=uuid.UUID(workspace_id),
                provider="github",
                display_name="gh",
            )
        )
    assert runtime.client.get("/api/v1/system/security-status").json()["connected_sources"] == 1


def test_security_status_reports_storage(runtime: Runtime) -> None:
    runtime.login()
    body = runtime.client.get("/api/v1/system/security-status").json()
    assert body["storage"]["data_dir_permissions"] == "private"
    assert body["storage"]["secret_store"] == "available"
    assert body["storage"]["secret_store_backend"] == "Keyring de prueba"
    assert body["storage"]["disk_encryption"] in {"encrypted", "not_detected", "unknown"}
    assert not any("todavia no se comprueba" in limit for limit in body["limits"])


def test_llm_status_and_check(runtime: Runtime) -> None:
    csrf = runtime.login()
    providers = runtime.client.get("/api/v1/system/security-status").json()["providers"]
    assert (providers["llm"], providers["llm_model"]) == ("ready", "fake:chat")
    assert runtime.client.get("/api/v1/privacy/data-flow").json()["remote_ai_providers"] == []

    response = runtime.client.post(
        "/api/v1/llm/check", headers={"Origin": ORIGIN, "X-CSRF-Token": csrf}
    )
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "model": "fake:chat", "duration_ms": 12}


def test_llm_check_needs_csrf(runtime: Runtime) -> None:
    runtime.login()
    response = runtime.client.post("/api/v1/llm/check", headers={"Origin": ORIGIN})
    assert response.status_code == 403
