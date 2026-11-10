"""Rule watch: the code backing a rule changes, is adjusted or disappears."""

import json
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
    assert _rule(api)["review_reason"] == "code_changed"
