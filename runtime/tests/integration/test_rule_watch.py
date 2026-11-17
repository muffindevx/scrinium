"""Rule watch: the code backing a rule changes, is adjusted or disappears."""

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from scrinium.infrastructure.local_http import ModelStatus
from tests.conftest import Runtime
from tests.integration.test_analyses import Api
from tests.integration.test_ingestion import commit_all, git, write

COMMISSION = """\
def comision(total):
    if total > 100:
        return total * 0.02
    return 0
"""
OTHER = """\
def recargo(importe):
    if importe < 0:
        raise ValueError('importe negativo')
    return importe * 1.1
"""


def _reply(
    rules: list[dict[str, Any]] | None = None, existing: list[dict[str, Any]] | None = None
) -> str:
    # One answer per decision (R1b): recargo is D1 (it goes first) and comision D2.
    answers: list[dict[str, Any] | None] = [None, *rules] if rules else []
    numbered = [{"i": f"D{n}", "r": a} for n, a in enumerate(answers, 1)]
    return json.dumps({"d": numbered, "existing": existing or []})


def _commission_rule() -> dict[str, Any]:
    return {
        "t": "Comision por venta",
        "s": "Las ventas de mas de 100 pagan un 2 % de comision.",
        "c": "cal",
    }


@pytest.fixture
def api(runtime: Runtime, tmp_path: Path) -> Api:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/pagos.py", COMMISSION)
    write(root, "src/recargos.py", OTHER)
    commit_all(root, "inicio")
    api = Api(runtime, root)
    runtime.chat.reply = _reply([_commission_rule()])
    detail = api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", {})["id"])
    assert detail["status"] == "completed"
    return api


def _rule(api: Api) -> dict[str, Any]:
    rules = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/rules").json()["rules"]
    found = [rule for rule in rules if rule["name"] == "Comision por venta"]
    assert len(found) == 1, rules
    detail: dict[str, Any] = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/rules/{found[0]['entity_id']}"
    ).json()
    return {**found[0], "detail": detail}


def _change(api: Api, text: str) -> None:
    write(api.repo, "src/pagos.py", text)
    commit_all(api.repo, "cambio")
    api.runtime.chat.received.clear()
    api.sync()


def test_a_changed_threshold_adjusts_the_rule(api: Api) -> None:
    api.runtime.chat.reply = _reply(
        existing=[{"ref": "R1", "verdict": "adjusted", "statement": "Mas de 200 pagan un 2 %."}]
    )
    _change(api, COMMISSION.replace("> 100", "> 200"))

    # Only the changed function goes back to the model, with its rule.
    assert len(api.runtime.chat.received) == 1
    prompt = api.runtime.chat.received[0][1].content
    assert "reglas existentes" in prompt and "[R1] Comision por venta" in prompt
    assert "funcion: comision" in prompt and "recargo" not in prompt

    rule = _rule(api)
    assert rule["review_reason"] is None
    latest, previous = rule["detail"]["versions"][0], rule["detail"]["versions"][1]
    assert latest["content"]["statement"] == "Mas de 200 pagan un 2 %."
    # The earlier statement is phase 2's (S3); in tests, the fake model's.
    assert "Antes: Enunciado de R1." in latest["content"]["rationale"]
    assert latest["verification_status"] == "unverified"
    assert previous["content"]["statement"] == "Enunciado de R1."
    # The new version cites the new code.
    assert latest["evidence"][0]["revision_id"] != previous["evidence"][0]["revision_id"]


def test_code_that_no_longer_follows_the_rule_is_flagged_once(api: Api) -> None:
    api.runtime.chat.reply = _reply(existing=[{"ref": "R1", "verdict": "broken"}])
    _change(api, COMMISSION.replace("total * 0.02", "total * 0"))
    rule = _rule(api)
    assert rule["review_reason"] == "code_contradicts"
    roles = {e["role"] for e in rule["detail"]["versions"][0]["evidence"]}
    assert "contradicts" in roles

    # Another sync of another file: the rule does not go back to the model.
    write(api.repo, "src/recargos.py", OTHER.replace("1.1", "1.2"))
    commit_all(api.repo, "otro")
    api.runtime.chat.received.clear()
    api.runtime.chat.reply = _reply()
    api.sync()
    assert all("Comision por venta" not in batch[1].content for batch in api.runtime.chat.received)
    assert _rule(api)["review_reason"] == "code_contradicts"


def test_a_kept_rule_is_anchored_to_the_new_code(api: Api) -> None:
    api.runtime.chat.reply = _reply(existing=[{"ref": "R1", "verdict": "same"}])
    _change(
        api,
        COMMISSION.replace("return 0", "return 0  # sin comision").replace(
            "    if", "    # umbral\n    if"
        ),
    )
    # With new comments but the same logic, nothing is asked.
    assert api.runtime.chat.received == []
    _change(api, COMMISSION.replace("total * 0.02", "round(total * 0.02, 2)"))
    assert len(api.runtime.chat.received) == 1
    rule = _rule(api)
    assert rule["review_reason"] is None
    # The map and its statement (S3); the kept rule creates no other version.
    assert len(rule["detail"]["versions"]) == 2
    # Without further changes, a new sync does not flag it again.
    api.runtime.chat.received.clear()
    api.sync()
    assert _rule(api)["review_reason"] is None and api.runtime.chat.received == []


def test_removed_code_is_flagged_without_the_model(api: Api) -> None:
    api.runtime.chat.reply = _reply()
    _change(api, "def otra_cosa():\n    return 1\n")
    assert _rule(api)["review_reason"] == "code_removed"
    assert all("[R1]" not in batch[1].content for batch in api.runtime.chat.received)


def test_changes_are_flagged_even_without_the_model(api: Api) -> None:
    api.runtime.chat.state = ModelStatus.UNAVAILABLE
    _change(api, COMMISSION.replace("> 100", "> 300"))
    assert api.runtime.chat.received == []
    # The same decision with another threshold: the rule changed (phase 2.1).
    assert _rule(api)["review_reason"] == "rule_changed"


def _verify(api: Api) -> None:
    client = api.runtime.client
    client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)
    version_id = _rule(api)["detail"]["versions"][0]["id"]
    response = client.post(
        f"/api/v1/knowledge-versions/{version_id}/verification",
        json={"status": "human_verified"},
        headers=api.headers,
    )
    assert response.status_code == 200, response.text


def _latest_anchor(api: Api) -> dict[str, Any]:
    evidence = _rule(api)["detail"]["versions"][0]["evidence"]
    with api.runtime.database.session() as session:
        from scrinium.infrastructure.sqlite.models import EvidenceRecord

        records = [session.get(EvidenceRecord, uuid.UUID(e["id"])) for e in evidence]
        latest = max((r for r in records if r is not None), key=lambda r: r.created_at)
        return {"path": latest.locator["path"], **latest.locator["anchor"]}


def test_a_renamed_function_keeps_its_verified_rule(api: Api) -> None:
    # ADR-011: identity by anchor. Renaming the function and its local, and a model that
    # titles the rule differently, leave one rule, still verified.
    _verify(api)
    api.runtime.chat.reply = json.dumps(
        {"d": [{"i": "D1", "r": {**_commission_rule(), "t": "Comision de ventas grandes"}}]}
    )
    _change(
        api, COMMISSION.replace("comision(total)", "commission(amount)").replace("total", "amount")
    )
    # The renamed function went to the model as new code, and it proposed the rule again.
    assert any("funcion: commission" in batch[1].content for batch in api.runtime.chat.received)
    rules = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/rules").json()["rules"]
    assert [r["name"] for r in rules if "omision" in r["name"]] == ["Comision por venta"]
    rule = _rule(api)
    assert rule["review_reason"] is None
    assert rule["detail"]["versions"][0]["verification_status"] == "human_verified"
    assert _latest_anchor(api)["symbol"] == "commission"


def test_a_moved_decision_follows_its_rule(api: Api) -> None:
    api.runtime.chat.reply = _reply()
    write(api.repo, "src/comisiones.py", COMMISSION)
    _change(api, "def otra_cosa():\n    return 1\n")
    assert _rule(api)["review_reason"] is None
    assert _latest_anchor(api)["path"] == "src/comisiones.py"


def test_a_change_beside_the_decision_does_not_flag_the_rule(api: Api) -> None:
    api.runtime.chat.reply = _reply()
    _change(api, COMMISSION.replace("    return 0\n", "    nota = 'sin comision'\n    return 0\n"))
    assert _rule(api)["review_reason"] is None
    assert all("[R1]" not in batch[1].content for batch in api.runtime.chat.received)


def test_a_rule_merged_by_title_gives_back_its_other_place(api: Api) -> None:
    # Before anchors, a later extraction could give the same title to the decision in
    # recargos.py and attach it to this rule: pagos.py was left without a rule (ADR-011, 1.6).
    from sqlalchemy import select

    from scrinium.application.knowledge.content import RuleContent
    from scrinium.application.knowledge.entities import add_rule_version, latest_version
    from scrinium.application.knowledge.evidence import cite_source
    from scrinium.application.knowledge.split import split_title_merges
    from scrinium.domain.enums import EvidenceRole, EvidenceType, VerificationMethod
    from scrinium.infrastructure.sqlite.models import (
        KnowledgeEntity,
        Project,
        SourceItem,
        SourceRevision,
    )

    original = uuid.UUID(_rule(api)["entity_id"])
    with api.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        content = RuleContent.model_validate(
            {"title": "Comision por venta", "statement": "Importes negativos no se aceptan."}
        )
        version, _ = add_rule_version(session, project, original, content)
        recargos = session.scalars(
            select(SourceRevision)
            .join(SourceItem, SourceItem.current_revision_id == SourceRevision.id)
            .where(SourceRevision.metadata_["path"].as_string() == "src/recargos.py")
        ).one()
        cite_source(
            session, project, entity_id=original, revision_id=recargos.id, start_line=2,
            end_line=3, role=EvidenceRole.SUPPORTS, evidence_type=EvidenceType.CODE,
            method=VerificationMethod.LLM_PROPOSAL, version_id=version.knowledge_entity_version_id,
        )  # fmt: skip
        latest_before = latest_version(session, original)
        assert latest_before is not None

        created = split_title_merges(session, project)
        assert len(created) == 1
        assert split_title_merges(session, project) == []
        latest_after = latest_version(session, original)
        assert latest_after is not None and latest_after.id == latest_before.id
        new_rule = session.get_one(KnowledgeEntity, created[0])
        assert new_rule.name == "Comision por venta" and new_rule.canonical_key != (
            session.get_one(KnowledgeEntity, original).canonical_key
        )

    detail = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/rules/{created[0]}").json()
    evidence = detail["versions"][0]["evidence"]
    assert [(e["locator"]["path"], e["locator"]["start_line"]) for e in evidence] == [
        ("src/pagos.py", 2)
    ]


def test_a_verified_rule_whose_value_changed_waits_for_a_person(api: Api) -> None:
    # Phase 2.1: the model may say the rule still holds; the flag stays until a person looks.
    _verify(api)
    api.runtime.chat.reply = _reply(existing=[{"ref": "R1", "verdict": "same"}])
    _change(api, COMMISSION.replace("> 100", "> 150"))
    assert _rule(api)["review_reason"] == "rule_changed"


def test_an_edited_decision_is_to_review_not_changed(api: Api) -> None:
    # The guard gained a condition: its shape changed, found only by similarity.
    api.runtime.chat.state = ModelStatus.UNAVAILABLE
    _change(api, COMMISSION.replace("if total > 100:", "if total > 100 and total < 10000:"))
    assert _rule(api)["review_reason"] == "code_changed"


def _exported(api: Api) -> list[Any]:
    from sqlalchemy import select

    from scrinium.application.knowledge.rulebook import verified_rules
    from scrinium.infrastructure.sqlite.models import SourceConnection

    with api.runtime.database.session() as session:
        source = session.scalars(
            select(SourceConnection).where(SourceConnection.project_id == uuid.UUID(api.project_id))
        ).one()
        return verified_rules(session, uuid.UUID(api.project_id), source.id)


def test_only_verified_rules_are_exported_with_their_anchors(api: Api) -> None:
    from scrinium.application.check.rule_files import parse, render

    assert _exported(api) == []
    _verify(api)
    [rule] = _exported(api)
    assert rule.id == _rule(api)["entity_id"] and rule.verified_by == "Ana"
    assert [a.path for a in rule.anchors] == ["src/pagos.py"]
    assert parse(render(rule)) == rule
    text = render(rule)
    assert "total" not in text  # No source code in a rule file: paths, lines, fingerprints.


def test_verifying_again_moves_the_anchor_to_the_new_values(api: Api) -> None:
    _verify(api)
    [before] = _exported(api)
    api.runtime.chat.reply = _reply(existing=[{"ref": "R1", "verdict": "same"}])
    _change(api, COMMISSION.replace("> 100", "> 150"))
    assert _rule(api)["review_reason"] == "rule_changed"
    # The model said it holds; the file still carries what the person verified.
    assert _exported(api)[0].anchors == before.anchors
    _verify(api)
    [after] = _exported(api)
    assert _rule(api)["review_reason"] is None
    assert after.anchors[0].shape == before.anchors[0].shape
    assert after.anchors[0].value != before.anchors[0].value


def test_check_explain_is_one_sentence_from_the_task_model(api: Api) -> None:
    from scrinium.application.check.explain import Item, explain

    entity_id = _rule(api)["entity_id"]
    item = Item(
        rule_id=entity_id, title="Comision por venta", path="src/pagos.py", start_line=2,
        end_line=3, before="if total > 100:", after="if total > 150:",
    )  # fmt: skip
    api.runtime.chat.reply = json.dumps({"explanation": "El umbral pasa de  100 a 150."})
    api.runtime.chat.received.clear()
    found = explain(api.runtime.database, api.runtime.llm, [item])
    assert [(e.rule_id, e.text) for e in found] == [(entity_id, "El umbral pasa de 100 a 150.")]
    prompt = api.runtime.chat.received[0][1].content
    assert "if total > 150:" in prompt and "Comision por venta" in prompt
    # A rule this Scrinium does not know, or a model that is not there: nothing, no error.
    unknown = item.model_copy(update={"rule_id": str(uuid.uuid4())})
    assert explain(api.runtime.database, api.runtime.llm, [unknown]) == []
    api.runtime.chat.state = ModelStatus.UNAVAILABLE
    assert explain(api.runtime.database, api.runtime.llm, [item]) == []
