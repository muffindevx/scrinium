"""Common lifecycle of an analysis: created in the request and run on another thread.

Each analysis only stores a short description and the hash of its input: a diff or a schema is
never copied whole into the history. Results keep references (paths, lines, commits,
identifiers), with recognizable secrets redacted.
"""

import hashlib
import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Any, Final

from sqlalchemy.orm import Session

from scrinium.application.ai.service import LlmService
from scrinium.application.workspaces import record_audit
from scrinium.core.secret_patterns import redact_tokens
from scrinium.domain.enums import AiTask, AnalysisStatus, AnalysisType, AuditResult
from scrinium.infrastructure.llm.base import provider_name
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import AnalysisRun, Project, utc_now

logger = logging.getLogger("scrinium.analysis")

MAX_DESCRIPTION_CHARS: Final = 300


_TASKS: Final = {
    AnalysisType.QUESTION: AiTask.QUESTIONS,
    AnalysisType.KNOWLEDGE_EXTRACTION: AiTask.RULE_MAP,
    AnalysisType.CODE_DIFF: AiTask.CHANGE_IMPACT,
}


def create_run(
    session: Session,
    project: Project,
    analysis_type: AnalysisType,
    description: str,
    input_text: str,
    *,
    prompt_version: str = "0",
    retrieval_version: str = "0",
    llm: LlmService | None = None,
    task: AiTask | None = None,
    base_revision: str | None = None,
    target_revision: str | None = None,
    release_ref: str | None = None,
    request_id: str | None = None,
) -> AnalysisRun:
    # The model this run's task uses for this project: local, or the consented one (ADR-010).
    model = None
    if llm is not None:
        model = llm.for_task(project.id, task or _TASKS.get(analysis_type, AiTask.RULE_MAP))
    run = AnalysisRun(
        workspace_id=project.workspace_id,
        project_id=project.id,
        analysis_type=analysis_type,
        question=redact_tokens(description.strip())[:MAX_DESCRIPTION_CHARS],
        input_hash=hashlib.sha256(input_text.encode()).hexdigest(),
        model_provider=provider_name(model) if model is not None else None,
        model_name=model.model_id if model is not None else None,
        prompt_version=prompt_version,
        retrieval_version=retrieval_version,
        base_revision=base_revision,
        target_revision=target_revision,
        release_ref=release_ref,
    )
    session.add(run)
    session.flush()
    # Without the input: the audit log only records that it was requested, and of what type.
    record_audit(
        session, project.workspace_id, f"analysis.{analysis_type.value}", "analysis_run",
        str(run.id), AuditResult.SUCCESS, request_id,
    )  # fmt: skip
    return run


def start_run(database: Database, run_id: uuid.UUID) -> bool:
    """Marks the analysis as running. False if it is no longer queued (repeated or deleted)."""
    with database.session() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None or run.status is not AnalysisStatus.QUEUED:
            return False
        run.status = AnalysisStatus.RUNNING
        run.started_at = utc_now()
        return True


def report_progress(database: Database, run_id: uuid.UUID, result: dict[str, Any]) -> None:
    """Partial result of a long analysis, without changing its status."""
    with database.session() as session:
        run = session.get(AnalysisRun, run_id)
        if run is not None:
            run.result = dict(result)


def finish_run(
    database: Database,
    run_id: uuid.UUID,
    status: AnalysisStatus,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with database.session() as session:
        run = session.get(AnalysisRun, run_id)
        if run is None:
            return
        run.status = status
        run.result = result or {}
        run.error_summary = error
        run.finished_at = utc_now()


Task = Callable[[uuid.UUID], None]


class AnalysisRunner:
    """Runs analyses one at a time, on its own thread, apart from questions."""

    def __init__(
        self, database: Database, llm: LlmService, executor: Executor | None = None
    ) -> None:
        self.database = database
        self.llm = llm
        self._executor = executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="analysis"
        )
        # Long analyses (batch extraction) that must stop at the next batch.
        self._cancelled: set[uuid.UUID] = set()
        self._stopping = False
        # Submitted analyses that have not started yet: a low-priority one (rule
        # statements) yields its turn to them.
        self._waiting = 0
        self._waiting_lock = threading.Lock()

    def cancel(self, run_id: uuid.UUID) -> None:
        self._cancelled.add(run_id)

    def should_stop(self, run_id: uuid.UUID) -> bool:
        return self._stopping or run_id in self._cancelled

    def has_waiting(self) -> bool:
        with self._waiting_lock:
            return self._waiting > 0

    def submit(self, task: Task, run_id: uuid.UUID) -> None:
        with self._waiting_lock:
            self._waiting += 1
        self._executor.submit(self._run, task, run_id)

    def _run(self, task: Task, run_id: uuid.UUID) -> None:
        with self._waiting_lock:
            self._waiting -= 1
        try:
            if start_run(self.database, run_id):
                task(run_id)
        except Exception as failure:
            logger.error("analysis_crashed run=%s type=%s", run_id, type(failure).__name__)
            finish_run(self.database, run_id, AnalysisStatus.FAILED, error="internal_error")

    def shutdown(self) -> None:
        # A long extraction finishes at the next batch; what is pending stays in the queue.
        self._stopping = True
        self._executor.shutdown(wait=True, cancel_futures=True)
