"""Stage 3, phase C: relationships between rules, from the code and without the model."""

import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from scrinium.application.analysis.rule_relations import rebuild_relations
from scrinium.application.knowledge.content import FlowContent, RuleContent
from scrinium.application.knowledge.entities import HUMAN, create_business_rule, create_flow
from scrinium.application.knowledge.evidence import add_relationship, cite_source
from scrinium.domain.enums import (
    EntityType,
    EvidenceRole,
    EvidenceType,
    FlowType,
    RelationType,
    RuleCategory,
    VerificationMethod,
)
from scrinium.infrastructure.sqlite.models import (
    EntityRelationship,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SourceRevision,
)
from tests.conftest import Runtime
from tests.integration.test_analyses import Api
from tests.integration.test_ingestion import commit_all, git, write

PAGOS = """\
export function cobrar(total: number) {
  if (total > MAX_TOTAL) throw new Error('limite')
  if (total < MIN_TOTAL) throw new Error('minimo')
  return comision(total)
}

export function comision(total: number) {
  return total > 100 ? total * TAX_RATE : 0
}

export function reembolso(total: number) {
  return total > 100 ? total * TAX_RATE : 0
}
"""


def _rule(api: Api, key: str, start: int, end: int) -> uuid.UUID:
    with api.runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        content = RuleContent.model_validate({"title": key, "statement": f"Regla {key}."})
        version = create_business_rule(
            session, project, canonical_key=key, category=RuleCategory.VALIDATION,
            content=content, provenance=HUMAN,
        )  # fmt: skip
        entity_id = session.get_one(
            KnowledgeEntityVersion, version.knowledge_entity_version_id
        ).knowledge_entity_id
        revision = session.scalars(
            select(SourceRevision.id).where(
                SourceRevision.metadata_["path"].as_string() == "src/pagos.ts"
            )
        ).first()
        assert revision is not None
        cite_source(
            session, project, entity_id=entity_id, revision_id=revision, start_line=start,
            end_line=end, role=EvidenceRole.IMPLEMENTS, evidence_type=EvidenceType.CODE,
            method=VerificationMethod.DIRECT_REFERENCE,
            version_id=version.knowledge_entity_version_id,
        )  # fmt: skip
        return entity_id


def test_rules_are_related_by_function_calls_constants_and_duplicates(
    runtime: Runtime, tmp_path: Path
) -> None:
    root = tmp_path / "pagos"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/pagos.ts", PAGOS)
    commit_all(root, "inicio")
    api = Api(runtime, root)
    maximo = _rule(api, "maximo", 2, 2)
    minimo = _rule(api, "minimo", 3, 3)
    tasa = _rule(api, "tasa", 8, 8)
    copia = _rule(api, "copia", 12, 12)
    # A relationship a person made survives the rebuild.
    with runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        add_relationship(session, project, maximo, RelationType.RELATED_TO, copia)

    # Rebuilding twice gives the same: the derived ones are replaced, not added up. The map
    # asks for it for rules extracted before phase C.
    assert rebuild_relations(runtime.database, uuid.UUID(api.project_id)) == 4
    response = runtime.client.post(
        f"/api/v1/projects/{api.project_id}/relations", headers=api.headers
    )
    assert response.status_code == 200
    assert response.json() == {"relationships": 4}
    with runtime.database.session() as session:
        found = {
            (frozenset({r.from_entity_id, r.to_entity_id}), r.relation_type, r.basis)
            for r in session.scalars(select(EntityRelationship))
        }
    assert found == {
        (frozenset({maximo, minimo}), RelationType.RELATED_TO, "same_function:cobrar"),
        # Both live in `cobrar`, which calls `comision`.
        (frozenset({maximo, tasa}), RelationType.DEPENDS_ON, "calls:1"),
        (frozenset({minimo, tasa}), RelationType.DEPENDS_ON, "calls:1"),
        # Same code in two places.
        (frozenset({tasa, copia}), RelationType.RELATED_TO, "duplicate"),
        (frozenset({maximo, copia}), RelationType.RELATED_TO, None),
    }
    detail = runtime.client.get(f"/api/v1/projects/{api.project_id}/rules/{maximo}").json()
    bases = {(r["other"]["id"], r["basis"], r["direction"]) for r in detail["relationships"]}
    assert (str(tasa), "calls:1", "out") in bases


def test_a_rule_says_what_depends_on_it(runtime: Runtime, tmp_path: Path) -> None:
    """N7: the impact of a rule is phase C read backwards, plus processes and tickets."""
    root = tmp_path / "pagos"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "src/pagos.ts", PAGOS)
    commit_all(root, "inicio")
    api = Api(runtime, root)
    maximo = _rule(api, "maximo", 2, 2)
    minimo = _rule(api, "minimo", 3, 3)
    tasa = _rule(api, "tasa", 8, 8)
    copia = _rule(api, "copia", 12, 12)
    rebuild_relations(runtime.database, uuid.UUID(api.project_id))
    with runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(api.project_id))
        session.get_one(KnowledgeEntity, maximo).metadata_ = {"tickets": ["PAY-7"]}
        content = FlowContent.model_validate(
            {
                "steps": [
                    {"key": "inicio", "step_type": "start", "label": "Pedido"},
                    {"key": "cobro", "step_type": "decision", "label": "Cobrar",
                     "entity_id": str(maximo)},
                ],
                "transitions": [{"from_key": "inicio", "to_key": "cobro",
                                 "transition_type": "next"}],
            }
        )  # fmt: skip
        create_flow(
            session, project, entity_type=EntityType.BUSINESS_FLOW, canonical_key="compra",
            name="Compra", flow_type=FlowType.BUSINESS, content=content,
        )  # fmt: skip

    def impact(entity_id: uuid.UUID) -> Any:
        url = f"/api/v1/projects/{api.project_id}/rules/{entity_id}"
        response = runtime.client.get(url)
        assert response.status_code == 200
        return response.json()["impact"]

    # `cobrar` calls `comision`: both rules in `cobrar` depend on `tasa`.
    found = impact(tasa)
    assert [(d["name"], d["depth"], d["via"]) for d in found["dependents"]] == [
        ("maximo", 1, None),
        ("minimo", 1, None),
    ]
    assert found["dependents"][0]["tickets"] == ["PAY-7"]
    assert [s["name"] for s in found["same_decision"]] == ["copia"]
    # The process reaches `tasa` through `maximo`, a step of it.
    assert found["flows"] == [{"id": found["flows"][0]["id"], "name": "Compra",
                               "through": ["maximo"]}]  # fmt: skip

    # A copy of the same decision: whoever relies on `tasa` relies on it too.
    found = impact(copia)
    assert [s["name"] for s in found["same_decision"]] == ["tasa"]
    assert [(d["name"], d["via"]["name"]) for d in found["dependents"]] == [
        ("maximo", "tasa"),
        ("minimo", "tasa"),
    ]

    # Nothing calls into `cobrar`: nothing depends on `maximo`, but its process and ticket show.
    found = impact(maximo)
    assert found["dependents"] == []
    assert [f["name"] for f in found["flows"]] == ["Compra"]
    assert found["tickets"] == ["PAY-7"]
    assert impact(minimo)["flows"] == []
