"""The review inbox (phase 4): order, groups, batch review and the F2 filter."""

import uuid
from typing import Any

from sqlalchemy import select

from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import Provenance, create_business_rule
from scrinium.application.knowledge.evidence import cite_source
from scrinium.domain.enums import (
    EvidenceRole,
    EvidenceType,
    RuleCategory,
    VerificationMethod,
    VersionOrigin,
)
from scrinium.infrastructure.local_http import ModelStatus
from scrinium.infrastructure.sqlite.models import KnowledgeEntityVersion, Project, SourceRevision
from tests.integration.test_analyses import Api
from tests.integration.test_rule_watch import COMMISSION, _change, _rule, _verify, api

__all__ = ["api"]


def _inbox(api: Api) -> dict[str, Any]:
    response = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/inbox")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _proposal(api: Api, title: str, statement: str, path: str, lines: tuple[int, int]) -> str:
    """A rule as a model would propose it, cited at `lines` of `path`."""
    with api.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        version = create_business_rule(
            session, project, canonical_key=f"t/{uuid.uuid4().hex[:8]}",
            category=RuleCategory.CALCULATION,
            content=RuleContent(title=title, statement=statement),
            provenance=Provenance(
                VersionOrigin.SOURCE_EXTRACTION, "fake", "fake", extractor_version="test"
            ),
        )  # fmt: skip
        revision = session.scalars(
            select(SourceRevision.id)
            .where(SourceRevision.metadata_["path"].as_string() == path)
            .order_by(SourceRevision.captured_at.desc())
        ).first()
        assert revision is not None
        entity_id = session.get_one(
            KnowledgeEntityVersion, version.knowledge_entity_version_id
        ).knowledge_entity_id
        cite_source(
            session, project, entity_id=entity_id,
            version_id=version.knowledge_entity_version_id, revision_id=revision,
            start_line=lines[0], end_line=lines[1], role=EvidenceRole.SUPPORTS,
            evidence_type=EvidenceType.CODE, method=VerificationMethod.LLM_PROPOSAL,
        )  # fmt: skip
        return str(version.knowledge_entity_version_id)


def test_unreviewed_rules_are_in_the_inbox_until_reviewed(api: Api) -> None:
    box = _inbox(api)
    [item] = box["items"]
    assert item["title"] == "Comision por venta" and item["review_reason"] is None
    assert item["parts"]["uncertainty"] == 1.0 and item["score"] > 0
    _verify(api)
    assert _inbox(api)["total"] == 0
    # The verified rule drifts: back in the inbox, ahead of a new one.
    api.runtime.chat.state = ModelStatus.UNAVAILABLE
    _change(api, COMMISSION.replace("> 100", "> 150"))
    _proposal(api, "Recargo", "Se recarga un 10 % al importe.", "src/recargos.py", (4, 4))
    box = _inbox(api)
    items = box["items"]
    assert items[0]["review_reason"] == "rule_changed" and items[0]["parts"]["uncertainty"] == 1.5
    # The tabs: the drifted rule needs attention, the new proposal is in the rest.
    assert box["buckets"]["attention"] == 1 and sum(box["buckets"].values()) == box["total"]


def test_a_statement_with_a_number_its_code_lacks_stays_out(api: Api) -> None:
    _proposal(api, "Recargo alto", "Se recarga un 25 % al importe.", "src/recargos.py", (4, 4))
    _proposal(api, "Recargo", "Se recarga un 10 % al importe.", "src/recargos.py", (4, 4))
    box = _inbox(api)
    titles = [item["title"] for item in box["items"]]
    assert "Recargo" in titles and "Recargo alto" not in titles
    assert box["unanchored_total"] == 1 and box["unanchored"][0]["missing"] == ["25 %"]


def test_near_identical_rules_form_a_group_reviewed_at_once(api: Api) -> None:
    first = _proposal(
        api, "Negativos A", "Un importe negativo no se acepta.", "src/recargos.py", (2, 3)
    )
    second = _proposal(
        api, "Negativos B", "Un importe negativo no se acepta nunca.", "src/recargos.py", (2, 3)
    )
    items = {item["version_id"]: item for item in _inbox(api)["items"]}
    group = items[first]["group"]
    assert group is not None and group == items[second]["group"] and group["size"] == 2
    api.runtime.client.put("/api/v1/profile", json={"display_name": "Ana"}, headers=api.headers)
    response = api.runtime.client.post(
        f"/api/v1/projects/{api.project_id}/inbox/review",
        json={"version_ids": [first], "status": "human_verified"},
        headers=api.headers,
    )
    assert response.status_code == 200, response.text
    # One confirmed: its near-identical twin drops to low uncertainty.
    items = {item["version_id"]: item for item in _inbox(api)["items"]}
    assert first not in items and items[second]["parts"]["uncertainty"] == 0.3
    response = api.runtime.client.post(
        f"/api/v1/projects/{api.project_id}/inbox/review",
        json={"version_ids": [second], "status": "rejected", "note": "duplicada"},
        headers=api.headers,
    )
    assert response.status_code == 200
    assert second not in {item["version_id"] for item in _inbox(api)["items"]}
    assert _rule(api)["entity_id"]  # The fixture's rule is still there.


def test_health_counts_without_listing_rules(api: Api) -> None:
    def health() -> dict[str, Any]:
        return dict(api.runtime.client.get(f"/api/v1/projects/{api.project_id}/health").json())

    first = health()
    assert first["rules"] == 1 and first["unreviewed"] == 1 and first["inbox"] == 1
    assert first["new_this_week"] == 1 and first["reviewed_this_week"] == 0
    assert first["last_sync_at"] and first["last_review_at"] is None
    _verify(api)
    api.runtime.chat.state = ModelStatus.UNAVAILABLE
    _change(api, COMMISSION.replace("> 100", "> 150"))
    after = health()
    assert after["verified"] == 1 and after["drifted"] == 1 and after["inbox"] == 1
    assert after["last_review_at"] and after["reviewed_this_week"] == 1


def test_activity_lists_syncs_and_reviews_without_extraction_rows(api: Api) -> None:
    _verify(api)
    events = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/activity").json()["events"]
    kinds = [event["kind"] for event in events]
    assert "sync" in kinds and "knowledge_version.verify" in kinds
    review = next(event for event in events if event["kind"] == "knowledge_version.verify")
    assert review["subject"] == "Comision por venta" and review["to"] == "human_verified"
    # The bulk rows an extraction writes stay out; newest first.
    assert not {"evidence.create", "knowledge_version.create"} & set(kinds)
    assert [event["at"] for event in events] == sorted((e["at"] for e in events), reverse=True)


def test_consistency_runs_on_its_own_after_an_extraction(api: Api) -> None:
    from scrinium.application.analysis.consistency import CONSISTENCY_DESCRIPTION
    from scrinium.infrastructure.sqlite.models import AnalysisRun

    def reviews() -> int:
        with api.runtime.database.session() as session:
            return len(
                session.scalars(
                    select(AnalysisRun.id).where(AnalysisRun.question == CONSISTENCY_DESCRIPTION)
                ).all()
            )

    # One rule: nothing to compare.
    assert reviews() == 0
    _proposal(api, "Recargo", "Se recarga un 10 % al importe.", "src/recargos.py", (4, 4))
    run = api.post(f"/api/v1/projects/{api.project_id}/extractions", {})
    assert api.read(run["id"])["status"] == "completed"
    assert reviews() == 1
