"""Logs and errors must not contain secrets or content."""

import logging

import pytest

from scrinium.core.logging import RedactingFilter, redact, safe_log_value
from tests.conftest import Runtime


@pytest.mark.parametrize(
    "secret",
    [
        "token=abc123",
        "Authorization: Bearer abc123",
        "password = hunter2",
        "api_key=abc123",
        "ghp_" + "a" * 36,
        "github_pat_" + "a" * 40,
        "sk-" + "a" * 40,
        "AKIA" + "A" * 16,
        "xoxb-" + "1" * 20,
        "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----",
    ],
)
def test_redact_removes_known_secret_shapes(secret: str) -> None:
    value = secret.split("=")[-1].split()[-1] if "=" in secret or "Bearer" in secret else secret
    redacted = redact(f"before {secret} after")
    assert value not in redacted
    assert "[REDACTADO]" in redacted
    assert redacted.startswith("before ")


def test_filter_redacts_formatting_arguments() -> None:
    record = logging.LogRecord("t", logging.INFO, "f", 1, "value %s", ("token=abc123",), None)
    assert RedactingFilter().filter(record)
    assert "abc123" not in record.getMessage()


def test_log_values_cannot_forge_lines() -> None:
    assert "\n" not in safe_log_value("/x\n2026-01-01 INFO forged")


def test_launch_token_and_session_values_never_reach_logs(
    runtime: Runtime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    token = runtime.sessions.issue_launch_token()
    runtime.client.get("/auth/launch", params={"token": token}, follow_redirects=False)
    csrf = runtime.client.get("/api/v1/session").json()["csrf_token"]
    cookie = runtime.client.cookies["scrinium_session"]

    # httpx is this test's client and logs its own URLs; it is not part of the runtime.
    runtime_logs = "\n".join(
        record.getMessage() for record in caplog.records if record.name != "httpx"
    )
    assert "path=/auth/launch" in runtime_logs
    for secret in (token, csrf, cookie):
        assert secret not in runtime_logs


def test_rejected_host_value_is_not_logged(
    runtime: Runtime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    runtime.client.get("/health", headers={"Host": "attacker-controlled.example"})
    assert "reason=invalid_host" in caplog.text
    assert "attacker-controlled" not in caplog.text


def test_client_request_id_is_ignored(runtime: Runtime) -> None:
    response = runtime.client.get("/health", headers={"X-Request-ID": "forged"})
    assert response.headers["x-request-id"] != "forged"


def test_unhandled_error_reveals_nothing(
    runtime: Runtime, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)

    @runtime.app.get("/boom")
    def boom() -> None:
        raise ValueError("contenido-sensible-del-repositorio")

    response = runtime.client.get("/boom")
    assert response.status_code == 500
    assert response.json()["error"] == "internal_error"
    assert "contenido-sensible" not in response.text
    assert "contenido-sensible" not in caplog.text
    assert "type=ValueError" in caplog.text
