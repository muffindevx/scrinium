"""Persistent rule-extraction queue: resumable, incremental and prioritized.

Each candidate (see `candidates`) is a task with a fingerprint of its path, its function and
its content. What was processed and did not change is not sent to the model again; if
Scrinium closes halfway, what is pending stays here and continues at startup.
"""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from scrinium.application.analysis.candidates import Candidate
from scrinium.infrastructure.sqlite.models import ExtractionTask, Project, utc_now

PENDING: Final = "pending"
DONE: Final = "done"
FAILED: Final = "failed"
# A failed batch is retried once in another extraction; after that, it is left.
MAX_ATTEMPTS: Final = 2


def unit_key(candidate: Candidate) -> str:
    """The task's fingerprint. For a function, its logical hash: if only formatting or
    comments change, it does not go back to the model."""
    if candidate.symbol and candidate.logic_hash:
        identity = f"{candidate.logic_hash}\0{candidate.part}"
    else:
        identity = candidate.text
    raw = f"{candidate.path}\0{candidate.symbol or ''}\0{identity}"
    if candidate.source:
        raw = f"{candidate.source}\0{raw}"
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class QueuedWork:
    task_id: uuid.UUID
    candidate: Candidate


@dataclass
class QueueState:
    work: list[QueuedWork]
    already_done: int
    failed: int


def sync_tasks(
    session: Session,
    project: Project,
    candidates: list[Candidate],
    path_prefix: str | None,
    forced: frozenset[str] = frozenset(),
) -> QueueState:
    """Updates the queue with the current candidates and returns what is pending, in order.

    Tasks for code that changed or stopped being a candidate are deleted: their fingerprint is gone.
    """
    query = select(ExtractionTask).where(ExtractionTask.project_id == project.id)
    if path_prefix:
        query = query.where(ExtractionTask.path.startswith(path_prefix, autoescape=True))
    existing = {task.unit_key: task for task in session.scalars(query)}
    seen: set[str] = set()
    work: list[QueuedWork] = []
    already_done = failed = 0
    for candidate in candidates:
        key = unit_key(candidate)
        if key in seen:
            continue
        seen.add(key)
        task = existing.get(key)
        if task is None:
            task = ExtractionTask(
                workspace_id=project.workspace_id, project_id=project.id, unit_key=key,
                status=PENDING, attempts=0, rules_found=0,
                source_revision_id=candidate.revision_id, path=candidate.path[:1000],
                symbol=(candidate.symbol or None) and candidate.symbol[:1000],
                start_line=candidate.start_line, end_line=candidate.end_line,
                score=candidate.score,
            )  # fmt: skip
            session.add(task)
        else:
            # Same content in another revision (another commit): only the references change.
            task.source_revision_id = candidate.revision_id
            task.start_line, task.end_line = candidate.start_line, candidate.end_line
            task.score = candidate.score
        # Forced: code with rules to review, even if it was already analyzed.
        if task.status == DONE and key not in forced:
            already_done += 1
            continue
        if task.status == FAILED and task.attempts >= MAX_ATTEMPTS and key not in forced:
            failed += 1
            continue
        task.status = PENDING
        session.flush()
        work.append(QueuedWork(task.id, candidate))
    stale = [task.id for key, task in existing.items() if key not in seen]
    if stale:
        session.execute(delete(ExtractionTask).where(ExtractionTask.id.in_(stale)))
    work.sort(
        key=lambda item: (-item.candidate.score, item.candidate.path, item.candidate.start_line)
    )
    return QueueState(work, already_done, failed)


def finish_tasks(
    session: Session, task_ids: list[uuid.UUID], rules_by_task: dict[uuid.UUID, int]
) -> None:
    for task in session.scalars(select(ExtractionTask).where(ExtractionTask.id.in_(task_ids))):
        task.status = DONE
        task.rules_found = rules_by_task.get(task.id, 0)
        task.updated_at = utc_now()


def fail_tasks(session: Session, task_ids: list[uuid.UUID]) -> None:
    for task in session.scalars(select(ExtractionTask).where(ExtractionTask.id.in_(task_ids))):
        task.attempts += 1
        task.status = FAILED if task.attempts >= MAX_ATTEMPTS else PENDING
        task.updated_at = utc_now()


def counts(session: Session, project_id: uuid.UUID) -> dict[str, int]:
    rows = session.execute(
        select(ExtractionTask.status, func.count())
        .where(ExtractionTask.project_id == project_id)
        .group_by(ExtractionTask.status)
    ).all()
    found = {status: int(count) for status, count in rows}
    return {name: found.get(name, 0) for name in (PENDING, DONE, FAILED)}


def projects_with_pending(session: Session) -> list[uuid.UUID]:
    return list(
        session.scalars(
            select(ExtractionTask.project_id).where(ExtractionTask.status == PENDING).distinct()
        )
    )


def delete_tasks_of_revisions(session: Session, revision_ids: Select[uuid.UUID]) -> None:
    session.execute(
        delete(ExtractionTask).where(ExtractionTask.source_revision_id.in_(revision_ids))
    )
