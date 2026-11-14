"""Question routes: ask, read the history and delete it."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from scrinium.application.ai import answering
from scrinium.application.ai.providers import ProviderError
from scrinium.application.ingestion import sources
from scrinium.application.knowledge.deletion import forget_analysis_run
from scrinium.application.workspaces import local_workspace, record_audit
from scrinium.domain.enums import AnalysisStatus, AnalysisType, AnswerStatus, AuditResult
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import AnalysisRun


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=answering.MAX_QUESTION_CHARS)
    # "local" or "<provider id> <model>"; absent: the project's default model for questions.
    model: str | None = Field(default=None, max_length=300)


class QuestionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    project_id: uuid.UUID
    question: str
    status: AnalysisStatus
    answer_status: AnswerStatus | None
    error_summary: str | None
    created_at: datetime
    finished_at: datetime | None


class QuestionDetail(QuestionSummary):
    model_name: str | None
    result: dict[str, Any]
    label: Literal["llm_proposal"] = "llm_proposal"


class QuestionListResponse(BaseModel):
    questions: list[QuestionSummary]


def build_questions_router(database: Database, runner: answering.QuestionRunner) -> APIRouter:
    router = APIRouter()

    def _run(session: DbSession, question_id: uuid.UUID) -> AnalysisRun:
        run = session.get(AnalysisRun, question_id)
        if (
            run is None
            or run.workspace_id != local_workspace(session).id
            or run.analysis_type is not AnalysisType.QUESTION
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found")
        return run

    @router.post("/api/v1/projects/{project_id}/questions", status_code=status.HTTP_202_ACCEPTED)
    def ask(project_id: uuid.UUID, body: QuestionRequest, request: Request) -> QuestionDetail:
        request_id: str | None = getattr(request.state, "request_id", None)
        with database.session() as session:
            try:
                project = sources.project_of(session, project_id)
            except sources.SourceNotFoundError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            try:
                run = answering.create_question(
                    session, project, body.question, runner.llm, body.model
                )
            except ProviderError as error:
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, error.code) from None
            # Without the question text: the audit log only records that it happened.
            record_audit(
                session, project.workspace_id, "question.ask", "analysis_run", str(run.id),
                AuditResult.SUCCESS, request_id,
            )  # fmt: skip
            run_id = run.id
        runner.submit(run_id, body.question)
        with database.session() as session:
            return QuestionDetail.model_validate(_run(session, run_id))

    @router.get("/api/v1/projects/{project_id}/questions")
    def history(
        project_id: uuid.UUID, limit: Annotated[int, Query(ge=1, le=200)] = 50
    ) -> QuestionListResponse:
        with database.session() as session:
            try:
                project = sources.project_of(session, project_id)
            except sources.SourceNotFoundError:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "not_found") from None
            runs = session.scalars(
                select(AnalysisRun)
                .where(
                    AnalysisRun.project_id == project.id,
                    AnalysisRun.analysis_type == AnalysisType.QUESTION,
                )
                .order_by(AnalysisRun.created_at.desc())
                .limit(limit)
            )
            return QuestionListResponse(
                questions=[QuestionSummary.model_validate(run) for run in runs]
            )

    @router.get("/api/v1/questions/{question_id}")
    def read(question_id: uuid.UUID) -> QuestionDetail:
        with database.session() as session:
            return QuestionDetail.model_validate(_run(session, question_id))

    @router.delete("/api/v1/questions/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete(question_id: uuid.UUID, request: Request) -> Response:
        request_id: str | None = getattr(request.state, "request_id", None)
        with database.session() as session:
            run = _run(session, question_id)
            if run.status in (AnalysisStatus.QUEUED, AnalysisStatus.RUNNING):
                raise HTTPException(status.HTTP_409_CONFLICT, "question_in_progress")
            record_audit(
                session, run.workspace_id, "question.delete", "analysis_run", str(run.id),
                AuditResult.SUCCESS, request_id,
            )  # fmt: skip
            forget_analysis_run(session, run.id)
            session.delete(run)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
