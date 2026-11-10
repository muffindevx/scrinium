"""Memory guard: Scrinium must not starve the machine of memory (and freeze it on swap).

It computes a free-memory floor from this machine's memory and respects it:
- below the floor, background work (extraction, consistency review) waits between
  batches and resumes by itself when there is room;
- below the critical minimum, it also unloads the models (they reload on the next
  request) and does not answer questions, which would load the model again.

It checks between batches, not during one: a running batch finishes even if memory drops.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from scrinium.core.memory import available_memory_bytes, total_memory_bytes

logger = logging.getLogger("scrinium.memory")

GIB: Final = 1024**3
MIN_FLOOR_BYTES: Final = 2 * GIB
FLOOR_FRACTION: Final = 0.15
CRITICAL_BYTES: Final = 1 * GIB


class MemoryLevel(StrEnum):
    OK = "ok"
    LOW = "low"
    CRITICAL = "critical"


@dataclass(frozen=True)
class MemoryBudget:
    floor_bytes: int
    critical_bytes: int


def budget_for(total_bytes: int | None) -> MemoryBudget:
    """The floor: 2 GiB or 15 % of total memory, whichever is larger."""
    floor = max(MIN_FLOOR_BYTES, int((total_bytes or 0) * FLOOR_FRACTION))
    return MemoryBudget(floor, min(CRITICAL_BYTES, floor))


class MemoryGuard:
    def __init__(
        self,
        read_available: Callable[[], int | None] = available_memory_bytes,
        total_bytes: int | None = None,
        release_models: Callable[[], None] | None = None,
    ) -> None:
        self._read_available = read_available
        self.budget = budget_for(total_bytes if total_bytes is not None else total_memory_bytes())
        self._release_models = release_models
        self._released = False
        self._lock = threading.Lock()
        self.waiting = False

    def level(self) -> MemoryLevel:
        """Without a reading (a system that does not report it), nothing is held back."""
        available = self._read_available()
        if available is None or available >= self.budget.floor_bytes:
            with self._lock:
                self._released = False
            return MemoryLevel.OK
        if available >= self.budget.critical_bytes:
            return MemoryLevel.LOW
        self._release_once(available)
        return MemoryLevel.CRITICAL

    def _release_once(self, available: int) -> None:
        with self._lock:
            if self._released or self._release_models is None:
                return
            self._released = True
        logger.warning("memory_critical available_mb=%d releasing_models", available // 2**20)
        try:
            self._release_models()
        except Exception as failure:  # releasing is a bonus: it never brings work down
            logger.warning("memory_release_failed type=%s", type(failure).__name__)
