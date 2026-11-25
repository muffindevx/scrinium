from types import SimpleNamespace
from typing import Any, cast

import pytest
import uvicorn

from scrinium.cli.main import open_when_ready


def _started_server() -> uvicorn.Server:
    return cast(uvicorn.Server, SimpleNamespace(started=True, should_exit=False))


def _open_with_result(monkeypatch: pytest.MonkeyPatch, result: bool) -> None:
    def fake_open(_url: str, *_args: Any, **_kwargs: Any) -> bool:
        return result

    monkeypatch.setattr("scrinium.cli.main.webbrowser.open", fake_open)
    open_when_ready(_started_server(), "http://127.0.0.1:1/auth/launch?token=x")


def test_failure_to_open_the_browser_is_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _open_with_result(monkeypatch, False)
    assert "The browser could not be opened" in capsys.readouterr().out


def test_successful_open_prints_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _open_with_result(monkeypatch, True)
    assert capsys.readouterr().out == ""
