"""Polling the connected trackers (phase 10.3, annex C.3).

A local application receives no webhooks, so it asks. Every few minutes this enqueues a sync for
each active connection with a scope; the job itself skips a connection whose interval has not
passed (10 minutes after a change, doubling to an hour while nothing changes), so asking often
costs nothing. Isolated mode stops it like any other egress.

A daemon thread that only enqueues: the sync runs on the job queue's single writer.
"""

import logging
import threading
from collections.abc import Callable
from typing import Final

from sqlalchemy import select

from scrinium.application.ai.setup import is_isolated
from scrinium.domain.enums import ConnectionStatus
from scrinium.infrastructure.sqlite.database import Database
from scrinium.infrastructure.sqlite.models import TicketConnection

logger = logging.getLogger("scrinium.trackers")

TICK_SECONDS: Final = 5 * 60
SYNC_JOB: Final = "ticket_sync"


def enqueue_due(database: Database, enqueue: Callable[[str], None]) -> int:
    """Enqueues a (non-forced) sync for every active connection. Returns how many."""
    with database.session() as session:
        if is_isolated(session):
            return 0
        ids = [
            str(connection.id)
            for connection in session.scalars(
                select(TicketConnection).where(
                    TicketConnection.status.in_((ConnectionStatus.ACTIVE, ConnectionStatus.ERROR))
                )
            )
            if connection.scope and connection.last_error != "auth"
        ]
    for connection_id in ids:
        enqueue(connection_id)
    return len(ids)


class TicketPoller:
    def __init__(
        self, database: Database, enqueue: Callable[[str], None], run: Callable[[], None],
        tick_seconds: float = TICK_SECONDS,
    ) -> None:  # fmt: skip
        self._database = database
        self._enqueue = enqueue
        self._run = run
        self._tick = tick_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._loop, name="ticket-poller", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            try:
                if enqueue_due(self._database, self._enqueue):
                    self._run()
            except Exception as error:
                logger.warning("ticket_poll_failed type=%s", type(error).__name__)
