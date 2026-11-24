"""S3: the rule map first; the statements later, in the background or on demand."""

from typing import Any

from tests.integration.test_analyses import Api, _extraction_reply, _rule, api, repo

__all__ = ["api", "repo"]

PENDING = "Pendiente de redactar."


def _rules(api: Api) -> list[dict[str, Any]]:
    url = f"/api/v1/projects/{api.project_id}/rules"
    return list(api.runtime.client.get(url).json()["rules"])


def _statement(api: Api, entity_id: str) -> str:
    url = f"/api/v1/projects/{api.project_id}/rules/{entity_id}"
    detail = api.runtime.client.get(url).json()
    return str(detail["versions"][0]["content"]["statement"])


def test_a_rule_opened_before_its_statement_gets_one_on_demand(api: Api) -> None:
    api.runtime.chat.reply = _extraction_reply(_rule())
    api.runtime.chat.fail_statements = True
    run = api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    assert run["status"] == "completed"
    entity_id = _rules(api)[0]["entity_id"]
    # The map is there, but phase 2 failed: the rule keeps its provisional statement.
    assert _statement(api, entity_id).startswith(PENDING)

    api.runtime.chat.fail_statements = False
    url = f"/api/v1/projects/{api.project_id}/rules/{entity_id}/statement"
    written = api.runtime.client.post(url, headers=api.headers)
    assert written.status_code == 200
    assert written.json()["versions"][0]["content"]["statement"] == "Enunciado de R1."
    # With a statement, asking again neither calls the model nor creates another version.
    calls = len(api.runtime.chat.received)
    again = api.runtime.client.post(url, headers=api.headers)
    assert again.status_code == 200 and len(again.json()["versions"]) == 2
    assert len(api.runtime.chat.received) == calls


def test_statements_run_as_their_own_analysis_after_the_map(api: Api) -> None:
    api.runtime.chat.reply = _extraction_reply(_rule())
    api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    runs = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/analyses").json()
    descriptions = [r.get("question") or r.get("description") for r in runs["analyses"]]
    assert "Redactar los enunciados de las reglas" in descriptions
    assert not _statement(api, _rules(api)[0]["entity_id"]).startswith(PENDING)


def test_without_the_model_a_statement_cannot_be_written(api: Api) -> None:
    api.runtime.chat.reply = _extraction_reply(_rule())
    api.runtime.chat.fail_statements = True
    api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    entity_id = _rules(api)[0]["entity_id"]
    api.runtime.chat.fail_statements = False
    api.runtime.chat.state = api.runtime.chat.state.__class__.UNAVAILABLE
    api.runtime.llm._status_cache = None
    url = f"/api/v1/projects/{api.project_id}/rules/{entity_id}/statement"
    refused = api.runtime.client.post(url, headers=api.headers)
    assert refused.status_code == 503 and refused.json()["error"] == "llm_unavailable"


def test_a_title_the_schema_cut_is_marked_and_written_again_whole(api: Api) -> None:
    # B1: past 60 characters the title is cut at a word, and marked as cut.
    long = "El estado de un nodo de agente debe ser uno de los valores definidos"
    api.runtime.chat.reply = _extraction_reply(_rule(long))
    api.runtime.chat.fail_statements = True
    api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    # Until phase 2, the cut shows: it does not read as a whole title.
    assert _rules(api)[0]["name"] == "El estado de un nodo de agente debe ser uno de los valores…"

    api.runtime.chat.fail_statements = False
    entity_id = _rules(api)[0]["entity_id"]
    url = f"/api/v1/projects/{api.project_id}/rules/{entity_id}/statement"
    content = api.runtime.client.post(url, headers=api.headers).json()["versions"][0]["content"]
    assert content["title"] == "Titulo completo de R1"
    assert content["statement"] == "Enunciado de R1."


def test_only_a_cut_title_is_written_again(api: Api) -> None:
    api.runtime.chat.reply = _extraction_reply(_rule())
    api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    assert _rules(api)[0]["name"] == "Total de factura"
