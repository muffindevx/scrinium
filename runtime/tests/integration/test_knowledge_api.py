"""Knowledge read API, human review with the local profile, graph and flows."""

import json
import uuid

from scrinium.application.knowledge.content import FlowContent
from scrinium.application.knowledge.entities import HUMAN, create_entity, create_flow
from scrinium.application.knowledge.evidence import add_relationship
from scrinium.domain.enums import EntityType, FlowType, RelationType
from scrinium.infrastructure.sqlite.models import Project
from tests.integration.test_analyses import Api, _extraction_reply, _rule, api, repo

__all__ = ["api", "repo"]


def _extract(api: Api) -> str:
    api.runtime.chat.reply = _extraction_reply(_rule())
    api.post(f"/api/v1/projects/{api.project_id}/extractions", {})
    rules = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/rules").json()["rules"]
    entity_id: str = rules[0]["entity_id"]
    return entity_id


def test_rulebook_lists_and_details_extracted_rules(api: Api) -> None:
    entity_id = _extract(api)
    client = api.runtime.client
    listed = client.get(f"/api/v1/projects/{api.project_id}/rules").json()["rules"]
    assert [(r["name"], r["lifecycle_status"], r["evidence_count"]) for r in listed] == [
        ("Total de factura", "proposed", 1)
    ]
    assert listed[0]["latest_version"]["verification_status"] == "unverified"
    filtered = client.get(
        f"/api/v1/projects/{api.project_id}/rules", params={"verification": "human_verified"}
    ).json()["rules"]
    assert filtered == []
    found = client.get(f"/api/v1/projects/{api.project_id}/rules", params={"q": "FACTURA"})
    assert len(found.json()["rules"]) == 1

    detail = client.get(f"/api/v1/projects/{api.project_id}/rules/{entity_id}").json()
    version = detail["versions"][0]
    assert version["origin"] == "source_extraction" and version["model_name"] == "fake:chat"
    # S3: the current version is phase 2's, with its statement; the map's stays beneath.
    assert version["content"]["statement"] == "Enunciado de R1."
    assert detail["versions"][1]["content"]["statement"].startswith("Pendiente de redactar.")
    evidence = version["evidence"][0]
    assert evidence["method"] == "llm_proposal" and evidence["locator"]["path"] == "src/billing.py"
    assert version["profile"]["implemented"] == 1
    other = client.post("/api/v1/projects", json={"name": "Otro"}, headers=api.headers).json()
    hidden = client.get(f"/api/v1/projects/{other['id']}/rules/{entity_id}")
    assert hidden.status_code == 404


def test_review_records_the_local_profile_name(api: Api) -> None:
    entity_id = _extract(api)
    client = api.runtime.client
    detail = client.get(f"/api/v1/projects/{api.project_id}/rules/{entity_id}").json()
    path = f"/api/v1/knowledge-versions/{detail['versions'][0]['id']}/verification"

    # An LLM proposal is not enough to say the source backs it.
    weak = client.post(path, json={"status": "source_supported"}, headers=api.headers)
    assert weak.status_code == 422 and weak.json()["error"] == "insufficient_evidence"
    nameless = client.post(path, json={"status": "human_verified"}, headers=api.headers)
    assert nameless.status_code == 422 and nameless.json()["error"] == "profile_name_required"

    assert client.get("/api/v1/profile").json() == {"display_name": None}
    saved = client.put("/api/v1/profile", json={"display_name": " Ana "}, headers=api.headers)
    assert saved.json() == {"display_name": "Ana"}
    assert (
        client.put("/api/v1/profile", json={"display_name": ""}, headers=api.headers).status_code
        == 422
    )
    no_csrf = client.put(
        "/api/v1/profile", json={"display_name": "X"}, headers={"Origin": api.headers["Origin"]}
    )
    assert no_csrf.status_code == 403

    confirmed = client.post(path, json={"status": "human_verified"}, headers=api.headers).json()
    assert confirmed == {
        "id": detail["versions"][0]["id"], "verification_status": "human_verified",
        "reviewed_by": "Ana",
    }  # fmt: skip
    rule = client.get(f"/api/v1/projects/{api.project_id}/rules/{entity_id}").json()
    assert rule["lifecycle_status"] == "active"

    # Findings use the same name unless another is given.
    analysis = api.analysis("sql", {"sql": "DELETE FROM invoices"})
    finding = client.post(
        f"/api/v1/findings/{analysis['findings'][0]['id']}/feedback",
        json={"status": "false_positive"},
        headers=api.headers,
    ).json()
    assert finding["reviewed_by"] == "Ana"


def test_graph_says_where_each_edge_comes_from(api: Api) -> None:
    entity_id = _extract(api)
    with api.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        service = create_entity(
            session,
            project,
            entity_type=EntityType.SERVICE,
            canonical_key="billing",
            name="Billing",
        )
        lonely = create_entity(
            session, project, entity_type=EntityType.MODULE, canonical_key="aislado", name="Aislado"
        )
        add_relationship(
            session, project, uuid.UUID(entity_id), RelationType.IMPLEMENTED_BY, service.id
        )
        ids = str(service.id), str(lonely.id)
    graph = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/graph").json()
    states = {node["id"]: node["state"] for node in graph["nodes"]}
    assert states == {entity_id: "inferred", ids[0]: "no_version", ids[1]: "no_version"}
    assert [(e["from"], e["to"], e["origin"]) for e in graph["edges"]] == [
        (entity_id, ids[0], "inferred")
    ]
    assert graph["coverage"]["entities_without_relationships"] == 1
    assert graph["coverage"]["entities_total"] == graph["coverage"]["entities_shown"] == 3


def test_flows_are_listed_with_steps_and_transitions(api: Api) -> None:
    content = FlowContent.model_validate(
        {
            "steps": [
                {"key": "inicio", "step_type": "start", "label": "Factura emitida"},
                {"key": "fin", "step_type": "end", "label": "Factura pagada"},
            ],
            "transitions": [{"from_key": "inicio", "to_key": "fin", "transition_type": "next"}],
        }
    )
    with api.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        version = create_flow(
            session, project, entity_type=EntityType.BUSINESS_FLOW, canonical_key="cobro",
            name="Cobro", flow_type=FlowType.BUSINESS, content=content, provenance=HUMAN,
        )  # fmt: skip
        del version
    client = api.runtime.client
    flows = client.get(f"/api/v1/projects/{api.project_id}/flows").json()["flows"]
    assert [(f["name"], f["flow_type"]) for f in flows] == [("Cobro", "business")]
    detail = client.get(f"/api/v1/projects/{api.project_id}/flows/{flows[0]['entity_id']}").json()
    steps = detail["versions"][0]["steps"]
    assert [s["label"] for s in steps] == ["Factura emitida", "Factura pagada"]
    assert detail["versions"][0]["transitions"] == [
        {"from": "inicio", "to": "fin", "transition_type": "next", "condition": None,
         "evidence_id": None}
    ]  # fmt: skip
    rules = client.get(f"/api/v1/projects/{api.project_id}/rules").json()["rules"]
    not_a_flow = client.get(f"/api/v1/projects/{api.project_id}/flows/{uuid.uuid4()}")
    assert not_a_flow.status_code == 404 and rules == []
    assert "Cobro" in json.dumps(flows)
    # Written by a person and unreviewed: unverified, not "inferred".
    graph = client.get(f"/api/v1/projects/{api.project_id}/graph").json()
    assert [n["state"] for n in graph["nodes"]] == ["unverified"]


def test_a_person_writes_a_rule_by_hand(api: Api) -> None:
    client = api.runtime.client
    path = f"/api/v1/projects/{api.project_id}/rules"
    body = {
        "title": "Comisión internacional",
        "statement": "Las transacciones internacionales pagan un 2 % de comisión.",
        "category": "calculation",
        "criticality": "high",
    }
    # Without a profile name there is no authorship to record.
    nameless = client.post(path, json=body, headers=api.headers)
    assert nameless.status_code == 422 and nameless.json()["error"] == "profile_name_required"
    client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)

    created = client.post(path, json=body, headers=api.headers)
    assert created.status_code == 201
    rule = created.json()
    assert rule["canonical_key"] == "manual/comision-internacional"
    assert rule["lifecycle_status"] == "active"
    assert (rule["rule_category"], rule["criticality"]) == ("calculation", "high")
    version = rule["versions"][0]
    assert version["origin"] == "human" and version["model_name"] is None
    assert version["content"]["statement"] == body["statement"]
    listed = client.get(path).json()["rules"]
    assert [r["entity_id"] for r in listed] == [rule["entity_id"]]

    again = client.post(path, json=body, headers=api.headers)
    assert again.status_code == 409 and again.json()["error"] == "rule_exists"
    for bad in ({**body, "title": " "}, {**body, "category": "magia"}, {**body, "extra": 1}):
        assert client.post(path, json=bad, headers=api.headers).status_code == 422
    no_csrf = client.post(path, json=body, headers={"Origin": api.headers["Origin"]})
    assert no_csrf.status_code == 403
    other = client.post("/api/v1/projects", json={"name": "Otro"}, headers=api.headers).json()
    assert client.get(f"/api/v1/projects/{other['id']}/rules").json()["rules"] == []


def test_engine_shows_memory_and_what_is_installed(api: Api) -> None:
    engine = api.runtime.client.get("/api/v1/system/engine").json()
    assert engine["provider"] == "llama"
    assert engine["memory_bytes"] is None or engine["memory_bytes"] > 0
    assert engine["engine"]["installed"] is False and engine["engine"]["running"] is False
    assert engine["engine"]["accelerator"] is None
    assert [(m["role"], m["installed"]) for m in engine["models"]] == [
        ("chat", False), ("embeddings", False),
    ]  # fmt: skip
