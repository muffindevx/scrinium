"""Rules as code (phase 3): export, tombstones and import from a clean start (ADR-012)."""

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scrinium.application.check.rule_files import load, render, write_all
from scrinium.application.knowledge.rulebook import import_rules, reviewed_rules
from scrinium.infrastructure.sqlite.models import Project, SourceConnection
from tests.conftest import Runtime
from tests.integration.test_analyses import Api
from tests.integration.test_rule_watch import _commission_rule, _reply, _rule, _verify, api

__all__ = ["api"]


def _reviewed(target: Api) -> list[Any]:
    with target.runtime.database.session() as session:
        return reviewed_rules(session, uuid.UUID(target.project_id), uuid.UUID(target.source_id))


def _import(target: Api, folder: Path) -> Any:
    with target.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(target.project_id))
        source = session.get_one(SourceConnection, uuid.UUID(target.source_id))
        return import_rules(session, project, source, load(folder))


def _clean_start(api: Api, make_runtime: Callable[..., Runtime], tmp_path: Path) -> Api:
    """Another machine: an empty data directory, the same repository."""
    other = Api(make_runtime(tmp_path / "otro"), api.repo)
    other.runtime.client.put(
        "/api/v1/profile", json={"display_name": "Luis"}, headers=other.headers
    )
    return other


def _reject(api: Api) -> None:
    client = api.runtime.client
    client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)
    version_id = _rule(api)["detail"]["versions"][0]["id"]
    response = client.post(
        f"/api/v1/knowledge-versions/{version_id}/verification",
        json={"status": "rejected"},
        headers=api.headers,
    )
    assert response.status_code == 200, response.text


def test_export_import_export_gives_the_same_bytes(
    api: Api, make_runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    _verify(api)
    folder = tmp_path / "reglas"
    assert write_all(folder, _reviewed(api)) == (1, 0, 0)
    exported = {p.name: p.read_bytes() for p in folder.iterdir()}

    other = _clean_start(api, make_runtime, tmp_path)
    done = _import(other, folder)
    assert len(done.created) == 1 and done.unanchored == []
    again = {rule.file_name: render(rule).encode() for rule in _reviewed(other)}
    assert again == exported
    # The same id, verified by the same person, as a rule of the new project.
    [rule] = other.runtime.client.get(f"/api/v1/projects/{other.project_id}/rules").json()["rules"]
    assert rule["entity_id"] == _rule(api)["entity_id"]
    # Importing twice changes nothing.
    assert _import(other, folder).unchanged == [rule["entity_id"]]


def test_a_rejected_rule_is_a_tombstone_and_is_not_proposed_again(
    api: Api, make_runtime: Callable[..., Runtime], tmp_path: Path
) -> None:
    _reject(api)
    folder = tmp_path / "reglas"
    write_all(folder, _reviewed(api))
    [path] = folder.iterdir()
    text = path.read_text()
    assert "status: rejected" in text and "title:" not in text and "Comision" not in text

    other = _clean_start(api, make_runtime, tmp_path)
    _import(other, folder)
    other.runtime.chat.reply = _reply([_commission_rule()])
    run = other.post(f"/api/v1/projects/{other.project_id}/extractions", {})
    assert other.read(run["id"])["status"] == "completed"
    rules = other.runtime.client.get(f"/api/v1/projects/{other.project_id}/rules").json()["rules"]
    # Only the tombstone's rule, still rejected: the model's proposal landed on it.
    assert [r["entity_id"] for r in rules] == [path.stem]
    assert rules[0]["latest_version"]["verification_status"] == "rejected"


def test_files_record_who_reviewed_and_from_what(api: Api, tmp_path: Path) -> None:
    _verify(api)
    [rule] = _reviewed(api)
    assert rule.verified_by == "Ana" and rule.verified_at and rule.verified_at.endswith("Z")
    assert rule.extractor  # The fake model's statement came from an extractor.
    assert json.dumps(render(rule)).count("total") == 0  # No code text.


def test_the_console_knows_what_is_not_exported(api: Api) -> None:
    from scrinium.application.check.rule_files import RULES_DIR

    def status() -> dict[str, Any]:
        response = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/rulebook")
        [source] = response.json()["sources"]
        return dict(source)

    assert status()["reviewed"] == 0 and status()["unexported"] == 0
    _verify(api)
    assert status()["unexported"] == 1
    write_all(api.repo / RULES_DIR, _reviewed(api))
    assert status()["unexported"] == 0 and status()["extra"] == 0
    (api.repo / RULES_DIR / f"{uuid.uuid4()}.md").write_text("---\n")
    assert status()["extra"] == 1
