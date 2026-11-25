"""Analysis routes: rule extraction, diff, SQL, releases, bugs and finding review.

Analyses are requested here and run on another thread; the response is 202, then poll
GET /api/v1/analyses/{id}. Everything they return is a proposal with its verification status.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool

from scrinium.application.ai.providers import ProviderError, consent_for_task
from scrinium.application.analysis import (
    bug_investigation,
    code_diff,
    consistency,
    extraction,
    extraction_queue,
    release_comparison,
    sql_review,
)
from scrinium.application.analysis.candidates import DEFAULT_MIN_SCORE
from scrinium.application.analysis.runs import AnalysisRunner, Task, create_run
from scrinium.application.ingestion import sources
from scrinium.application.knowledge.common import (
    KnowledgeError,
    KnowledgeNotFoundError,
    get_in_project,
)
from scrinium.application.knowledge.deletion import forget_analysis_run
from scrinium.application.knowledge.evidence import assess_finding
from scrinium.application.knowledge.releases import latest_snapshot
from scrinium.application.retrieval.indexing import Indexer
from scrinium.application.workspaces import local_workspace, record_audit, reviewer_name
from scrinium.domain.enums import (
    AiTask,
    AnalysisStatus,
    AnalysisType,
    AssessmentStatus,
    AuditResult,
    FindingType,
    ReleaseStatus,
    Severity,
)
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import (
    AnalysisRun,
    Finding,
    FindingEvidence,
    Project,
    SoftwareRelease,
    SourceConnection,
    utc_now,
)

Dialect = Literal[
    "", "postgres", "mysql", "sqlite", "tsql", "oracle", "bigquery", "snowflake", "duckdb"
]


# Server-sent events (phase 9.4): how often the state is read, and a keep-alive comment.
_EVENT_SECONDS: Final = 1.0
_KEEPALIVE_SECONDS: Final = 15.0
_LIVE: Final = frozenset({AnalysisStatus.QUEUED.value, AnalysisStatus.RUNNING.value})


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExtractionRequest(_Body):
    path_prefix: str | None = Field(default=None, max_length=500)
    # Minimum score for the model to read a function; 0 = all of them (much slower).
    min_score: int = Field(default=DEFAULT_MIN_SCORE, ge=0, le=20)
    # "local" or "<provider id> <model>"; absent: the project's default for the rule map.
    model: str | None = Field(default=None, max_length=300)


class DiffRequest(_Body):
    diff: str = Field(min_length=1, max_length=code_diff.MAX_DIFF_CHARS)
    base_revision: str | None = Field(default=None, max_length=200)
    target_revision: str | None = Field(default=None, max_length=200)


class SqlRequest(_Body):
    sql: str = Field(min_length=1, max_length=sql_review.MAX_SQL_CHARS)
    dialect: Dialect = ""
    schema_ddl: str | None = Field(default=None, max_length=sql_review.MAX_SCHEMA_CHARS)


class ReleaseComparisonRequest(_Body):
    base_release_id: uuid.UUID
    target_release_id: uuid.UUID


class BugRequest(_Body):
    description: str = Field(min_length=1, max_length=bug_investigation.MAX_DESCRIPTION_CHARS)


class FeedbackRequest(_Body):
    status: AssessmentStatus
    reviewer: str | None = Field(default=None, max_length=200)


class FindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    finding_type: FindingType
    title: str
    description: str
    severity: Severity
    assessment_status: AssessmentStatus
    rule_version_id: uuid.UUID | None
    location: dict[str, Any] | None
    recommended_next_step: str | None
    reviewed_by: str | None
    reviewed_at: datetime | None
    created_at: datetime
    evidence: list[dict[str, str]] = Field(default_factory=list)


class AnalysisSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    analysis_type: AnalysisType
    question: str
    status: AnalysisStatus
    error_summary: str | None
    base_revision: str | None
    target_revision: str | None
    release_ref: str | None
    created_at: datetime
    finished_at: datetime | None


class AnalysisDetail(AnalysisSummary):
    model_name: str | None
    result: dict[str, Any]
    findings: list[FindingResponse] = Field(default_factory=list)


class AnalysisListResponse(BaseModel):
    analyses: list[AnalysisSummary]


class ReleaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_connection_id: uuid.UUID
    release_name: str
    commit_sha: str | None
    previous_release_id: uuid.UUID | None
    released_at: datetime | None
    status: ReleaseStatus
    snapshot_id: uuid.UUID | None = None
    snapshot_version: int | None = None


class ReleaseListResponse(BaseModel):
    releases: list[ReleaseResponse]


class DiscoveryResponse(BaseModel):
    created: list[uuid.UUID]
    existing: int


def _project(session: DbSession, project_id: uuid.UUID) -> Project:
    try:
        return sources.project_of(session, project_id)
    except sources.SourceNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def _knowledge_error(error: Exception) -> HTTPException:
    if isinstance(error, KnowledgeNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
    code = error.code if isinstance(error, KnowledgeError) else "invalid_request"
    conflict = code in ("release_not_indexed",)
    return HTTPException(
        status.HTTP_409_CONFLICT if conflict else status.HTTP_422_UNPROCESSABLE_CONTENT, code
    )


def _release(session: DbSession, release: SoftwareRelease) -> ReleaseResponse:
    response = ReleaseResponse.model_validate(release)
    snapshot = latest_snapshot(session, release.id)
    if snapshot is not None:
        response.snapshot_id, response.snapshot_version = snapshot.id, snapshot.snapshot_version
    return response


def build_analyses_router(
    database: Database, runner: AnalysisRunner, indexer: Indexer
) -> APIRouter:
    router = APIRouter()
    llm = runner.llm

    def _run(session: DbSession, run_id: uuid.UUID) -> AnalysisRun:
        run = session.get(AnalysisRun, run_id)
        if (
            run is None
            or run.workspace_id != local_workspace(session).id
            or run.analysis_type is AnalysisType.QUESTION
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        return run

    def _detail(session: DbSession, run: AnalysisRun) -> AnalysisDetail:
        detail = AnalysisDetail.model_validate(run)
        findings = session.scalars(
            select(Finding).where(Finding.analysis_run_id == run.id).order_by(Finding.created_at)
        )
        for finding in findings:
            item = FindingResponse.model_validate(finding)
            item.evidence = [
                {"evidence_id": str(link.evidence_record_id), "relevance": link.relevance.value}
                for link in session.scalars(
                    select(FindingEvidence).where(FindingEvidence.finding_id == finding.id)
                )
            ]
            detail.findings.append(item)
        return detail

    def _active_extraction(session: DbSession, project_id: uuid.UUID) -> uuid.UUID | None:
        """The project's running extraction or, if none, the one waiting for its turn."""
        return session.scalar(
            select(AnalysisRun.id)
            .where(
                AnalysisRun.project_id == project_id,
                AnalysisRun.analysis_type == AnalysisType.KNOWLEDGE_EXTRACTION,
                AnalysisRun.status.in_([AnalysisStatus.QUEUED, AnalysisStatus.RUNNING]),
            )
            .order_by(
                (AnalysisRun.status == AnalysisStatus.RUNNING).desc(),
                AnalysisRun.created_at.desc(),
            )
            .limit(1)
        )

    def _start(
        project_id: uuid.UUID,
        request: Request,
        analysis_type: AnalysisType,
        description: str,
        input_text: str,
        task: Task,
        *,
        one_extraction_at_a_time: bool = False,
        **kwargs: Any,
    ) -> AnalysisDetail:
        with database.session() as session:
            project = _project(session, project_id)
            # Two extractions of the same project would trample the queue, and pausing would stop
            # only one: the other (e.g. the one that resumes at startup) would keep working.
            if one_extraction_at_a_time and _active_extraction(session, project.id) is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "extraction_running")
            run = create_run(
                session, project, analysis_type, description, input_text,
                request_id=_request_id(request), **kwargs,
            )  # fmt: skip
            run_id = run.id
        runner.submit(task, run_id)
        with database.session() as session:
            return _detail(session, _run(session, run_id))

    accepted = status.HTTP_202_ACCEPTED

    @router.post("/api/v1/projects/{project_id}/extractions", status_code=accepted)
    def extract(project_id: uuid.UUID, body: ExtractionRequest, request: Request) -> AnalysisDetail:
        prefix = body.path_prefix or None
        with database.session() as session:
            try:
                project = _project(session, project_id)
                consent = consent_for_task(session, project, AiTask.RULE_MAP, body.model)
            except ProviderError as error:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code) from None
        # Rules are written by a model the person provides: without one, say so before starting.
        if consent is None and llm.status() is not ModelStatus.READY:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "model_needed")
        return _start(
            project_id, request, AnalysisType.KNOWLEDGE_EXTRACTION,
            f"Extraer reglas{f' de {prefix}' if prefix else ''}", prefix or "",
            extraction.extraction_task(database, llm, runner, prefix, body.min_score),
            one_extraction_at_a_time=True, llm=llm, prompt_version=extraction.PROMPT_VERSION,
            choice=body.model,
        )  # fmt: skip

    @router.post("/api/v1/projects/{project_id}/analyses/code-diff", status_code=accepted)
    def analyze_diff(project_id: uuid.UUID, body: DiffRequest, request: Request) -> AnalysisDetail:
        diff = body.diff
        return _start(
            project_id, request, AnalysisType.CODE_DIFF,
            f"Diff de {diff.count(chr(10)) + 1} lineas", diff,
            lambda run_id: code_diff.analyze_diff(database, llm, run_id, diff),
            llm=llm, prompt_version=code_diff.PROMPT_VERSION,
            base_revision=body.base_revision, target_revision=body.target_revision,
        )  # fmt: skip

    @router.post("/api/v1/projects/{project_id}/analyses/sql", status_code=accepted)
    def analyze_sql(project_id: uuid.UUID, body: SqlRequest, request: Request) -> AnalysisDetail:
        sql, dialect, schema = body.sql, body.dialect, body.schema_ddl
        return _start(
            project_id, request, AnalysisType.SQL_REVIEW, f"SQL ({dialect or 'genérico'})",
            sql, lambda run_id: sql_review.review_sql(database, run_id, sql, dialect, schema),
        )  # fmt: skip

    @router.post("/api/v1/projects/{project_id}/analyses/release-comparison", status_code=accepted)
    def compare(
        project_id: uuid.UUID, body: ReleaseComparisonRequest, request: Request
    ) -> AnalysisDetail:
        base_id, target_id = body.base_release_id, body.target_release_id
        if base_id == target_id:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "same_release")
        with database.session() as session:
            project = _project(session, project_id)
            try:
                base = get_in_project(session, SoftwareRelease, project, base_id)
                target = get_in_project(session, SoftwareRelease, project, target_id)
            except KnowledgeNotFoundError as error:
                raise _knowledge_error(error) from None
            names = base.release_name, target.release_name
        return _start(
            project_id, request, AnalysisType.RELEASE_COMPARISON,
            f"{names[0]} -> {names[1]}", f"{base_id}:{target_id}",
            lambda run_id: release_comparison.compare_releases(
                database, run_id, base_id, target_id
            ),
            base_revision=names[0], target_revision=names[1],
        )  # fmt: skip

    @router.post("/api/v1/projects/{project_id}/analyses/bug", status_code=accepted)
    def investigate(project_id: uuid.UUID, body: BugRequest, request: Request) -> AnalysisDetail:
        description = body.description
        return _start(
            project_id, request, AnalysisType.BUG_INVESTIGATION, description, description,
            lambda run_id: bug_investigation.investigate_bug(
                database, indexer, run_id, description
            ),
        )  # fmt: skip

    @router.get("/api/v1/projects/{project_id}/analyses")
    def history(
        project_id: uuid.UUID,
        analysis_type: Annotated[AnalysisType | None, Query(alias="type")] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> AnalysisListResponse:
        with database.session() as session:
            project = _project(session, project_id)
            query = select(AnalysisRun).where(
                AnalysisRun.project_id == project.id,
                AnalysisRun.analysis_type != AnalysisType.QUESTION,
            )
            if analysis_type is not None:
                query = query.where(AnalysisRun.analysis_type == analysis_type)
            runs = session.scalars(query.order_by(AnalysisRun.created_at.desc()).limit(limit))
            return AnalysisListResponse(
                analyses=[AnalysisSummary.model_validate(run) for run in runs]
            )

    @router.get("/api/v1/analyses/{analysis_id}")
    def read(analysis_id: uuid.UUID) -> AnalysisDetail:
        with database.session() as session:
            return _detail(session, _run(session, analysis_id))

    @router.get("/api/v1/analyses/{analysis_id}/events")
    async def events(analysis_id: uuid.UUID, request: Request) -> StreamingResponse:
        """Server-sent events (phase 9.4): the analysis each time it changes, until it ends.
        The console listens instead of asking every second; it still can, if this fails."""
        with database.session() as session:
            _run(session, analysis_id)  # 404 before the stream starts

        async def stream() -> AsyncIterator[str]:
            last = ""
            waited = 0.0
            while not await request.is_disconnected():
                detail = await run_in_threadpool(_read_detail, analysis_id)
                body = detail.model_dump_json()
                if body != last:
                    last = body
                    yield f"event: analysis\ndata: {body}\n\n"
                if detail.status not in _LIVE:
                    yield "event: end\ndata: {}\n\n"
                    return
                await asyncio.sleep(_EVENT_SECONDS)
                waited += _EVENT_SECONDS
                if waited >= _KEEPALIVE_SECONDS:
                    waited = 0.0
                    yield ": sigue\n\n"  # a comment keeps proxies and the browser listening

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    def _read_detail(analysis_id: uuid.UUID) -> AnalysisDetail:
        with database.session() as session:
            return _detail(session, _run(session, analysis_id))

    @router.post("/api/v1/projects/{project_id}/consistency", status_code=accepted)
    def consistency_review(project_id: uuid.UUID, request: Request) -> AnalysisDetail:
        """Duplicates, contradictions, ambiguous rules, and the tests that exercise each rule."""

        def task(run_id: uuid.UUID) -> None:
            consistency.review_consistency(
                database, llm, indexer, run_id, should_stop=lambda: runner.should_stop(run_id)
            )

        return _start(
            project_id, request, AnalysisType.KNOWLEDGE_EXTRACTION,
            "Revisar la consistencia de las reglas", "consistency", task,
            llm=llm, prompt_version=consistency.CONSISTENCY_VERSION,
        )  # fmt: skip

    @router.post("/api/v1/analyses/{analysis_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
    def cancel(analysis_id: uuid.UUID, request: Request) -> dict[str, str]:
        """Pauses an extraction at the next batch: what is left stays in the queue."""
        with database.session() as session:
            run = _run(session, analysis_id)
            if run.analysis_type is not AnalysisType.KNOWLEDGE_EXTRACTION:
                raise HTTPException(status.HTTP_409_CONFLICT, "not_cancellable")
            if run.status not in (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING):
                raise HTTPException(status.HTTP_409_CONFLICT, "analysis_finished")
            record_audit(
                session, run.workspace_id, "analysis.cancel", "analysis_run", str(run.id),
                AuditResult.SUCCESS, _request_id(request),
            )  # fmt: skip
        runner.cancel(analysis_id)
        return {"status": "stopping"}

    @router.get("/api/v1/projects/{project_id}/extraction-estimate")
    def extraction_estimate(
        project_id: uuid.UUID,
        path_prefix: str = "",
        min_score: int = DEFAULT_MIN_SCORE,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Before extracting: what would go to the project's remote model, and the budget left.
        Nothing is worked out when the rule map runs locally."""
        with database.session() as session:
            _project(session, project_id)
        router_ = llm.router
        try:
            route = (
                router_.route(project_id, AiTask.RULE_MAP, model) if router_ is not None else None
            )
        except ModelError:
            refused = "model_not_permitted"
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, refused) from None
        if router_ is None or route is None:
            return {"remote": False}
        prefix = path_prefix.strip()[:500] or None
        estimate = extraction.estimate_remote(
            database, project_id, prefix, max(0, min(min_score, 20)), route.keeps_local
        )
        return {
            "remote": True, "host": route.host, "model": route.model, **estimate,
            "budget": route.budget, "spent_this_month": router_.spent(project_id, utc_now()),
            "statements_remote": router_.route(project_id, AiTask.RULE_STATEMENTS) is not None,
        }  # fmt: skip

    @router.get("/api/v1/projects/{project_id}/extraction-status")
    def extraction_status(project_id: uuid.UUID) -> dict[str, Any]:
        """The project's extraction queue and the running extraction, if any."""
        with database.session() as session:
            project = _project(session, project_id)
            running = _active_extraction(session, project.id)
            return {
                **extraction_queue.counts(session, project.id),
                "running_analysis_id": str(running) if running else None,
                # Paused until the machine has free memory (resumes by itself).
                "waiting_for_memory": running is not None and runner.llm.memory.waiting,
            }

    @router.delete("/api/v1/analyses/{analysis_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete(analysis_id: uuid.UUID, request: Request) -> Response:
        with database.session() as session:
            run = _run(session, analysis_id)
            if run.status in (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING):
                raise HTTPException(status.HTTP_409_CONFLICT, "analysis_in_progress")
            record_audit(
                session, run.workspace_id, "analysis.delete", "analysis_run", str(run.id),
                AuditResult.SUCCESS, _request_id(request),
            )  # fmt: skip
            forget_analysis_run(session, run.id)
            session.delete(run)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/api/v1/findings/{finding_id}/feedback")
    def feedback(finding_id: uuid.UUID, body: FeedbackRequest, request: Request) -> FindingResponse:
        with database.session() as session:
            finding = session.get(Finding, finding_id)
            if finding is None or finding.workspace_id != local_workspace(session).id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            project = session.get_one(Project, finding.project_id)
            try:
                updated = assess_finding(
                    session, project, finding.id, body.status,
                    reviewer=body.reviewer or reviewer_name(session),
                    request_id=_request_id(request),
                )  # fmt: skip
            except (KnowledgeError, KnowledgeNotFoundError) as error:
                raise _knowledge_error(error) from None
            return FindingResponse.model_validate(updated)

    @router.get("/api/v1/projects/{project_id}/releases")
    def releases(project_id: uuid.UUID) -> ReleaseListResponse:
        with database.session() as session:
            project = _project(session, project_id)
            rows = session.scalars(
                select(SoftwareRelease)
                .where(SoftwareRelease.project_id == project.id)
                .order_by(SoftwareRelease.released_at, SoftwareRelease.created_at)
            )
            return ReleaseListResponse(releases=[_release(session, r) for r in rows])

    @router.post("/api/v1/sources/{source_id}/releases/discover")
    def discover(source_id: uuid.UUID, request: Request) -> DiscoveryResponse:
        with database.session() as session:
            workspace = local_workspace(session)
            found = session.get(SourceConnection, source_id)
            if found is None or found.workspace_id != workspace.id or found.project_id is None:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            project_id = found.project_id
        try:
            discovery = release_comparison.discover_releases(
                database, project_id, source_id, _request_id(request)
            )
        except (KnowledgeError, KnowledgeNotFoundError) as error:
            raise _knowledge_error(error) from None
        return DiscoveryResponse(created=discovery.created, existing=discovery.existing)

    @router.post("/api/v1/releases/{release_id}/snapshots", status_code=status.HTTP_201_CREATED)
    def snapshot(release_id: uuid.UUID, request: Request) -> ReleaseResponse:
        with database.session() as session:
            release = session.get(SoftwareRelease, release_id)
            if release is None or release.workspace_id != local_workspace(session).id:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
            project = session.get_one(Project, release.project_id)
            try:
                release_comparison.snapshot_release(
                    session, project, release.id, extraction.EXTRACTOR_VERSION,
                    _request_id(request),
                )  # fmt: skip
            except (KnowledgeError, KnowledgeNotFoundError) as error:
                raise _knowledge_error(error) from None
            return _release(session, release)

    return router
