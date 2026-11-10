"""Memory guard: background work waits if the machine lacks free memory, and with
critical memory the models are unloaded and questions are not answered."""

import threading
import time
from typing import Any

from scrinium.application.ai.memory_guard import GIB, MemoryGuard, MemoryLevel
from scrinium.application.analysis import extraction
from tests.integration.test_analyses import Api
from tests.integration.test_extraction_queue import _run_id, _status, api  # noqa: F401

TOTAL = 16 * GIB  # floor: 15 %, 2.4 GiB


def _guard(
    target: Api, available: dict[str, int], releases: list[int] | None = None
) -> MemoryGuard:
    guard = MemoryGuard(
        read_available=lambda: available["bytes"],
        total_bytes=TOTAL,
        release_models=(lambda: releases.append(1)) if releases is not None else None,
    )
    target.runtime.llm.memory = guard
    return guard


def test_extraction_waits_for_free_memory_and_then_continues(api: Api) -> None:  # noqa: F811
    available = {"bytes": int(1.5 * GIB)}
    guard = _guard(api, available)
    runtime = api.runtime
    run_id = _run_id(runtime, api.project_id)
    worker = threading.Thread(
        target=extraction.extract_rules, args=(runtime.database, runtime.llm, run_id, None)
    )
    worker.start()
    time.sleep(1.5)
    # With low memory no batch reaches the model, and the UI can say so.
    assert runtime.chat.received == []
    assert guard.waiting and _status(api)["waiting_for_memory"] is True

    available["bytes"] = 8 * GIB
    worker.join(timeout=30)
    assert not worker.is_alive()
    assert len(runtime.chat.received) == 4
    assert api.read(str(run_id))["status"] == "completed"
    assert _status(api)["waiting_for_memory"] is False


def test_pausing_works_while_waiting_for_memory(api: Api) -> None:  # noqa: F811
    _guard(api, {"bytes": int(1.5 * GIB)})
    runtime = api.runtime
    run_id = _run_id(runtime, api.project_id)
    stop = threading.Event()
    worker = threading.Thread(
        target=extraction.extract_rules,
        args=(runtime.database, runtime.llm, run_id, None),
        kwargs={"should_stop": stop.is_set},
    )
    worker.start()
    time.sleep(1.2)
    stop.set()
    worker.join(timeout=10)
    assert not worker.is_alive()
    detail: dict[str, Any] = api.read(str(run_id))
    assert detail["status"] == "cancelled" and runtime.chat.received == []
    assert _status(api)["pending"] == 4


def test_critical_memory_releases_models_once_and_refuses_questions(api: Api) -> None:  # noqa: F811
    available = {"bytes": 512 * 2**20}
    releases: list[int] = []
    guard = _guard(api, available, releases)
    assert guard.level() is MemoryLevel.CRITICAL
    assert guard.level() is MemoryLevel.CRITICAL
    assert releases == [1]

    question = api.post(
        f"/api/v1/projects/{api.project_id}/questions", {"question": "regla_1 valor limite"}
    )
    answer = api.runtime.client.get(f"/api/v1/questions/{question['id']}").json()
    assert answer["status"] == "failed" and answer["error_summary"] == "low_memory"
    assert api.runtime.chat.received == []

    # Once it recovers it answers again, and a new drop frees memory again.
    available["bytes"] = 8 * GIB
    assert guard.level() is MemoryLevel.OK
    available["bytes"] = 512 * 2**20
    guard.level()
    assert releases == [1, 1]


def test_without_a_memory_reading_nothing_is_held_back() -> None:
    guard = MemoryGuard(read_available=lambda: None, total_bytes=TOTAL)
    assert guard.level() is MemoryLevel.OK
