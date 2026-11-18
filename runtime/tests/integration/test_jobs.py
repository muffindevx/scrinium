"""The durable job queue (phase 9.2): idempotent, by priority, leased, retried, recovered."""

from datetime import timedelta
from typing import Any

from sqlalchemy import select, update

from scrinium.application.jobs import DONE, FAILED, LEASED, QUEUED, JobQueue
from scrinium.infrastructure.sqlite.models import Job, utc_now
from tests.conftest import Runtime


def _status(runtime: Runtime) -> dict[str, str]:
    with runtime.database.session() as session:
        return {job.kind: job.status for job in session.scalars(select(Job))}


def test_the_same_work_is_queued_once_and_runs_by_priority(runtime: Runtime) -> None:
    ran: list[str] = []
    queue = JobQueue(runtime.database, {
        "vectores": lambda _p: ran.append("vectores"),
        "vigilar": lambda p: ran.append(f"vigilar {p['sync']}"),
    })  # fmt: skip
    first = queue.enqueue("vectores", {}, priority=1)
    assert queue.enqueue("vectores", {}, priority=1) == first
    queue.enqueue("vigilar", {"sync": "a"}, priority=5)
    assert queue.run_ready() == 2
    assert ran == ["vigilar a", "vectores"]
    # Done, the same work can be asked for again.
    assert queue.enqueue("vectores", {}) != first


def test_a_failure_is_retried_later_and_then_gives_up(runtime: Runtime) -> None:
    calls: list[int] = []

    def broken(_payload: dict[str, Any]) -> None:
        calls.append(1)
        raise OSError("ruta secreta /home/ana/proyecto")

    queue = JobQueue(runtime.database, {"roto": broken})
    queue.enqueue("roto", {}, max_attempts=2)
    queue.run_ready()
    assert len(calls) == 1 and _status(runtime) == {"roto": QUEUED}
    with runtime.database.session() as session:
        job = session.scalars(select(Job)).one()
        assert job.last_error == "OSError" and job.run_after > utc_now()
        session.execute(update(Job).values(run_after=utc_now() - timedelta(seconds=1)))
    queue.run_ready()
    assert len(calls) == 2 and _status(runtime) == {"roto": FAILED}


def test_a_job_whose_worker_died_is_taken_again(runtime: Runtime) -> None:
    ran: list[int] = []
    queue = JobQueue(runtime.database, {"largo": lambda _p: ran.append(1)})
    queue.enqueue("largo", {})
    taken = queue.claim()
    assert taken is not None and _status(runtime) == {"largo": LEASED}
    assert queue.claim() is None  # leased: nobody else takes it
    # Scrinium closed with it leased: at the next start it goes back to the queue.
    assert queue.recover() == 1
    assert queue.run_ready() == 1 and ran == [1] and _status(runtime) == {"largo": DONE}


def test_an_expired_lease_is_taken_again(runtime: Runtime) -> None:
    queue = JobQueue(runtime.database, {"largo": lambda _p: None})
    queue.enqueue("largo", {})
    assert queue.claim() is not None
    with runtime.database.session() as session:
        session.execute(update(Job).values(leased_until=utc_now() - timedelta(seconds=1)))
    again = queue.claim()
    assert again is not None and again.attempts == 2
