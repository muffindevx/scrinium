"""Privacy: really delete (from disk too), propagate deletion to knowledge and to
analyses, delete a project and the workspace, and export encrypted."""

import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select

from scrinium.application.privacy.export import ExportError, decrypt
from scrinium.domain.enums import ConnectionStatus, SyncStatus, SyncTrigger
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    AuditEvent,
    KnowledgeEntity,
    LocalProfile,
    SourceConnection,
    SyncRun,
    Workspace,
)
from tests.conftest import Runtime
from tests.integration.test_analyses import Api, _extraction_reply, _rule, api, repo
from tests.integration.test_ingestion import commit_all, write

__all__ = ["api", "repo"]

MARKER = "zorrocanario7731"
PASSPHRASE = "una frase larga de prueba"


def _files_containing(root: Path, needle: str) -> list[str]:
    data = needle.encode()
    return sorted(
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and data in path.read_bytes()
    )


def _with_marker(api: Api) -> None:
    write(api.repo, "docs/secreto.md", f"# Nota\nEl codigo interno es {MARKER}.\n")
    commit_all(api.repo, "nota")
    api.sync()


def test_deleting_a_source_removes_its_text_from_disk(api: Api) -> None:
    _with_marker(api)
    data_dir = api.runtime.config.data_dir
    # Before: the text is in SQLite (revision, chunk, FTS5) and so are the vectors (phase 9.1).
    assert _files_containing(data_dir, MARKER)
    assert _vectors_of_source(api) > 0

    path = f"/api/v1/sources/{api.source_id}"
    assert api.runtime.client.delete(path, headers=api.headers).status_code == 204
    # After: not in the database, nor the WAL; no vector of the source is left.
    assert _files_containing(data_dir, MARKER) == []
    assert _vectors_of_source(api) == 0
    # The only file naming the source: the database, for the audit log (that it was deleted).
    assert _files_containing(data_dir, api.source_id) == ["scrinium.sqlite3"]


def _vectors_of_source(api: Api) -> int:
    with api.runtime.database.engine.connect() as connection:
        tables = [
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'vecf_%'"
            )
        ]
        return sum(
            int(
                connection.exec_driver_sql(
                    f"SELECT count(*) FROM {table} WHERE source_id = ?",  # noqa: S608
                    (api.source_id,),
                ).scalar()
                or 0
            )
            for table in tables
        )


def test_source_deletion_drops_extracted_rules_and_marks_the_rest(api: Api) -> None:
    api.runtime.chat.reply = _extraction_reply(_rule("Regla extraida"))
    api.post(f"/api/v1/projects/{api.project_id}/extractions", {})
    human = api.rule("billing.human", "Escrita por una persona.")
    api.runtime.chat.reply = json.dumps(
        {"answer_status": "answered", "summary": "Suma.", "limitations": [],
         "claims": [{"text": "calculate_invoice_total suma", "sources": [1]}]}
    )  # fmt: skip
    question = api.post(
        f"/api/v1/projects/{api.project_id}/questions", {"question": "calculate_invoice_total"}
    )
    other = api.analysis("sql", {"sql": "SELECT 1"})
    client = api.runtime.client

    impact = client.get(f"/api/v1/sources/{api.source_id}/deletion-impact").json()
    assert impact["entities_to_delete"] == 1
    assert impact["entities_to_review"] == 1
    # The human rule cites only this source: it could go too, if asked.
    assert impact["entities_reviewed_here"] == 1
    assert impact["citing_questions"] == 1
    assert impact["files"] == 3 and impact["revisions"] == 3

    response = client.delete(
        f"/api/v1/sources/{api.source_id}", params={"delete_citing": "true"}, headers=api.headers
    )
    assert response.status_code == 204, response.text
    with api.runtime.database.session() as session:
        entities = {e.canonical_key: e for e in session.scalars(select(KnowledgeEntity))}
        # The LLM rule paraphrased the source: it goes with it. The human one stays, for review.
        assert set(entities) == {"billing.human"}
        assert entities["billing.human"].id == human
        assert entities["billing.human"].review_reason == "source_deleted"
        runs = {run.id for run in session.scalars(select(AnalysisRun))}
        assert uuid.UUID(question["id"]) not in runs
        # An analysis that did not cite it is untouched.
        assert uuid.UUID(other["id"]) in runs
        audit = session.scalars(
            select(AuditEvent.metadata_).where(AuditEvent.action == "source.delete")
        ).one()
        assert audit["entities_deleted"] == 1 and audit["analyses_deleted"] == 1

    # Reviewing it clears the flag.
    client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)
    rule = client.get(f"/api/v1/projects/{api.project_id}/rules/{human}").json()
    version = rule["versions"][0]["id"]
    client.post(
        f"/api/v1/knowledge-versions/{version}/verification",
        json={"status": "human_verified"},
        headers=api.headers,
    )
    with api.runtime.database.session() as session:
        assert session.get_one(KnowledgeEntity, human).review_reason is None


def test_source_deletion_can_also_take_the_reviewed_rules_it_alone_backs(
    api: Api,
) -> None:
    api.runtime.chat.reply = _extraction_reply(_rule("Regla extraida"))
    api.post(f"/api/v1/projects/{api.project_id}/extractions", {})
    api.rule("billing.human", "Escrita por una persona.")
    response = api.runtime.client.delete(
        f"/api/v1/sources/{api.source_id}", params={"delete_reviewed": "true"},
        headers=api.headers,
    )  # fmt: skip
    assert response.status_code == 204, response.text
    with api.runtime.database.session() as session:
        assert session.scalars(select(KnowledgeEntity)).all() == []
        audit = session.scalars(
            select(AuditEvent.metadata_).where(AuditEvent.action == "source.delete")
        ).one()
        assert audit["entities_deleted"] == 2


def test_deleting_a_project_needs_its_key_and_leaves_others_alone(api: Api) -> None:
    _with_marker(api)
    api.rule("billing.total", "El total suma los importes.")
    api.analysis("sql", {"sql": "DELETE FROM invoices"})
    client = api.runtime.client
    other = client.post("/api/v1/projects", json={"name": "Otro"}, headers=api.headers).json()
    path = f"/api/v1/projects/{api.project_id}"

    wrong = client.request("DELETE", path, json={"confirmation": "app2"}, headers=api.headers)
    assert wrong.status_code == 422 and wrong.json()["error"] == "confirmation_mismatch"
    done = client.request("DELETE", path, json={"confirmation": "app"}, headers=api.headers)
    assert done.status_code == 200, done.text
    summary = done.json()
    assert summary["sources_deleted"] == 1 and summary["entities_deleted"] == 1
    assert summary["analyses_deleted"] == 1

    projects = client.get("/api/v1/projects").json()["projects"]
    assert [p["id"] for p in projects] == [other["id"]]
    assert _files_containing(api.runtime.config.data_dir, MARKER) == []


def test_nothing_is_deleted_while_work_is_running(api: Api) -> None:
    with api.runtime.database.session() as session:
        connection = session.get_one(SourceConnection, uuid.UUID(api.source_id))
        session.add(
            SyncRun(
                workspace_id=connection.workspace_id, source_connection_id=connection.id,
                trigger=SyncTrigger.MANUAL, status=SyncStatus.RUNNING,
            )
        )  # fmt: skip
    client = api.runtime.client
    project = client.request(
        "DELETE", f"/api/v1/projects/{api.project_id}", json={"confirmation": "app"},
        headers=api.headers,
    )  # fmt: skip
    assert project.status_code == 409
    wipe = client.request(
        "DELETE", "/api/v1/privacy/workspace-data", json={"confirmation": "BORRAR TODO"},
        headers=api.headers,
    )  # fmt: skip
    assert wipe.status_code == 409
    assert len(client.get("/api/v1/projects").json()["projects"]) == 1


def test_wiping_the_workspace_leaves_one_event_and_nothing_else(api: Api, runtime: Runtime) -> None:
    _with_marker(api)
    api.rule("billing.total", "El total suma los importes.")
    client = api.runtime.client
    client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)
    store = runtime.environment.secret_store
    ref = store.put("token-de-prueba")
    with api.runtime.database.session() as session:
        workspace = session.scalars(select(Workspace)).one()
        session.add(
            SourceConnection(
                workspace_id=workspace.id, provider="github", display_name="gh",
                status=ConnectionStatus.ACTIVE, secret_ref=ref,
            )
        )  # fmt: skip

    path = "/api/v1/privacy/workspace-data"
    refused = client.request("DELETE", path, json={"confirmation": "borrar"}, headers=api.headers)
    assert refused.status_code == 422
    done = client.request("DELETE", path, json={"confirmation": "BORRAR TODO"}, headers=api.headers)
    assert done.status_code == 200, done.text
    assert done.json()["secrets_deleted"] == 1
    assert store.get(ref) is None

    stored = client.get("/api/v1/privacy/stored-data").json()
    assert {k: v for k, v in stored["counts"].items() if v} == {"workspaces": 1, "audit_events": 1}
    with api.runtime.database.session() as session:
        assert session.get_one(LocalProfile, 1).display_name is None
        events = session.scalars(select(AuditEvent)).all()
        assert [e.action for e in events] == ["workspace.wipe"]
        assert "Ana" not in json.dumps(events[0].metadata_)
    assert _files_containing(api.runtime.config.data_dir, MARKER) == []
    # The application keeps working: a new project can be created.
    created = client.post("/api/v1/projects", json={"name": "Nuevo"}, headers=api.headers)
    assert created.status_code == 201


def test_export_is_encrypted_and_opens_only_with_the_passphrase(api: Api) -> None:
    _with_marker(api)
    client = api.runtime.client
    path = "/api/v1/privacy/export"
    assert client.post(path, json={}, headers=api.headers).status_code == 422
    both = {"passphrase": PASSPHRASE, "unencrypted": True}
    assert client.post(path, json=both, headers=api.headers).status_code == 422
    short = client.post(path, json={"passphrase": "corta"}, headers=api.headers)
    assert short.status_code == 422 and short.json()["error"] == "passphrase_too_short"

    response = client.post(path, json={"passphrase": PASSPHRASE}, headers=api.headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-disposition"].endswith('.scrinium"')
    content = response.content
    assert MARKER.encode() not in content and PASSPHRASE.encode() not in content

    document = decrypt(content, PASSPHRASE)
    revisions = document["tables"]["source_revisions"]
    assert any(MARKER in (row["text_content"] or "") for row in revisions)
    assert "content_chunks" not in document["tables"]
    assert all("secret_ref" not in row for row in document["tables"]["source_connections"])
    with pytest.raises(ExportError, match="wrong_passphrase_or_modified"):
        decrypt(content, PASSPHRASE + "x")
    envelope = json.loads(content)
    envelope["header"]["kdf"]["n"] = 2**14
    with pytest.raises(ExportError, match="wrong_passphrase_or_modified"):
        decrypt(json.dumps(envelope).encode(), PASSPHRASE)

    plain = client.post(path, json={"unencrypted": True}, headers=api.headers)
    assert MARKER in plain.text and plain.headers["content-disposition"].endswith('.json"')
    with api.runtime.database.session() as session:
        exports = session.scalars(
            select(AuditEvent.metadata_).where(AuditEvent.action == "workspace.export")
        ).all()
        assert [e["encrypted"] for e in exports] == [True, False]
        assert (session.scalar(select(func.count()).select_from(AuditEvent)) or 0) > 2
