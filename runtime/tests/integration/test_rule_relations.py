"""Stage 3, phase C: relationships between rules, from the code and without the model."""

import uuid
from pathlib import Path

from sqlalchemy import select

from scrinium.application.analysis.rule_relations import rebuild_relations
from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.entities import HUMAN, create_business_rule
from scrinium.application.knowledge.evidence import add_relationship, cite_source
from scrinium.domain.enums import (
    EvidenceRole,
    EvidenceType,
    RelationType,
    RuleCategory,
    VerificationMethod,
)
from scrinium.infrastructure.sqlite.models import (
    EntityRelationship,
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

    # Rebuilding twice gives the same: the derived ones are replaced, not added up.
    assert rebuild_relations(runtime.database, uuid.UUID(api.project_id)) == 4
    assert rebuild_relations(runtime.database, uuid.UUID(api.project_id)) == 4
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
