"""Knowledge model: immutable versions, real evidence, valid relationships, snapshots
per release, and deletion that propagates. Against real SQLite and real Git sources."""

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from scrinium.application.knowledge.common import KnowledgeError, KnowledgeNotFoundError
from scrinium.application.knowledge.content import FlowContent, RuleContent
from scrinium.application.knowledge.entities import (
    HUMAN,
    Provenance,
    add_rule_version,
    create_business_rule,
    create_entity,
    create_flow,
    list_versions,
    set_verification,
)
from scrinium.application.knowledge.evidence import (
    add_relationship,
    assess_finding,
    cite_source,
    evidence_profile,
    link_finding_evidence,
    record_finding,
    record_human_review,
)
from scrinium.application.knowledge.releases import capture_snapshot, knowledge_at, register_release
from scrinium.domain.enums import (
    AnalysisType,
    AssessmentStatus,
    EntityLifecycle,
    EntityType,
    EvidenceRelevance,
    EvidenceRole,
    EvidenceType,
    FindingType,
    FlowType,
    RelationType,
    RuleCategory,
    Severity,
    VerificationMethod,
    VerificationStatus,
    VersionOrigin,
)
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    AuditEvent,
    BusinessRuleVersion,
    EntityRelationship,
    EvidenceRecord,
    Finding,
    FlowStep,
    FlowTransition,
    KnowledgeEntity,
    KnowledgeEntityVersion,
    Project,
    SoftwareRelease,
    SourceConnection,
    SourceRevision,
)
from tests.conftest import ORIGIN, Runtime
from tests.integration.test_ingestion import commit_all, git, write

EXTRACTOR = Provenance(
    VersionOrigin.SOURCE_EXTRACTION, "ollama", "qwen3:4b-instruct", extractor_version="rules-1"
)
GH_TOKEN = "ghp_" + "Z" * 36


def _rule(statement: str = "El total es la suma de los importes de las lineas.") -> RuleContent:
    return RuleContent.model_validate(
        {
            "title": "Total de factura",
            "statement": statement,
            "scope": {"modules": ["billing"]},
            "expected_behavior": [{"description": "total == sum(amount)"}],
        }
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(
        root,
        "src/billing.py",
        "def calculate_invoice_total(lines):\n    return sum(l.amount for l in lines)\n",
    )
    write(
        root,
        "tests/test_billing.py",
        "def test_total():\n    assert calculate_invoice_total([]) == 0\n",
    )
    write(root, "docs/reglas.md", "# Reglas\nEl total suma los importes.\n")
    commit_all(root)
    return root


@pytest.fixture
def ctx(runtime: Runtime, repo: Path) -> "Context":
    csrf = runtime.login()
    headers = {"Origin": ORIGIN, "X-CSRF-Token": csrf}
    ids = []
    for name in ("App", "Otro"):
        project = runtime.client.post("/api/v1/projects", json={"name": name}, headers=headers)
        ids.append(uuid.UUID(project.json()["id"]))
    source = runtime.client.post(
        f"/api/v1/projects/{ids[0]}/sources",
        json={"kind": "git_repository", "path": str(repo)},
        headers=headers,
    )
    assert source.status_code == 201, source.text
    return Context(runtime, headers, ids[0], ids[1], uuid.UUID(source.json()["source"]["id"]), repo)


class Context:
    def __init__(
        self,
        runtime: Runtime,
        headers: dict[str, str],
        project_id: uuid.UUID,
        other_id: uuid.UUID,
        source_id: uuid.UUID,
        repo: Path,
    ) -> None:
        self.runtime = runtime
        self.headers = headers
        self.project_id = project_id
        self.other_id = other_id
        self.source_id = source_id
        self.repo = repo

    @contextmanager
    def session(self) -> Iterator[tuple[Session, Project]]:
        with self.runtime.database.session() as session:
            project = session.get(Project, self.project_id)
            assert project is not None
            yield session, project

    def engine(self) -> Any:
        return self.runtime.database.engine.begin()

    def other(self, session: Session) -> Project:
        project = session.get(Project, self.other_id)
        assert project is not None
        return project


def _revision_id(session: Session, path: str) -> uuid.UUID:
    revision = session.scalars(
        select(SourceRevision).where(SourceRevision.metadata_["path"].as_string() == path)
    ).one()
    return revision.id


def _cite(
    session: Session,
    project: Project,
    version: BusinessRuleVersion,
    method: VerificationMethod,
    path: str = "src/billing.py",
    **extra: Any,
) -> EvidenceRecord:
    record, _ = cite_source(
        session, project,
        entity_id=_entity_of(session, version), version_id=version.knowledge_entity_version_id,
        revision_id=_revision_id(session, path), start_line=1, end_line=2,
        role=extra.pop("role", EvidenceRole.IMPLEMENTS),
        evidence_type=extra.pop("evidence_type", EvidenceType.CODE), method=method, **extra,
    )  # fmt: skip
    return record


def _entity_of(session: Session, version: BusinessRuleVersion) -> uuid.UUID:
    row = session.get(KnowledgeEntityVersion, version.knowledge_entity_version_id)
    assert row is not None
    return row.knowledge_entity_id


def _version(session: Session, version: BusinessRuleVersion) -> KnowledgeEntityVersion:
    row = session.get(KnowledgeEntityVersion, version.knowledge_entity_version_id)
    assert row is not None
    return row


def _new_rule(
    session: Session, project: Project, key: str = "billing.total", **kwargs: Any
) -> BusinessRuleVersion:
    return create_business_rule(
        session, project, canonical_key=key, category=RuleCategory.CALCULATION,
        content=kwargs.pop("content", _rule()), provenance=kwargs.pop("provenance", EXTRACTOR),
        **kwargs,
    )  # fmt: skip


def test_rule_versions_are_immutable_and_repeated_content_is_not_a_new_version(
    ctx: Context,
) -> None:
    with ctx.session() as (session, project):
        first = _new_rule(session, project)
        entity_id = _entity_of(session, first)
        again, created = add_rule_version(session, project, entity_id, _rule(), EXTRACTOR)
        assert (
            not created and again.knowledge_entity_version_id == first.knowledge_entity_version_id
        )
        second, created = add_rule_version(
            session, project, entity_id, _rule("El total incluye impuestos."), EXTRACTOR
        )
        assert created
        assert second.supersedes_version_id == first.knowledge_entity_version_id
        versions = list_versions(session, entity_id)
        assert [v.version_number for v in versions] == [1, 2]
        # What the LLM proposes is born unverified, and the rule as a proposal.
        assert {v.verification_status for v in versions} == {VerificationStatus.UNVERIFIED}
        entity = session.get(KnowledgeEntity, entity_id)
        assert entity is not None and entity.lifecycle_status is EntityLifecycle.PROPOSED
        assert entity.summary == "El total incluye impuestos."
        assert versions[0].model_name == "qwen3:4b-instruct"
        assert versions[0].extractor_version == "rules-1"

    for statement in (
        update(BusinessRuleVersion).values(statement="cambiado"),
        update(KnowledgeEntityVersion).values(payload={"x": 1}),
    ):
        with pytest.raises(IntegrityError, match="solo anadir"), ctx.session() as (session, _):
            session.execute(statement)
    # The human review can change.
    with ctx.session() as (session, _):
        session.execute(update(KnowledgeEntityVersion).values(reviewed_by="ana"))


def test_generated_content_needs_extractor_version() -> None:
    with pytest.raises(KnowledgeError, match="extractor_version_required"):
        Provenance(VersionOrigin.SOURCE_EXTRACTION, "ollama", "m")


def test_llm_evidence_cannot_confirm_a_rule(ctx: Context) -> None:
    with ctx.session() as (session, project):
        version = _new_rule(session, project)
        version_id = version.knowledge_entity_version_id
        proposal = _cite(session, project, version, VerificationMethod.LLM_PROPOSAL)
        # The lines exist, but only the LLM says they back the rule.
        assert proposal.verification_status is VerificationStatus.UNVERIFIED
        with pytest.raises(KnowledgeError, match="insufficient_evidence"):
            set_verification(session, project, version_id, VerificationStatus.SOURCE_SUPPORTED)
        with pytest.raises(KnowledgeError, match="insufficient_evidence"):
            set_verification(session, project, version_id, VerificationStatus.TEST_SUPPORTED)
        with pytest.raises(KnowledgeError, match="reviewer_required"):
            set_verification(session, project, version_id, VerificationStatus.HUMAN_VERIFIED)

        _cite(session, project, version, VerificationMethod.DIRECT_REFERENCE)
        set_verification(session, project, version_id, VerificationStatus.SOURCE_SUPPORTED)
        _cite(
            session, project, version, VerificationMethod.TEST_RESULT,
            path="tests/test_billing.py", role=EvidenceRole.TESTS, evidence_type=EvidenceType.TEST,
        )  # fmt: skip
        set_verification(session, project, version_id, VerificationStatus.TEST_SUPPORTED)
        entity = session.get(KnowledgeEntity, _entity_of(session, version))
        assert entity is not None and entity.lifecycle_status is EntityLifecycle.PROPOSED

        set_verification(
            session, project, version_id, VerificationStatus.HUMAN_VERIFIED, reviewer="ana"
        )
        reviewed = session.get(KnowledgeEntity, entity.id)
        assert reviewed is not None and reviewed.lifecycle_status is EntityLifecycle.ACTIVE
        assert _version(session, version).reviewed_by == "ana"
        actions = session.scalars(
            select(AuditEvent.metadata_).where(AuditEvent.action == "knowledge_version.verify")
        ).all()
        assert [a["to"] for a in actions] == [
            "source_supported",
            "test_supported",
            "human_verified",
        ]
        # The audit log does not copy the rule's content.
        assert "suma" not in json.dumps(actions)


def test_test_result_must_be_a_test(ctx: Context) -> None:
    with ctx.session() as (session, project):
        version = _new_rule(session, project)
        with pytest.raises(KnowledgeError, match="test_result_needs_test"):
            _cite(session, project, version, VerificationMethod.TEST_RESULT)


def test_evidence_points_to_real_lines_of_this_project(ctx: Context) -> None:
    with ctx.session() as (session, project):
        version = _new_rule(session, project)
        record = _cite(session, project, version, VerificationMethod.DIRECT_REFERENCE)
        assert record.excerpt == (
            "def calculate_invoice_total(lines):\n    return sum(l.amount for l in lines)"
        )
        assert record.locator["path"] == "src/billing.py"
        assert record.locator["commit"] == git(ctx.repo, "rev-parse", "HEAD")
        assert len(record.excerpt_hash or "") == 64
        # Repeating the same citation does not duplicate it.
        again = _cite(session, project, version, VerificationMethod.DIRECT_REFERENCE)
        assert again.id == record.id

        entity_id = _entity_of(session, version)
        revision_id = _revision_id(session, "src/billing.py")
        base: dict[str, Any] = {
            "entity_id": entity_id, "revision_id": revision_id, "role": EvidenceRole.SUPPORTS,
            "evidence_type": EvidenceType.CODE, "method": VerificationMethod.LLM_PROPOSAL,
        }  # fmt: skip
        for start, end in ((0, 1), (2, 1), (1, 3), (5, 9)):
            with pytest.raises(KnowledgeError, match="lines_out_of_range"):
                cite_source(session, project, start_line=start, end_line=end, **base)
        # The same revision, cited from another project, does not exist for it.
        other = ctx.other(session)
        other_entity = create_entity(
            session, other, entity_type=EntityType.SERVICE, canonical_key="pagos", name="Pagos"
        )
        with pytest.raises(KnowledgeNotFoundError):
            cite_source(
                session, other, start_line=1, end_line=1, **{**base, "entity_id": other_entity.id}
            )


def test_human_review_evidence_needs_a_reviewer_and_redacts_secrets(ctx: Context) -> None:
    with ctx.session() as (session, project):
        version = _new_rule(session, project)
        entity_id = _entity_of(session, version)
        with pytest.raises(KnowledgeError, match="reviewer_required"):
            record_human_review(
                session, project, entity_id=entity_id, reviewer=" ", note="ok",
                role=EvidenceRole.SUPPORTS,
            )  # fmt: skip
        record = record_human_review(
            session, project, entity_id=entity_id, reviewer="ana",
            note=f"Confirmado con finanzas; token {GH_TOKEN}", role=EvidenceRole.SUPPORTS,
        )  # fmt: skip
        assert record.source_revision_id is None
        assert GH_TOKEN not in (record.excerpt or "")


def test_database_blocks_references_across_projects(ctx: Context) -> None:
    with ctx.session() as (session, project):
        mine = create_entity(
            session, project, entity_type=EntityType.SERVICE, canonical_key="a", name="A"
        )
        theirs = create_entity(
            session, ctx.other(session), entity_type=EntityType.SERVICE, canonical_key="b",
            name="B",
        )  # fmt: skip
        ids = mine.id, theirs.id
        with pytest.raises(KnowledgeNotFoundError):
            add_relationship(session, project, mine.id, RelationType.CALLS, theirs.id)
    # Even if someone bypasses the service, the composite foreign key prevents it.
    with pytest.raises(IntegrityError), ctx.session() as (session, project):
        session.add(
            EntityRelationship(
                workspace_id=project.workspace_id, project_id=project.id,
                from_entity_id=ids[0], relation_type=RelationType.CALLS, to_entity_id=ids[1],
            )
        )  # fmt: skip
        session.flush()


def test_relationships_are_typed_deduplicated_and_acyclic(ctx: Context) -> None:
    with ctx.session() as (session, project):

        def entity(key: str, kind: EntityType = EntityType.SERVICE) -> uuid.UUID:
            return create_entity(session, project, entity_type=kind, canonical_key=key, name=key).id

        a, b, c = entity("a"), entity("b"), entity("c")
        test = entity("t", EntityType.TEST_CASE)
        with pytest.raises(KnowledgeError, match="self_relation"):
            add_relationship(session, project, a, RelationType.DEPENDS_ON, a)
        with pytest.raises(KnowledgeError, match="relation_not_allowed"):
            add_relationship(session, project, a, RelationType.TESTED_BY, b)
        add_relationship(session, project, a, RelationType.TESTED_BY, test)
        with pytest.raises(KnowledgeError, match="relation_not_allowed"):
            add_relationship(session, project, a, RelationType.SUPERSEDES, test)

        add_relationship(session, project, a, RelationType.DEPENDS_ON, b)
        add_relationship(session, project, b, RelationType.DEPENDS_ON, c)
        with pytest.raises(KnowledgeError, match="relation_cycle"):
            add_relationship(session, project, c, RelationType.DEPENDS_ON, a)
        # Calls may be circular; another relationship type does not count for the cycle.
        add_relationship(session, project, c, RelationType.CALLS, a)

        first, created = add_relationship(session, project, a, RelationType.CONTRADICTS, b)
        same, again = add_relationship(session, project, b, RelationType.CONTRADICTS, a)
        assert created and not again and same.id == first.id

        with pytest.raises(KnowledgeError, match="invalid_validity"):
            moment = datetime(2026, 1, 1, tzinfo=UTC)
            add_relationship(
                session, project, a, RelationType.READS, c, valid_from=moment,
                valid_until=moment - timedelta(days=1),
            )  # fmt: skip


def test_flow_is_stored_whole_with_ordered_steps(ctx: Context) -> None:
    content: dict[str, Any] = {
        "description": "Cobro de una factura",
        "steps": [
            {"key": "inicio", "step_type": "start", "label": "Factura emitida"},
            {"key": "cobro", "step_type": "external_call", "label": "Cobrar con la pasarela"},
            {"key": "fin", "step_type": "end", "label": "Factura pagada"},
        ],
        "transitions": [
            {"from_key": "inicio", "to_key": "cobro", "transition_type": "next"},
            {"from_key": "cobro", "to_key": "fin", "transition_type": "conditional",
             "condition": "la pasarela confirma el pago"},
            {"from_key": "cobro", "to_key": "cobro", "transition_type": "retry"},
        ],
    }  # fmt: skip
    for broken, error in (
        ({**content, "transitions": [{"from_key": "inicio", "to_key": "nada",
                                      "transition_type": "next"}]}, "unknown_step"),
        ({**content, "transitions": [{"from_key": "cobro", "to_key": "fin",
                                      "transition_type": "conditional"}]}, "condition_required"),
        ({**content, "steps": content["steps"] * 2}, "duplicate_step_key"),
        ({**content, "steps": []}, "too_short"),
        ({**content, "owner": "x"}, "extra_forbidden"),
    ):  # fmt: skip
        with pytest.raises(ValidationError, match=error):
            FlowContent.model_validate(broken)

    with ctx.session() as (session, project):
        version = create_flow(
            session, project, entity_type=EntityType.BUSINESS_FLOW, canonical_key="cobro",
            name="Cobro", flow_type=FlowType.BUSINESS,
            content=FlowContent.model_validate(content), provenance=EXTRACTOR,
        )  # fmt: skip
        version_id = version.knowledge_entity_version_id
        steps = session.scalars(
            select(FlowStep)
            .where(FlowStep.flow_version_id == version_id)
            .order_by(FlowStep.position)
        ).all()
        assert [s.step_key for s in steps] == ["inicio", "cobro", "fin"]
        transitions = session.scalars(
            select(FlowTransition).where(FlowTransition.flow_version_id == version_id)
        ).all()
        assert len(transitions) == 3
        flow = session.scalars(
            select(KnowledgeEntity).where(KnowledgeEntity.canonical_key == "cobro")
        ).one()
        # A generated flow is a proposal: the plan says so and the UI will show it.
        assert flow.lifecycle_status is EntityLifecycle.PROPOSED
        with pytest.raises(KnowledgeError, match="not_a_flow"):
            create_flow(
                session, project, entity_type=EntityType.SERVICE, canonical_key="x", name="x",
                flow_type=FlowType.BUSINESS, content=FlowContent.model_validate(content),
            )  # fmt: skip
    with pytest.raises(IntegrityError, match="solo anadir"), ctx.session() as (session, _):
        session.execute(update(FlowStep).values(label="otro"))


def test_rule_content_is_closed_and_bounded() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        RuleContent.model_validate({"title": "t", "statement": "s", "sql": "DROP TABLE x"})
    with pytest.raises(ValidationError, match="too_long"):
        RuleContent.model_validate(
            {"title": "t", "statement": "s", "preconditions": [{"description": "x"}] * 21}
        )
    with pytest.raises(ValidationError):
        RuleContent.model_validate({"title": " ", "statement": "s"})
    rule = RuleContent.model_validate({"title": "t", "statement": f"usa {GH_TOKEN}"})
    assert GH_TOKEN not in rule.statement


def test_snapshot_fixes_what_was_known_at_each_release(ctx: Context) -> None:
    sha = git(ctx.repo, "rev-parse", "HEAD")
    with ctx.session() as (session, project):
        first = _new_rule(session, project, provenance=HUMAN)
        entity_id = _entity_of(session, first)
        rejected = _new_rule(session, project, key="billing.rejected")
        set_verification(
            session, project, rejected.knowledge_entity_version_id, VerificationStatus.REJECTED,
            reviewer="ana",
        )  # fmt: skip
        service = create_entity(
            session, project, entity_type=EntityType.SERVICE, canonical_key="billing", name="B"
        )
        add_relationship(session, project, entity_id, RelationType.IMPLEMENTED_BY, service.id)

        for bad in ("abc", sha.upper()):
            with pytest.raises(KnowledgeError, match="invalid_commit_sha"):
                register_release(
                    session, project, source_id=ctx.source_id, release_name="v0", commit_sha=bad
                )
        v1 = register_release(
            session, project, source_id=ctx.source_id, release_name="v1.0", commit_sha=sha
        )
        with pytest.raises(KnowledgeError, match="release_exists"):
            register_release(session, project, source_id=ctx.source_id, release_name="v1.0")
        with pytest.raises(KnowledgeNotFoundError):
            register_release(
                session, ctx.other(session), source_id=ctx.source_id, release_name="v9"
            )
        snapshot, created = capture_snapshot(session, project, v1.id, extractor_version="rules-1")
        assert created and snapshot.snapshot_version == 1
        same, created = capture_snapshot(session, project, v1.id, extractor_version="rules-1")
        assert not created and same.id == snapshot.id

        known = knowledge_at(session, project, snapshot.id)
        # The rejected rule did not apply; the service has no version, so it is not included.
        assert [(e.canonical_key, v.version_number) for e, v in known] == [("billing.total", 1)]
        assert snapshot.relationship_snapshot == [
            [str(entity_id), "implemented_by", str(service.id)]
        ]

        add_rule_version(session, project, entity_id, _rule("Con impuestos."), HUMAN)
        v2 = register_release(
            session,
            project,
            source_id=ctx.source_id,
            release_name="v2.0",
            previous_release_id=v1.id,
        )
        later, _ = capture_snapshot(session, project, v2.id, extractor_version="rules-1")
        assert [v.version_number for _, v in knowledge_at(session, project, later.id)] == [2]
        # The previous release still says what was known then.
        assert [v.version_number for _, v in knowledge_at(session, project, snapshot.id)] == [1]
        snapshot_id = snapshot.id
    with pytest.raises(IntegrityError, match="solo anadir"), ctx.engine() as connection:
        connection.execute(
            text("UPDATE release_knowledge_snapshots SET snapshot_hash = :h WHERE id = :i"),
            {"h": "0" * 64, "i": snapshot_id.hex},
        )


def test_deleting_a_source_removes_its_evidence_and_what_it_supported(ctx: Context) -> None:
    with ctx.session() as (session, project):
        # Written by a person: kept. Those the LLM extracted are deleted (test_privacy).
        version = _new_rule(session, project, provenance=HUMAN)
        version_id = version.knowledge_entity_version_id
        entity_id = _entity_of(session, version)
        record = _cite(session, project, version, VerificationMethod.DIRECT_REFERENCE)
        set_verification(session, project, version_id, VerificationStatus.SOURCE_SUPPORTED)
        test = create_entity(
            session, project, entity_type=EntityType.TEST_CASE, canonical_key="t", name="t"
        )
        relation, _ = add_relationship(
            session, project, entity_id, RelationType.TESTED_BY, test.id, evidence_id=record.id
        )
        run = _analysis(session, project)
        finding = record_finding(
            session, run, finding_type=FindingType.MISSING_TEST, title="Sin test de impuestos",
            description="Ningun test cubre el IVA.", severity=Severity.MEDIUM,
        )  # fmt: skip
        link_finding_evidence(session, project, finding.id, record.id, EvidenceRelevance.SUPPORTS)
        assess_finding(session, project, finding.id, AssessmentStatus.EVIDENCE_SUPPORTED)
        register_release(session, project, source_id=ctx.source_id, release_name="v1")
        ids = relation.id, finding.id

    response = ctx.runtime.client.delete(f"/api/v1/sources/{ctx.source_id}", headers=ctx.headers)
    assert response.status_code == 204, response.text

    with ctx.session() as (session, _):
        assert session.scalar(select(func.count()).select_from(EvidenceRecord)) == 0
        assert session.scalar(select(func.count()).select_from(SoftwareRelease)) == 0
        # The rule is kept, but no longer shows as backed by a source.
        kept = session.get(KnowledgeEntityVersion, version_id)
        assert kept is not None and kept.verification_status is VerificationStatus.UNVERIFIED
        entity = session.get(KnowledgeEntity, kept.knowledge_entity_id)
        assert entity is not None and entity.review_reason == "source_deleted"
        kept_relation = session.get(EntityRelationship, ids[0])
        assert kept_relation is not None and kept_relation.evidence_record_id is None
        kept_finding = session.get(Finding, ids[1])
        assert kept_finding is not None
        assert kept_finding.assessment_status is AssessmentStatus.HYPOTHESIS
        audit = session.scalars(
            select(AuditEvent.metadata_).where(AuditEvent.action == "source.delete")
        ).one()
        assert audit["evidence_deleted"] == 1
        assert audit["versions_unverified"] == 1
        assert audit["findings_reopened"] == 1
        assert audit["releases_deleted"] == 1
        assert session.get(SourceConnection, ctx.source_id) is None


def _analysis(session: Session, project: Project) -> AnalysisRun:
    run = AnalysisRun(
        workspace_id=project.workspace_id, project_id=project.id,
        analysis_type=AnalysisType.QUESTION, question="q", input_hash="0" * 64,
        prompt_version="2", retrieval_version="1",
    )  # fmt: skip
    session.add(run)
    session.flush()
    return run


def test_findings_need_real_evidence_and_keep_the_contrary(ctx: Context) -> None:
    with ctx.session() as (session, project):
        version = _new_rule(session, project)
        run = _analysis(session, project)
        finding = record_finding(
            session, run, finding_type=FindingType.RULE_VIOLATION, title="Viola la regla",
            description="d", severity=Severity.HIGH,
            rule_version_id=version.knowledge_entity_version_id,
        )  # fmt: skip
        assert finding.assessment_status is AssessmentStatus.HYPOTHESIS
        with pytest.raises(KnowledgeError, match="insufficient_evidence"):
            assess_finding(session, project, finding.id, AssessmentStatus.EVIDENCE_SUPPORTED)
        proposal = _cite(session, project, version, VerificationMethod.LLM_PROPOSAL)
        link_finding_evidence(session, project, finding.id, proposal.id, EvidenceRelevance.SUPPORTS)
        with pytest.raises(KnowledgeError, match="insufficient_evidence"):
            assess_finding(session, project, finding.id, AssessmentStatus.EVIDENCE_SUPPORTED)
        contrary = _cite(
            session, project, version, VerificationMethod.DIRECT_REFERENCE,
            path="docs/reglas.md", role=EvidenceRole.CONTRADICTS,
            evidence_type=EvidenceType.DOCUMENT,
        )  # fmt: skip
        link_finding_evidence(
            session, project, finding.id, contrary.id, EvidenceRelevance.CONTRADICTS
        )
        with pytest.raises(KnowledgeError, match="reviewer_required"):
            assess_finding(session, project, finding.id, AssessmentStatus.FALSE_POSITIVE)
        assess_finding(
            session, project, finding.id, AssessmentStatus.FALSE_POSITIVE, reviewer="ana"
        )
        assert finding.reviewed_by == "ana"
        assert session.get(EvidenceRecord, contrary.id) is not None


def test_evidence_profile_keeps_requested_implemented_and_tested_apart(ctx: Context) -> None:
    with ctx.session() as (session, project):
        version = _new_rule(session, project)
        _cite(session, project, version, VerificationMethod.DIRECT_REFERENCE)
        _cite(
            session, project, version, VerificationMethod.DIRECT_REFERENCE, path="docs/reglas.md",
            role=EvidenceRole.SUPPORTS, evidence_type=EvidenceType.DOCUMENT,
        )  # fmt: skip
        profile = evidence_profile(session, version.knowledge_entity_version_id)
        assert profile == {
            "requested": 0, "documented": 1, "implemented": 1, "tested": 0, "contradicted": 0,
        }  # fmt: skip


def test_unsupported_claims_become_findings_and_go_with_the_question(ctx: Context) -> None:
    ctx.runtime.chat.reply = json.dumps(
        {
            "answer_status": "answered",
            "summary": "El total suma los importes.",
            "claims": [
                {"text": "calculate_invoice_total suma l.amount", "sources": [1]},
                {"text": f"Se aplica IVA del 21 % {GH_TOKEN}", "sources": []},
            ],
            "limitations": [],
        }
    )
    response = ctx.runtime.client.post(
        f"/api/v1/projects/{ctx.project_id}/questions",
        json={"question": "¿cómo se calcula calculate_invoice_total?"},
        headers=ctx.headers,
    )
    assert response.status_code == 202, response.text
    run_id = uuid.UUID(response.json()["id"])
    with ctx.session() as (session, project):
        findings = session.scalars(select(Finding).where(Finding.analysis_run_id == run_id)).all()
        assert [f.finding_type for f in findings] == [FindingType.UNSUPPORTED_CLAIM]
        assert findings[0].assessment_status is AssessmentStatus.HYPOTHESIS
        assert findings[0].description.startswith("Se aplica IVA del 21 %")
        assert GH_TOKEN not in findings[0].description
        # A rule proposed from that question survives its deletion.
        rule = _new_rule(session, project, provenance=Provenance(
            VersionOrigin.DERIVED_ANALYSIS, extractor_version="x", analysis_run_id=run_id
        ))  # fmt: skip
        rule_id = rule.knowledge_entity_version_id

    deleted = ctx.runtime.client.delete(f"/api/v1/questions/{run_id}", headers=ctx.headers)
    assert deleted.status_code == 204, deleted.text
    with ctx.session() as (session, _):
        assert session.scalar(select(func.count()).select_from(Finding)) == 0
        kept = session.get(BusinessRuleVersion, rule_id)
        assert kept is not None and kept.created_from_analysis_id is None
