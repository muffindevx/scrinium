"""Cambios (phase 7.1): what a change does to the rules, between any two refs."""

import uuid
from typing import Any

from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import create_business_rule
from scrinium.domain.enums import RuleCategory
from scrinium.infrastructure.sqlite.models import KnowledgeEntityVersion, Project
from tests.integration.test_analyses import Api
from tests.integration.test_ingestion import commit_all, git, write
from tests.integration.test_rule_watch import COMMISSION, api

__all__ = ["api"]

NEW_GUARD = """\

def descuento(total):
    if total > 500:
        raise ValueError('sin descuento')
    return total
"""


def _changes(api: Api, **params: str) -> Any:
    response = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/changes", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_a_branch_that_changes_a_threshold_and_adds_a_decision(api: Api) -> None:
    git(api.repo, "checkout", "-q", "-b", "campana")
    write(api.repo, "src/pagos.py", COMMISSION.replace("total > 100", "total > 200") + NEW_GUARD)
    commit_all(api.repo, "campana")
    git(api.repo, "checkout", "-q", "main")

    found = _changes(api, base="main", head="campana")
    assert found["changed_files"] == ["src/pagos.py"]
    [rule] = found["rules"]
    assert rule["title"] == "Comision por venta" and rule["level"] == "changed"
    assert "100" in rule["before"]["values"] and "200" in rule["after"]["values"]
    assert {d["symbol"] for d in found["new_decisions"]} == {"descuento"}

    # The same refs the other way round: the rule changes back, the decision is gone.
    back = _changes(api, base="campana", head="main")
    assert back["new_decisions"] == []


def test_local_changes_and_the_refs_to_choose_from(api: Api) -> None:
    refs = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/changes/refs").json()
    assert "main" in refs["branches"] and refs["commits"][0]["subject"] == "inicio"
    assert _changes(api, base="main")["rules"] == []  # nothing changed yet
    write(api.repo, "src/pagos.py", COMMISSION.replace("total > 100", "total > 150"))
    [rule] = _changes(api, base="main")["rules"]
    assert rule["level"] == "changed"


def test_a_ref_is_never_an_option(api: Api) -> None:
    path = f"/api/v1/projects/{api.project_id}/changes"
    for bad in ("--output=/tmp/x", "-n", "main branch"):
        response = api.runtime.client.get(path, params={"base": bad})
        assert response.status_code == 422 and response.json() == {"error": "invalid_ref"}
    response = api.runtime.client.get(path, params={"base": "no-existe"})
    assert response.json() == {"error": "unknown_ref"}


def test_since_when_a_rule_has_its_values(api: Api) -> None:
    """Phase 7.2: 300 commits, the threshold changes twice; found by bisection in under 2 s."""
    for number in range(300):
        threshold = 100 if number < 120 else 150 if number < 250 else 200
        text = COMMISSION.replace("total > 100", f"total > {threshold}")
        write(api.repo, "src/pagos.py", text + f"# revision {number}\n")
        git(api.repo, "add", "-A")
        git(api.repo, "commit", "-q", "-m", f"cambio {number}")
    api.sync()
    rules = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/rules").json()["rules"]
    entity_id = next(r["entity_id"] for r in rules if r["name"] == "Comision por venta")
    response = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/rules/{entity_id}/history"
    )
    assert response.status_code == 200, response.text
    [anchor] = response.json()["anchors"]
    periods = anchor["periods"]
    assert [p["values"][:1] for p in periods] == [["200"], ["150"], ["100"]]
    assert periods[0]["since"]["subject"] == "cambio 250" and periods[0]["until"] is None
    assert periods[1]["since"]["subject"] == "cambio 120"
    assert periods[1]["until"]["subject"] == "cambio 250"
    assert periods[2]["since"]["subject"] == "inicio"
    assert anchor["commits"] == 301 and anchor["parsed"] < 60
    assert anchor["seconds"] < 2


def test_release_notes_in_markdown(api: Api) -> None:
    write(api.repo, "src/pagos.py", COMMISSION.replace("total > 100", "total > 300"))
    response = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/changes",
        params={"base": "main", "format": "markdown"},
    )
    assert response.status_code == 200 and "markdown" in response.headers["content-type"]
    assert "## Cambian" in response.text and "**Comision por venta**" in response.text
    assert "`100`" in response.text and "`300`" in response.text


def test_a_trace_leads_to_the_function_its_rules_and_its_history(api: Api) -> None:
    write(api.repo, "src/pagos.py", COMMISSION.replace("0.02", "0.03"))
    commit_all(api.repo, "Arregla la comision de las ventas grandes")
    api.sync()
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/opt/venv/lib/python3.12/site-packages/flask/app.py", line 880, in run\n'
        f'  File "/srv/despliegue/{api.repo.name}/src/pagos.py", line 3, in comision\n'
        "ZeroDivisionError: division by zero\n"
    )
    response = api.runtime.client.post(
        f"/api/v1/projects/{api.project_id}/triage", json={"trace": trace}, headers=api.headers
    )
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["parsed"] == 2 and found["in_project"] == 1 and found["resolved"] == 1
    frame = found["frames"][1]
    assert frame["path"] == "src/pagos.py" and frame["function"]["name"] == "comision"
    assert [rule["title"] for rule in frame["rules"]] == ["Comision por venta"]
    assert frame["recent"][0]["subject"] == "Arregla la comision de las ventas grandes"
    assert [fix["subject"] for fix in frame["fixes"]] == [
        "Arregla la comision de las ventas grandes"
    ]
    assert found["frames"][0]["path"] is None  # a library frame is not the project's


def test_a_rule_without_a_decision_to_follow_has_no_history_but_no_error(api: Api) -> None:
    """A rule whose code holds no decision (no anchor) answers with nothing to show, not 404."""
    with api.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        version = create_business_rule(
            session, project, canonical_key="manual/sin-codigo",
            category=RuleCategory.WORKFLOW,
            content=RuleContent(title="Sin codigo", statement="Una regla escrita a mano."),
        )  # fmt: skip
        entity_id = session.get_one(
            KnowledgeEntityVersion, version.knowledge_entity_version_id
        ).knowledge_entity_id
    path = f"/api/v1/projects/{api.project_id}/rules/{entity_id}/history"
    response = api.runtime.client.get(path)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "rule_id": str(entity_id),
        "title": "Sin codigo",
        "anchors": [],
        "reason": "no_decision",
    }
    missing = f"/api/v1/projects/{api.project_id}/rules/{uuid.uuid4()}/history"
    assert api.runtime.client.get(missing).status_code == 404
