"""Claude's adapter (ADR-010). No request leaves this machine: the SDK client is faked, except
where the real client is built (never sent) to check which credentials it would use."""

from typing import Any

import anthropic
import httpx2
import pytest

from scrinium.infrastructure.llm.anthropic_chat import AnthropicChat
from scrinium.infrastructure.llm.base import ChatMessage
from scrinium.infrastructure.local_http import ModelError, Outcome, ProviderUnavailableError
from scrinium.infrastructure.remote_http import ProviderAuthError, ProviderRequestError

KEY = "sk-ant-desde-el-llavero"
MESSAGE = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
           "content": [{"type": "text", "text": '{"ok": true}'}], "stop_reason": "end_turn",
           "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 3}}  # fmt: skip
MESSAGES = [ChatMessage("system", "Responde en JSON."), ChatMessage("user", "Hola")]


class Recorder:
    """A fake SDK client: records how it was built and what it was asked, and replies."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.built: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.closed = 0

    def __call__(self, **options: Any) -> "Recorder":
        self.built.append(options)
        return self

    @property
    def messages(self) -> "Recorder":
        return self

    def create(self, **request: Any) -> Any:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return anthropic.types.Message.model_validate(reply)

    def close(self) -> None:
        self.closed += 1


def _status_error(cls: type[anthropic.APIStatusError], status: int) -> Exception:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx2.Response(status, request=request, json={"error": {"message": KEY}})
    return cls(f"clave {KEY}", response=response, body=None)


def test_each_request_reads_the_key_and_sends_what_the_contract_says() -> None:
    reads: list[int] = []

    def key() -> str:
        reads.append(1)
        return KEY

    sdk = Recorder(MESSAGE, MESSAGE)
    chat = AnthropicChat(
        "claude", "https://api.anthropic.com/", "claude-opus-5-5",
        key, effort="low", client_factory=sdk,
    )  # fmt: skip
    completion = chat.complete(MESSAGES, 64, {"type": "object"})
    chat.complete(MESSAGES, 64)
    assert completion.text == '{"ok": true}' and completion.model_id == "claude:claude-opus-5-5"
    # A client per request, with the key just read and the registered URL; then closed.
    assert len(reads) == 2 and sdk.closed == 2
    assert all(
        o["api_key"] == KEY and o["base_url"] == "https://api.anthropic.com" for o in sdk.built
    )
    first = sdk.requests[0]
    assert first["system"] == "Responde en JSON."
    assert first["messages"] == [{"role": "user", "content": "Hola"}]
    assert first["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}},
        "effort": "low",
    }
    assert not {"temperature", "top_p", "top_k", "fallbacks"} & set(first)
    assert not chat.is_local


def test_a_rejected_schema_falls_back_on_the_same_model() -> None:
    sdk = Recorder(_status_error(anthropic.BadRequestError, 400), MESSAGE, MESSAGE)
    chat = AnthropicChat(
        "claude", "https://api.anthropic.com", "m", lambda: KEY, client_factory=sdk
    )
    chat.complete(MESSAGES, 64, {"type": "object"})
    chat.complete(MESSAGES, 64, {"type": "object"})
    assert ["output_config" in r for r in sdk.requests] == [True, False, False]
    assert {r["model"] for r in sdk.requests} == {"m"}


@pytest.mark.parametrize(
    ("error", "expected", "outcome"),
    [
        (_status_error(anthropic.AuthenticationError, 401), ProviderAuthError, Outcome.ERROR),
        (_status_error(anthropic.PermissionDeniedError, 403), ProviderAuthError, Outcome.ERROR),
        (_status_error(anthropic.RateLimitError, 429), ProviderUnavailableError,
         Outcome.UNAVAILABLE),
        (_status_error(anthropic.InternalServerError, 529), ProviderUnavailableError,
         Outcome.UNAVAILABLE),
        (_status_error(anthropic.NotFoundError, 404), ProviderRequestError, Outcome.ERROR),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.example")),
         ProviderUnavailableError, Outcome.UNAVAILABLE),
    ],
)  # fmt: skip
def test_sdk_errors_map_to_the_contract(
    error: Exception, expected: type[ModelError], outcome: Outcome
) -> None:
    chat = AnthropicChat(
        "claude", "https://api.anthropic.com", "m", lambda: KEY, client_factory=Recorder(error)
    )
    with pytest.raises(expected) as raised:
        chat.complete(MESSAGES, 64)
    assert raised.value.outcome is outcome
    assert KEY not in str(raised.value) and raised.value.__cause__ is None


def test_the_real_sdk_uses_only_the_key_it_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials in the environment are never used when the key is passed explicitly."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-del-entorno")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-del-entorno")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://otro.example.com")
    client = anthropic.Anthropic(api_key=KEY, base_url="https://api.anthropic.com")
    try:
        headers = {name.lower(): value for name, value in client.auth_headers.items()}
        # One credential, ours, in whichever header this SDK version uses.
        assert len(headers) == 1 and KEY in next(iter(headers.values()))
        assert "del-entorno" not in str(headers)
        assert str(client.base_url).startswith("https://api.anthropic.com")
    finally:
        client.close()
