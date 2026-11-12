"""Extraction queue: resumable, incremental, pausable, and with priority for questions."""

import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from scrinium.application.analysis import extraction
from scrinium.application.analysis.runs import create_run
from scrinium.domain.enums import AnalysisType
from scrinium.infrastructure.llm.base import ChatMessage, Completion
from scrinium.infrastructure.local_http import ModelError
from scrinium.infrastructure.sqlite.models import ExtractionTask, Project
from tests.conftest import Runtime
from tests.integration.test_analyses import Api, _extraction_reply
from tests.integration.test_ingestion import commit_all, git, write


def _function(index: int) -> str:
    # Long enough for each one to take its own batch. With decisions throughout the
    # body: filler without decisions would not reach the model (F10, windows).
    body = f"def regla_{index}(valor):\n    if valor > {index + 10}:\n"
    body += "        raise ValueError('limite')\n"
    return body + f"    if valor > {index}:\n        valor -= 1\n" * 150


@pytest.fixture
def api(runtime: Runtime, tmp_path: Path) -> Api:
    root = tmp_path / "app"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for index in range(4):
        write(root, f"src/modulo_{index}.py", _function(index))
    commit_all(root, "inicio")
    runtime.chat.reply = _extraction_reply()
    return Api(runtime, root)


def _sent(runtime: Runtime) -> list[str]:
    return [batch[1].content for batch in runtime.chat.received]


def _status(api: Api) -> dict[str, Any]:
    response = api.runtime.client.get(f"/api/v1/projects/{api.project_id}/extraction-status")
    status: dict[str, Any] = response.json()
    return status


def _extract(api: Api, body: dict[str, Any] | None = None) -> dict[str, Any]:
    return api.read(api.post(f"/api/v1/projects/{api.project_id}/extractions", body or {})["id"])


def _fail_calls(runtime: Runtime, monkeypatch: pytest.MonkeyPatch, failing: set[int]) -> None:
    """The model fails on the given calls (counting from 0) and answers on the rest."""
    original = runtime.chat.complete
    calls = {"n": 0}

    def complete(messages: list[ChatMessage], *args: Any, **kwargs: Any) -> Completion:
        index = calls["n"]
        calls["n"] += 1
        if index in failing:
            runtime.chat.received.append(messages)
            raise ModelError
        return original(messages, *args, **kwargs)

    monkeypatch.setattr(runtime.chat, "complete", complete)


def test_a_failed_batch_is_retried_once_and_only_it(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fail_calls(api.runtime, monkeypatch, {1})
    first = _extract(api)
    assert first["status"] == "partial"
    assert first["result"]["batches_total"] == 4 and first["result"]["batches_failed"] == 1
    failed_function = re.search(r"funcion: (\w+)", _sent(api.runtime)[1])
    assert _status(api) == {
        "pending": 1,
        "done": 3,
        "failed": 0,
        "running_analysis_id": None,
        "waiting_for_memory": False,
    }

    # The next extraction only sends the batch that failed.
    api.runtime.chat.received.clear()
    _fail_calls(api.runtime, monkeypatch, {0})
    second = _extract(api)
    assert second["result"]["already_done"] == 3 and second["result"]["pending"] == 1
    assert failed_function is not None
    assert (
        len(_sent(api.runtime)) == 1 and f"funcion: {failed_function[1]}" in _sent(api.runtime)[0]
    )
    # After two attempts it is left: it does not block later extractions.
    assert _status(api)["failed"] == 1
    api.runtime.chat.received.clear()
    third = _extract(api)
    assert third["result"]["failed_before"] == 1 and _sent(api.runtime) == []


def test_each_extraction_records_the_tokens_it_spent(api: Api) -> None:
    # The test model reports 10 tokens read and 5 written per call; there are 4 batches.
    result = _extract(api)["result"]
    assert result["tokens"] == {"input": 40, "output": 20, "cached": 0, "calls": 4}
    # What was already analyzed does not go back to the model: it spends nothing.
    assert _extract(api)["result"]["tokens"] == {"input": 0, "output": 0, "cached": 0, "calls": 0}


def test_only_changed_code_goes_back_to_the_model(api: Api) -> None:
    _extract(api)
    api.runtime.chat.received.clear()
    # Only comments and whitespace: the logical hash does not change and nothing goes back.
    write(
        api.repo, "src/modulo_1.py", "# revisado\n" + _function(1).replace("if valor", "if  valor")
    )
    commit_all(api.repo, "formato")
    api.sync()
    assert _sent(api.runtime) == []

    # A real change: after syncing, extraction catches up by itself, with that only.
    write(api.repo, "src/modulo_2.py", _function(2).replace("> 12", "> 99"))
    commit_all(api.repo, "cambio")
    api.sync()
    assert [("modulo_2.py" in text) for text in _sent(api.runtime)] == [True]
    api.runtime.chat.received.clear()
    again = _extract(api)
    assert again["result"]["already_done"] == 4 and again["result"]["pending"] == 0
    assert _sent(api.runtime) == []
    # The old code's tasks went with their fingerprint: the queue does not grow forever.
    assert _status(api)["done"] == 4


def _run_id(runtime: Runtime, project_id: str) -> uuid.UUID:
    with runtime.database.session() as session:
        project = session.get_one(Project, uuid.UUID(project_id))
        return create_run(
            session, project, AnalysisType.KNOWLEDGE_EXTRACTION, "Extraer reglas", ""
        ).id


def test_pausing_keeps_the_rest_in_the_queue(api: Api) -> None:
    runtime = api.runtime
    run_id = _run_id(runtime, api.project_id)
    stop = threading.Event()
    original = runtime.chat.complete

    def complete_then_pause(*args: Any, **kwargs: Any) -> Completion:
        stop.set()
        return original(*args, **kwargs)

    runtime.chat.complete = complete_then_pause  # type: ignore[method-assign]
    extraction.extract_rules(runtime.database, runtime.llm, run_id, None, should_stop=stop.is_set)
    detail = api.read(str(run_id))
    assert detail["status"] == "cancelled" and detail["result"]["batches"] == 1
    assert _status(api)["pending"] == 3
    # Pausing something already finished makes no sense.
    response = runtime.client.post(f"/api/v1/analyses/{run_id}/cancel", headers=api.headers)
    assert response.status_code == 409


def test_pausing_stops_the_extraction_that_resumed_at_startup(
    api: Api, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What happened in the pilot: with the extraction that resumes at startup running,
    another request created a second one waiting its turn; the status showed the second, and Pause
    stopped it, while the first kept working."""
    _fail_calls(api.runtime, monkeypatch, {0, 1, 2, 3})
    _extract(api)
    monkeypatch.undo()
    runtime = api.runtime
    original = runtime.chat.complete
    seen: dict[str, Any] = {}

    def complete(*args: Any, **kwargs: Any) -> Completion:
        if not seen:
            path = f"/api/v1/projects/{api.project_id}/extractions"
            seen["second"] = runtime.client.post(path, json={}, headers=api.headers)
            seen["status"] = _status(api)
            running = seen["status"]["running_analysis_id"]
            seen["cancel"] = runtime.client.post(
                f"/api/v1/analyses/{running}/cancel", headers=api.headers
            )
        return original(*args, **kwargs)

    monkeypatch.setattr(runtime.chat, "complete", complete)
    analyses = runtime.app.state.analyses
    assert extraction.resume_pending(runtime.database, runtime.llm, analyses) == 1

    assert seen["second"].status_code == 409
    assert seen["second"].json()["error"] == "extraction_running"
    assert seen["cancel"].status_code == 202
    resumed = api.read(seen["status"]["running_analysis_id"])
    assert resumed["status"] == "cancelled" and resumed["result"]["batches"] == 1
    assert _status(api) == {
        "pending": 3,
        "done": 1,
        "failed": 0,
        "running_analysis_id": None,
        "waiting_for_memory": False,
    }
    # Stopped, it can be launched again.
    assert _extract(api)["status"] == "completed"


def test_questions_go_first(api: Api) -> None:
    runtime = api.runtime
    run_id = _run_id(runtime, api.project_id)
    runtime.llm.begin_interactive()
    worker = threading.Thread(
        target=extraction.extract_rules, args=(runtime.database, runtime.llm, run_id, None)
    )
    worker.start()
    time.sleep(0.5)
    # While someone waits for an answer, no batch uses the model.
    assert runtime.chat.received == []
    runtime.llm.end_interactive()
    worker.join(timeout=30)
    assert len(runtime.chat.received) == 4


def test_unfinished_work_resumes_at_startup(api: Api, monkeypatch: pytest.MonkeyPatch) -> None:
    _fail_calls(api.runtime, monkeypatch, {0, 1})
    _extract(api)
    assert _status(api)["pending"] == 2
    monkeypatch.undo()

    analyses = api.runtime.app.state.analyses
    assert extraction.resume_pending(api.runtime.database, api.runtime.llm, analyses) == 1
    assert _status(api)["pending"] == 0
    with api.runtime.database.session() as session:
        assert {task.status for task in session.query(ExtractionTask)} == {"done"}
    assert extraction.resume_pending(api.runtime.database, api.runtime.llm, analyses) == 0


def test_a_failed_batch_with_several_functions_is_retried_in_halves(
    runtime: Runtime, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F9: the batch goes again at once in two halves; nothing waits for the next run."""
    root = tmp_path / "small"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    for index in range(4):
        write(
            root,
            f"src/pequena_{index}.py",
            f"def regla_{index}(v):\n    if v > {index + 10}:\n        raise ValueError('x')\n",
        )
    commit_all(root, "inicio")
    runtime.chat.reply = _extraction_reply()
    api = Api(runtime, root)
    _fail_calls(runtime, monkeypatch, {0})
    done = _extract(api)
    result = done["result"]
    assert done["status"] == "completed", result
    assert result["batches_split"] == 1 and result["batches_failed"] == 0
    assert result["failure_reasons"] == {"ModelError": 1}
    # The first call held all four; then each half, with its own numbering from F1.
    sent = _sent(runtime)
    assert len(sent) == 3 and sent[0].count("<<FUENTE") == 4
    assert sent[1].count("<<FUENTE") == 2 and sent[2].count("<<FUENTE") == 2
    assert _status(api)["pending"] == 0 and _status(api)["failed"] == 0
