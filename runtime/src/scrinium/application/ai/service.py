"""Access to the local LLM for the rest of Scrinium: status and a working check."""

import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final

from scrinium.application.ai.memory_guard import MemoryGuard, MemoryLevel
from scrinium.application.ai.policy import require_local
from scrinium.application.ai.routing import Router, TaskModel
from scrinium.domain.enums import AiTask
from scrinium.infrastructure.llm.base import ChatMessage, LLMProvider
from scrinium.infrastructure.local_http import ModelError, ModelStatus

_STATUS_TTL_SECONDS: Final = 10.0
_TURN_CHECK_SECONDS: Final = 2.0
# Check with a fixed text: never the person's content.
_CHECK_PROMPT: Final = "Responde solo con la palabra: listo"


@dataclass(frozen=True)
class CheckResult:
    status: str  # "ok", "model_missing", "unavailable" or "failed"
    duration_ms: int


class LlmService:
    def __init__(
        self,
        provider: LLMProvider,
        memory: MemoryGuard | None = None,
        router: Router | None = None,
    ) -> None:
        """`provider` is this machine's model: always local. `router` sends a task of a project
        to a remote model only where that project's consent says so (ADR-010)."""
        self.provider = require_local(provider)
        self.router = router
        self.memory = memory or MemoryGuard(read_available=lambda: None)
        self._status_cache: tuple[float, ModelStatus] | None = None
        # A person's questions in progress: batch extraction yields the model to them.
        self._interactive = 0
        self._idle = threading.Condition()

    def for_task(self, project_id: uuid.UUID, task: AiTask, choice: str | None = None) -> TaskModel:
        """The model this task of this project uses, decided on every call."""
        return TaskModel(self.provider, self.router, project_id, task, choice)

    def wait_for_background_turn(
        self, should_stop: Callable[[], bool], check_seconds: float = _TURN_CHECK_SECONDS
    ) -> bool:
        """Background work waits here before each batch: until no questions are running
        and the machine has free memory. False if it must stop meanwhile."""
        while True:
            if should_stop():
                self.memory.waiting = False
                return False
            if not self.wait_for_interactive(check_seconds):
                continue
            if self.memory.level() is MemoryLevel.OK:
                self.memory.waiting = False
                return True
            self.memory.waiting = True
            with self._idle:
                self._idle.wait(check_seconds)

    def memory_too_low_for_questions(self) -> bool:
        """A question would load the model right when the machine has the least memory."""
        return self.memory.level() is MemoryLevel.CRITICAL

    def begin_interactive(self) -> None:
        """Someone is waiting for an answer (a question): it takes priority over batches."""
        with self._idle:
            self._interactive += 1

    def end_interactive(self) -> None:
        with self._idle:
            self._interactive -= 1
            self._idle.notify_all()

    @contextmanager
    def interactive(self) -> Iterator[None]:
        self.begin_interactive()
        try:
            yield
        finally:
            self.end_interactive()

    def wait_for_interactive(self, timeout: float) -> bool:
        """Background work waits here between batches. True if the model became free."""
        with self._idle:
            return self._idle.wait_for(lambda: self._interactive == 0, timeout)

    def status(self) -> ModelStatus:
        """With a short cache: the status screen must not wait for the model on every load."""
        now = time.monotonic()
        cached = self._status_cache
        if cached is not None and now - cached[0] < _STATUS_TTL_SECONDS:
            return cached[1]
        status = self.provider.status()
        self._status_cache = (now, status)
        return status

    def check(self) -> CheckResult:
        self._status_cache = None
        status = self.status()
        if status is not ModelStatus.READY:
            return CheckResult(status.value, 0)
        started = time.perf_counter()
        try:
            completion = self.provider.complete(
                [ChatMessage("user", _CHECK_PROMPT)], max_tokens=1024
            )
        except ModelError:
            return CheckResult("failed", int((time.perf_counter() - started) * 1000))
        ok = "listo" in completion.text.lower()
        return CheckResult("ok" if ok else "failed", completion.duration_ms)
