"""Restore: export, delete everything, restore and search again. And reject tampered
files without leaving anything half done."""

import copy
import json
from typing import Any

import pytest
from sqlalchemy import func, select

from scrinium.application.privacy import restore
from scrinium.application.privacy.export import encrypt
from scrinium.infrastructure.sqlite.models import AuditEvent, Project
from tests.integration.test_analyses import Api, _extraction_reply, _rule, api, repo
from tests.integration.test_ingestion import commit_all, write

__all__ = ["api", "repo"]

MARKER = "restauracioncanario"
PASSPHRASE = "frase de prueba bastante larga"
RESTORE = "/api/v1/privacy/restore"
WIPE = {"confirmation": "BORRAR TODO"}


def _export(api: Api, **body: Any) -> bytes:
    response = api.runtime.client.post(
        "/api/v1/privacy/export", json=body or {"passphrase": PASSPHRASE}, headers=api.headers
    )
    assert response.status_code == 200
    content: bytes = response.content
    return content


def _wipe(api: Api) -> None:
    response = api.runtime.client.request(
        "DELETE", "/api/v1/privacy/workspace-data", json=WIPE, headers=api.headers
    )
    assert response.status_code == 200


def _restore(api: Api, data: bytes, passphrase: str | None = PASSPHRASE) -> Any:
    headers = {**api.headers, "Content-Type": "application/octet-stream"}
    if passphrase is not None:
        headers["X-Backup-Passphrase"] = passphrase
    return api.runtime.client.post(RESTORE, content=data, headers=headers)


def _populate(api: Api) -> str:
    write(api.repo, "docs/nota.md", f"# Nota\nEl termino interno es {MARKER}.\n")
    commit_all(api.repo, "nota")
    api.sync()
    api.runtime.chat.reply = _extraction_reply(_rule())
    api.post(f"/api/v1/projects/{api.project_id}/extractions", {})
    api.rule("billing.human", "Escrita por una persona.")
    api.runtime.client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)
    api.analysis("sql", {"sql": "DELETE FROM invoices"})
    project_id: str = api.project_id
    return project_id


def test_export_wipe_restore_gives_back_the_same_workspace(api: Api) -> None:
    project_id = _populate(api)
    client = api.runtime.client
    rules_before = client.get(f"/api/v1/projects/{project_id}/rules").json()["rules"]
    analyses_before = client.get(f"/api/v1/projects/{project_id}/analyses").json()["analyses"]
    data = _export(api)
    _wipe(api)
    assert client.get("/api/v1/projects").json()["projects"] == []

    response = _restore(api, data)
    assert response.status_code == 200, response.text
    restored = response.json()["restored"]
    assert restored["projects"] == 1 and restored["source_revisions"] >= 4

    assert [p["id"] for p in client.get("/api/v1/projects").json()["projects"]] == [project_id]
    rules = client.get(f"/api/v1/projects/{project_id}/rules").json()["rules"]
    assert rules == rules_before
    analyses = client.get(f"/api/v1/projects/{project_id}/analyses").json()["analyses"]
    assert [a["id"] for a in analyses] == [a["id"] for a in analyses_before]
    assert client.get("/api/v1/profile").json() == {"display_name": "Ana"}
    # Indexes are rebuilt from the revisions: search finds the text again.
    hits = client.get(f"/api/v1/projects/{project_id}/search", params={"q": MARKER}).json()
    exact = [hit["path"] for hit in hits["results"] if "lexical" in hit["matched_by"]]
    assert exact == ["docs/nota.md"]
    with api.runtime.database.session() as session:
        actions = session.scalars(select(AuditEvent.action)).all()
        assert actions[-1] == "workspace.restore" and "workspace.wipe" in actions


def test_restore_only_into_an_empty_workspace(api: Api) -> None:
    _populate(api)
    data = _export(api)
    response = _restore(api, data)
    assert response.status_code == 409 and response.json()["error"] == "workspace_not_empty"


def test_wrong_or_missing_passphrase(api: Api) -> None:
    _populate(api)
    data = _export(api)
    _wipe(api)
    wrong = _restore(api, data, "otra frase igual de larga")
    assert wrong.status_code == 422 and wrong.json()["error"] == "wrong_passphrase_or_modified"
    missing = _restore(api, data, None)
    assert missing.json()["error"] == "passphrase_required"


def _plain(api: Api) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(_export(api, unencrypted=True))
    return document


@pytest.mark.parametrize(
    "tamper",
    [
        # A project that does not exist: the foreign key rejects it.
        lambda d: d["tables"]["source_connections"][0].update(
            project_id="00000000-0000-0000-0000-000000000000"
        ),
        # A table or column Scrinium does not know.
        lambda d: d["tables"].update(users=[{"id": "x"}]),
        lambda d: d["tables"]["projects"][0].update(is_admin=True),
        # A value outside the closed vocabulary.
        lambda d: d["tables"]["projects"][0].update(status="deleted"),
        # A revision with a hash that is not SHA-256 (CHECK).
        lambda d: d["tables"]["source_revisions"][0].update(content_hash="abc"),
        lambda d: d.update(format_version=99),
        lambda d: d.update(format="otra-cosa"),
        lambda d: d["tables"].update(projects="no es una lista"),
    ],
)
def test_tampered_backups_are_refused_and_leave_nothing(api: Api, tamper: Any) -> None:
    _populate(api)
    document = _plain(api)
    _wipe(api)
    original = copy.deepcopy(document)
    tamper(document)
    response = _restore(api, encrypt(document, PASSPHRASE))
    assert response.status_code == 422, response.text
    with api.runtime.database.session() as session:
        assert session.scalar(select(func.count()).select_from(Project)) == 0
    # The failure left nothing half done: the untouched copy restores afterwards.
    good = _restore(api, encrypt(original, PASSPHRASE))
    assert good.status_code == 200, good.text


def test_decompression_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(restore, "MAX_PLAIN_BYTES", 1000)
    document = {"format": "scrinium-export", "format_version": 1, "tables": {"x": ["a" * 5000]}}
    with pytest.raises(restore.RestoreError, match="backup_too_large"):
        restore.read_backup(encrypt(document, PASSPHRASE), PASSPHRASE)


def test_restore_accepts_bodies_larger_than_the_usual_limit(api: Api) -> None:
    response = _restore(api, b"{" + b" " * (5 * 1024 * 1024) + b"}", None)
    assert response.status_code == 422 and response.json()["error"] == "invalid_backup"
