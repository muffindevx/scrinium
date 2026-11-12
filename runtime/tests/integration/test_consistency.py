"""Rulebook consistency: duplicates, contradictions and ambiguity."""

import json
from pathlib import Path
from typing import Any

import pytest

from scrinium.infrastructure.local_http import ModelStatus
from tests.conftest import Runtime
from tests.integration.test_analyses import Api
from tests.integration.test_ingestion import commit_all, git, write

PAGOS = "def comision(total):\n    return total * 0.02 if total > 100 else 0\n"
RECARGOS = "def recargo(importe):\n    if importe < 0:\n        raise ValueError()\n"
TEST_PAGOS = (
    "from src.pagos import comision\n\n\ndef test_comision():\n    assert comision(200) == 4\n"
)
SAME = "Las ventas de mas de 100 pagan un 2 % de comision sobre el total."


@pytest.fixture
def api(runtime: Runtime, tmp_path: Path) -> Api:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/pagos.py", PAGOS)
    write(root, "src/recargos.py", RECARGOS)
    write(root, "tests/test_pagos.py", TEST_PAGOS)
    commit_all(root, "inicio")
    return Api(runtime, root)


def _rules(api: Api) -> dict[str, dict[str, Any]]:
    listed = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/rules").json()["rules"]
    return {rule["canonical_key"]: rule for rule in listed}


def _detail(api: Api, entity_id: str) -> dict[str, Any]:
    detail: dict[str, Any] = api.runtime.client.get(
        f"/api/v1/projects/{api.project_id}/rules/{entity_id}"
    ).json()
    return detail


def _review(api: Api, pairs: list[dict[str, Any]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    api.runtime.chat.reply = json.dumps({"pairs": pairs, "rules": rules})
    return api.read(api.post(f"/api/v1/projects/{api.project_id}/consistency", {})["id"])


def test_review_flags_duplicates_and_ambiguity(api: Api) -> None:
    first = api.rule("comision-venta", SAME, "src/pagos.py")
    second = api.rule("comision-ventas", SAME.replace("Las ventas", "Toda venta"), "src/pagos.py")
    api.rule("recargo", "Un importe negativo no se acepta.", "src/recargos.py")

    detail = _review(
        api,
        pairs=[{"pair": "P1", "verdict": "duplicate", "explanation": "Dicen lo mismo."}],
        rules=[{"rule": "R1", "ambiguous": True, "reason": "No dice que pasa con 100 justos."}],
    )
    assert detail["status"] == "completed", detail
    result = detail["result"]
    assert result["kind"] == "consistency" and result["rules"] == 3
    assert result["tokens"]["calls"] == result["batches"] and result["tokens"]["output"] > 0
    assert result["pairs"] == 1 and result["duplicates"] == 1
    assert result["ambiguous"] >= 1
    # A test calls comision(), but tests are never cited: they check rules, not explain them.
    assert "tests_linked" not in result
    version = _detail(api, str(first))["versions"][0]
    assert all(e["locator"]["path"] != "tests/test_pagos.py" for e in version["evidence"])

    rules = _rules(api)
    assert rules["comision-venta"]["review_reason"] == "possible_duplicate"
    assert rules["comision-ventas"]["review_reason"] == "possible_duplicate"
    kinds = {f["location"]["kind"] for f in detail["findings"]}
    assert kinds == {"duplicate", "ambiguous"}
    assert all(f["assessment_status"] == "hypothesis" for f in detail["findings"])

    # Merging requires the profile name.
    path = f"/api/v1/projects/{api.project_id}/rules/{first}/merge"
    nameless = api.runtime.client.post(
        path, json={"duplicate_id": str(second)}, headers=api.headers
    )
    assert nameless.status_code == 422 and nameless.json()["error"] == "profile_name_required"
    api.runtime.client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)
    same = api.runtime.client.post(path, json={"duplicate_id": str(first)}, headers=api.headers)
    assert same.status_code == 422
    merged = api.runtime.client.post(path, json={"duplicate_id": str(second)}, headers=api.headers)
    assert merged.status_code == 200, merged.text
    rules = _rules(api)
    assert rules["comision-ventas"]["lifecycle_status"] == "retired"
    assert rules["comision-venta"]["review_reason"] is None


def test_contradictions_are_high_severity(api: Api) -> None:
    api.rule("comision-dos", SAME, "src/pagos.py")
    api.rule("comision-tres", SAME.replace("2 %", "3 %"), "src/pagos.py")
    detail = _review(
        api, [{"pair": "P1", "verdict": "contradicts", "explanation": "2 % o 3 %."}], []
    )
    contradiction = [f for f in detail["findings"] if f["finding_type"] == "contradiction"]
    assert len(contradiction) == 1 and contradiction[0]["severity"] == "high"
    assert {r["review_reason"] for r in _rules(api).values()} == {"contradiction"}


def test_without_embeddings_similar_rules_are_found_by_words(api: Api) -> None:
    api.rule("comision-a", SAME, "src/pagos.py")
    api.rule("comision-b", SAME + " Siempre.", "src/pagos.py")
    api.rule("recargo", "Un importe negativo no se acepta.", "src/recargos.py")
    api.runtime.embeddings.state = ModelStatus.UNAVAILABLE
    api.runtime.runner.indexer._status_cache = None
    detail = _review(api, [], [])
    assert detail["result"]["pairs"] == 1
