"""Every provider answers through one contract (ADR-010).

Each adapter is fed recorded responses of its provider (`provider_fixtures/<kind>/*.json`) and
must give the same `Completion`, or a `ModelError` with the same outcome. A new provider is
accepted when its own folder of cases passes here.
"""

import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import anthropic
import pytest

from scrinium.infrastructure.llm.anthropic_chat import AnthropicChat
from scrinium.infrastructure.llm.base import ChatMessage, Completion, LLMProvider
from scrinium.infrastructure.llm.ollama import OllamaChat
from scrinium.infrastructure.llm.openai_compatible import OpenAICompatibleChat
from scrinium.infrastructure.llm.remote_openai import RemoteOpenAIChat
from scrinium.infrastructure.local_http import (
    LocalHttpClient,
    ModelError,
    Outcome,
    ProviderUnavailableError,
)

FIXTURES = Path(__file__).parent / "provider_fixtures"
MESSAGES = [ChatMessage("system", "Responde en JSON."), ChatMessage("user", "Hola")]
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def _openai_compatible(response: Any) -> LLMProvider:
    chat = OpenAICompatibleChat("http://127.0.0.1:9", "modelo")
    chat._client.request = lambda *_args: response  # type: ignore[method-assign,union-attr]
    return chat


def _ollama(response: Any) -> LLMProvider:
    chat = OllamaChat("http://127.0.0.1:9", "modelo")

    def request(path: str, *_args: Any) -> Any:
        return {"capabilities": []} if path == "/api/show" else response

    chat._client.request = request  # type: ignore[method-assign,assignment]
    return chat


def _remote_openai(response: Any) -> LLMProvider:
    chat = RemoteOpenAIChat("remoto", "https://api.example.com", "modelo", lambda: "sk-x")
    chat._client.request = lambda *_args: response  # type: ignore[method-assign]
    return chat


class _FakeAnthropic:
    """What `anthropic.Anthropic(...)` returns, answering one recorded message."""

    def __init__(self, response: Any, **options: Any) -> None:
        self.options = options
        self.messages = self
        self._response = response

    def create(self, **_request: Any) -> Any:
        return anthropic.types.Message.model_validate(self._response)

    def close(self) -> None:
        pass


def _anthropic(response: Any) -> LLMProvider:
    return AnthropicChat(
        "claude", "https://api.anthropic.com", "modelo", lambda: "sk-ant-x",
        client_factory=lambda **options: _FakeAnthropic(response, **options),
    )  # fmt: skip


ADAPTERS: dict[str, Callable[[Any], LLMProvider]] = {
    "anthropic": _anthropic,
    "openai_compatible": _openai_compatible,
    "ollama": _ollama,
    "remote_openai": _remote_openai,
}
# An adapter that speaks the same API as another is held to that one's recorded cases.
FOLDERS: dict[str, str] = {"remote_openai": "openai_compatible"}


def _cases() -> list[Any]:
    cases = []
    for kind in sorted(ADAPTERS):
        for path in sorted((FIXTURES / FOLDERS.get(kind, kind)).glob("*.json")):
            cases.append(pytest.param(kind, json.loads(path.read_text()), id=f"{kind}/{path.stem}"))
    return cases


def test_every_adapter_has_its_cases() -> None:
    """A provider without recorded cases is not held to the contract."""
    for kind in ADAPTERS:
        outcomes = {
            json.loads(path.read_text())["expect"]["outcome"]
            for path in (FIXTURES / FOLDERS.get(kind, kind)).glob("*.json")
        }
        assert {"completed", "length", "error"} <= outcomes, kind


@pytest.mark.parametrize(("kind", "case"), _cases())
def test_adapter_follows_the_contract(kind: str, case: dict[str, Any]) -> None:
    provider = ADAPTERS[kind](case["response"])
    expect = case["expect"]
    if expect["outcome"] != Outcome.COMPLETED:
        with pytest.raises(ModelError) as raised:
            provider.complete(MESSAGES, 256, SCHEMA)
        assert raised.value.outcome == expect["outcome"], case["note"]
        # Errors never carry what was sent or received.
        assert str(raised.value) == ""
        return
    completion = provider.complete(MESSAGES, 256, SCHEMA)
    assert isinstance(completion, Completion)
    fields = ("text", "prompt_tokens", "completion_tokens", "cached_tokens", "request_id")
    assert {name: getattr(completion, name) for name in fields} == {
        name: expect[name] for name in fields
    }, case["note"]
    assert completion.model_id.endswith(":modelo")
    assert completion.duration_ms >= 0


def test_an_unreachable_provider_is_unavailable() -> None:
    """Nothing listening: the request fails as `unavailable`, never as a wrong answer."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    client = LocalHttpClient(f"http://127.0.0.1:{port}")
    with pytest.raises(ProviderUnavailableError) as raised:
        client.request("/v1/chat/completions", {"x": 1}, 2)
    assert raised.value.outcome is Outcome.UNAVAILABLE
