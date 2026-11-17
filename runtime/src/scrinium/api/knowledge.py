"""Knowledge routes (Rulebook, graph, flows), hand-written rules, human review and
local profile.

Reviewing a version uses the local profile name: it is not an account, it only records
who reviewed on this machine. Without a name, the review is rejected.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session as DbSession

from scrinium.application.ai.service import LlmService
from scrinium.application.analysis.consistency import merge_rules
from scrinium.application.analysis.extraction import write_statement_now
from scrinium.application.analysis.rule_relations import rebuild_relations
from scrinium.application.ingestion import sources
from scrinium.application.knowledge import views
from scrinium.application.knowledge.architecture import architecture
from scrinium.application.knowledge.common import KnowledgeError, KnowledgeNotFoundError
from scrinium.application.knowledge.content import RuleContent
from scrinium.application.knowledge.corrections import correct_rule
from scrinium.application.knowledge.entities import (
    Provenance,
    create_business_rule,
    set_verification,
    title_slug,
)
from scrinium.application.knowledge.rulebook import export_status
from scrinium.application.retrieval import edges
from scrinium.application.workspaces import local_workspace, record_audit, reviewer_name
from scrinium.domain.enums import (
    AuditResult,
    Criticality,
    EntityLifecycle,
    RuleCategory,
    VerificationStatus,
    VersionOrigin,
)
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import KnowledgeEntityVersion, LocalProfile, Project

_HUMAN_REVIEW = frozenset({VerificationStatus.HUMAN_VERIFIED, VerificationStatus.REJECTED})


class ProfileBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    display_name: str = Field(min_length=1, max_length=100)


class ProfileResponse(BaseModel):
    display_name: str | None


class NewRuleBody(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=300)
    statement: str = Field(min_length=1, max_length=4000)
    rationale: str | None = Field(default=None, max_length=4000)
    category: RuleCategory = RuleCategory.OTHER
    criticality: Criticality = Criticality.MEDIUM


class CorrectionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=4000)
    title: str | None = Field(default=None, min_length=1, max_length=300)


class MergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duplicate_id: uuid.UUID


class VerificationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VerificationStatus


def _project(session: DbSession, project_id: uuid.UUID) -> Project:
    try:
        return sources.project_of(session, project_id)
    except sources.SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None


def build_knowledge_router(database: Database, llm: LlmService | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/v1/profile")
    def read_profile() -> ProfileResponse:
        with database.session() as session:
            return ProfileResponse(display_name=reviewer_name(session))

    @router.put("/api/v1/profile")
    def update_profile(body: ProfileBody, request: Request) -> ProfileResponse:
        with database.session() as session:
            profile = session.get_one(LocalProfile, 1)
            profile.display_name = body.display_name
            record_audit(
                session, local_workspace(session).id, "profile.update", "local_profile", "1",
                AuditResult.SUCCESS, getattr(request.state, "request_id", None),
            )  # fmt: skip
            return ProfileResponse(display_name=profile.display_name)

    @router.get("/api/v1/projects/{project_id}/rulebook")
    def rulebook(project_id: uuid.UUID) -> dict[str, Any]:
        """Per source, the reviewed rules not yet exported to its .scrinium/rules/ (ADR-012)."""
        with database.session() as session:
            project = _project(session, project_id)
            return {"sources": export_status(session, project)}

    @router.get("/api/v1/projects/{project_id}/rules")
    def rules(
        project_id: uuid.UUID,
        verification: VerificationStatus | None = None,
        lifecycle: EntityLifecycle | None = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
    ) -> dict[str, Any]:
        with database.session() as session:
            project = _project(session, project_id)
            return {"rules": views.list_rules(session, project, verification, lifecycle, q)}

    @router.post("/api/v1/projects/{project_id}/rules", status_code=status.HTTP_201_CREATED)
    def create_rule(project_id: uuid.UUID, body: NewRuleBody, request: Request) -> dict[str, Any]:
        """A rule written by a person: born active, with their name as author."""
        with database.session() as session:
            project = _project(session, project_id)
            author = reviewer_name(session)
            if author is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "profile_name_required")
            content = RuleContent(
                title=body.title, statement=body.statement, rationale=body.rationale or None
            )
            try:
                version = create_business_rule(
                    session, project, canonical_key=f"manual/{title_slug(body.title) or 'regla'}",
                    category=body.category, content=content, criticality=body.criticality,
                    provenance=Provenance(VersionOrigin.HUMAN, created_by=author),
                    request_id=getattr(request.state, "request_id", None),
                )  # fmt: skip
            except KnowledgeError as error:
                code = "rule_exists" if error.code == "entity_exists" else error.code
                conflict = code == "rule_exists"
                raise HTTPException(
                    status.HTTP_409_CONFLICT if conflict else status.HTTP_422_UNPROCESSABLE_CONTENT,
                    code,
                ) from None
            entity_version = session.get_one(
                KnowledgeEntityVersion, version.knowledge_entity_version_id
            )
            return views.rule_detail(session, project, entity_version.knowledge_entity_id)

    @router.post("/api/v1/projects/{project_id}/rules/{entity_id}/correction")
    def correct(
        project_id: uuid.UUID, entity_id: uuid.UUID, body: CorrectionBody, request: Request
    ) -> dict[str, Any]:
        """The rule is real but badly worded: the person's text becomes a new, confirmed version
        with the same citations."""
        with database.session() as session:
            project = _project(session, project_id)
            author = reviewer_name(session)
            if author is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "profile_name_required")
            try:
                views.rule_detail(session, project, entity_id)
                correct_rule(
                    session, project, entity_id, statement=body.statement, title=body.title,
                    author=author, request_id=getattr(request.state, "request_id", None),
                )  # fmt: skip
            except (KnowledgeNotFoundError, LookupError):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except KnowledgeError as error:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code) from None
            except ValidationError:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request"
                ) from None
            return views.rule_detail(session, project, entity_id)

    @router.post("/api/v1/projects/{project_id}/rules/{entity_id}/merge")
    def merge(
        project_id: uuid.UUID, entity_id: uuid.UUID, body: MergeBody, request: Request
    ) -> dict[str, Any]:
        """A person decides two rules are the same: this one is kept."""
        with database.session() as session:
            project = _project(session, project_id)
            reviewer = reviewer_name(session)
            if reviewer is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "profile_name_required")
            try:
                moved = merge_rules(
                    session, project, entity_id, body.duplicate_id, reviewer,
                    getattr(request.state, "request_id", None),
                )  # fmt: skip
            except KnowledgeNotFoundError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            except KnowledgeError as error:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code) from None
            return {"merged": str(body.duplicate_id), "evidence_moved": moved}

    @router.get("/api/v1/projects/{project_id}/rules/{entity_id}")
    def rule(project_id: uuid.UUID, entity_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            project = _project(session, project_id)
            try:
                return views.rule_detail(session, project, entity_id)
            except (KnowledgeNotFoundError, LookupError):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None

    @router.post("/api/v1/projects/{project_id}/rules/{entity_id}/statement")
    def write_statement(project_id: uuid.UUID, entity_id: uuid.UUID) -> dict[str, Any]:
        """The statement of a map rule, now (S3): it was opened before background extraction
        got to it. It goes ahead, like a question."""
        with database.session() as session:
            project = _project(session, project_id)
            try:
                views.rule_detail(session, project, entity_id)
            except (KnowledgeNotFoundError, LookupError):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
        if llm is None or llm.status() is not ModelStatus.READY:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "llm_unavailable")
        if llm.memory_too_low_for_questions():
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "low_memory")
        try:
            write_statement_now(database, llm, project_id, entity_id)
        except ModelError:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, "model_error") from None
        with database.session() as session:
            return views.rule_detail(session, _project(session, project_id), entity_id)

    @router.post("/api/v1/knowledge-versions/{version_id}/verification")
    def verify(version_id: uuid.UUID, body: VerificationBody, request: Request) -> dict[str, Any]:
        with database.session() as session:
            version = session.get(KnowledgeEntityVersion, version_id)
            if version is None or version.workspace_id != local_workspace(session).id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            project = session.get_one(Project, version.project_id)
            reviewer = reviewer_name(session)
            if body.status in _HUMAN_REVIEW and reviewer is None:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "profile_name_required")
            try:
                updated = set_verification(
                    session, project, version.id, body.status,
                    reviewer=reviewer if body.status in _HUMAN_REVIEW else None,
                    request_id=getattr(request.state, "request_id", None),
                )  # fmt: skip
            except KnowledgeError as error:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code) from None
            return {
                "id": str(updated.id),
                "verification_status": updated.verification_status.value,
                "reviewed_by": updated.reviewed_by,
            }

    @router.get("/api/v1/projects/{project_id}/architecture")
    def project_architecture(
        project_id: uuid.UUID, path: Annotated[str, Query(max_length=1000)] = ""
    ) -> dict[str, Any]:
        """One level of the code as modules with dependencies (stage 3, phase D)."""
        with database.session() as session:
            project = _project(session, project_id)
            try:
                return architecture(session, project.id, path)
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None

    @router.get("/api/v1/projects/{project_id}/symbols/{symbol_id}/neighbours")
    def symbol_neighbours(project_id: uuid.UUID, symbol_id: uuid.UUID) -> dict[str, Any]:
        """Who this function calls and who calls it (stage 3, phase B)."""
        with database.session() as session:
            project = _project(session, project_id)
            try:
                return edges.neighbours(session, project.id, symbol_id)
            except LookupError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None

    @router.get("/api/v1/projects/{project_id}/graph")
    def graph(project_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            return views.graph(session, _project(session, project_id))

    @router.post("/api/v1/projects/{project_id}/relations")
    def relate_rules(project_id: uuid.UUID) -> dict[str, Any]:
        """Phase C now, without extracting again: rules extracted before it have none. Derived
        from the code without the model, under a second; a person's relationships are kept."""
        with database.session() as session:
            _project(session, project_id)
        return {"relationships": rebuild_relations(database, project_id)}

    @router.get("/api/v1/projects/{project_id}/flows")
    def flows(project_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            return {"flows": views.list_flows(session, _project(session, project_id))}

    @router.get("/api/v1/projects/{project_id}/flows/{entity_id}")
    def flow(project_id: uuid.UUID, entity_id: uuid.UUID) -> dict[str, Any]:
        with database.session() as session:
            project = _project(session, project_id)
            try:
                return views.flow_detail(session, project, entity_id)
            except (KnowledgeNotFoundError, LookupError):
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None

    return router
