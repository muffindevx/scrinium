"""The durable job queue (phase 9.2, R7, Annex B.7).

A job is a row (`Job`): it survives a restart. It runs once per input while pending (its `key`
is the SHA-256 of kind and payload: asking again joins the pending job), by priority and then
age, under a **lease**: a worker that dies leaves the lease to expire and the job is taken
again. A failure is retried with a growing wait (5 s, 20 s, 80 s…) up to `max_attempts`; then the
job stays `failed`, with the exception's type and never its message.

The queue does not own a thread: `run_ready` is called on the sync worker (one writer for
SQLite), after each sync and once at startup. Handlers take the payload and do their own
transactions.
"""

import hashlib
import json
import logging
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Final

from sqlalchemy import delete, or_, select, update

from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import Job, utc_now

logger = logging.getLogger("scrinium.jobs")

QUEUED: Final = "queued"
LEASED: Final = "leased"
DONE: Final = "done"
FAILED: Final = "failed"
LEASE: Final = timedelta(minutes=30)
_BACKOFF_SECONDS: Final = 5
# Finished jobs are kept this long, then removed: the queue is not an archive.
_KEEP_DONE: Final = timedelta(days=7)

Handler = Callable[[dict[str, Any]], object]


def job_key(kind: str, payload: dict[str, Any]) -> str:
    raw = json.dumps([kind, payload], sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


class JobQueue:
    def __init__(self, database: Database, handlers: dict[str, Handler] | None = None) -> None:
        self._database = database
        self.handlers: dict[str, Handler] = dict(handlers or {})

    def enqueue(
        self, kind: str, payload: dict[str, Any] | None = None, priority: int = 0,
        max_attempts: int = 3,
    ) -> uuid.UUID:  # fmt: skip
        """The job's id; the pending one's if the same work is already waiting."""
        payload = payload or {}
        key = job_key(kind, payload)
        with self._database.session() as session:
            pending = session.scalars(
                select(Job.id).where(Job.key == key, Job.status.in_((QUEUED, LEASED)))
            ).first()
            if pending is not None:
                return pending
            job = Job(kind=kind, key=key, payload=payload, priority=priority,
                      max_attempts=max_attempts)  # fmt: skip
            session.add(job)
            session.flush()
            return job.id

    def claim(self) -> Job | None:
        """The next ready job, leased to the caller; None if there is none."""
        now = utc_now()
        with self._database.session() as session:
            candidates = session.scalars(
                select(Job)
                .where(
                    or_(
                        (Job.status == QUEUED) & (Job.run_after <= now),
                        (Job.status == LEASED) & (Job.leased_until < now),
                    )
                )
                .order_by(Job.priority.desc(), Job.created_at)
                .limit(5)
            ).all()
            for job in candidates:
                # Conditional update: two workers never take the same job.
                taken = session.execute(
                    update(Job)
                    .where(Job.id == job.id, Job.status == job.status,
                           Job.attempts == job.attempts)
                    .values(status=LEASED, leased_until=now + LEASE, attempts=job.attempts + 1)
                ).rowcount  # type: ignore[attr-defined]  # fmt: skip
                if taken:
                    session.flush()
                    session.refresh(job)
                    session.expunge(job)
                    return job
        return None

    def complete(self, job_id: uuid.UUID) -> None:
        with self._database.session() as session:
            session.execute(
                update(Job).where(Job.id == job_id)
                .values(status=DONE, finished_at=utc_now(), leased_until=None)
            )  # fmt: skip

    def fail(self, job: Job, error: BaseException) -> None:
        with self._database.session() as session:
            last = job.attempts >= job.max_attempts
            wait = timedelta(seconds=_BACKOFF_SECONDS * 4 ** (job.attempts - 1))
            session.execute(
                update(Job).where(Job.id == job.id).values(
                    status=FAILED if last else QUEUED,
                    last_error=type(error).__name__[:100],
                    leased_until=None,
                    run_after=utc_now() + wait,
                    finished_at=utc_now() if last else None,
                )
            )  # fmt: skip

    def run_ready(self, limit: int = 1000) -> int:
        """Runs ready jobs until none is left (or `limit`). Returns how many ran."""
        ran = 0
        while ran < limit:
            job = self.claim()
            if job is None:
                break
            ran += 1
            handler = self.handlers.get(job.kind)
            if handler is None:
                self.fail(job, LookupError())
                continue
            try:
                handler(dict(job.payload or {}))
            except Exception as error:
                logger.error("job_failed kind=%s type=%s", job.kind, type(error).__name__)
                self.fail(job, error)
            else:
                self.complete(job.id)
        self._forget_old()
        return ran

    def recover(self) -> int:
        """At startup: jobs leased by a Scrinium that is gone go back to the queue."""
        with self._database.session() as session:
            return int(
                session.execute(
                    update(Job).where(Job.status == LEASED).values(status=QUEUED, leased_until=None)
                ).rowcount  # type: ignore[attr-defined]
            )

    def counts(self) -> dict[str, int]:
        from sqlalchemy import func

        with self._database.session() as session:
            rows = session.execute(select(Job.status, func.count()).group_by(Job.status)).all()
        return {status: int(count) for status, count in rows}

    def _forget_old(self) -> None:
        with self._database.session() as session:
            session.execute(
                delete(Job).where(Job.status == DONE, Job.finished_at < utc_now() - _KEEP_DONE)
            )
