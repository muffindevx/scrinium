"""The HTTPS client for remote providers (ADR-010): no request leaves this machine here.

A fake HTTPS handler answers inside urllib, so the real opener chain runs: redirects, error
statuses and headers behave as they would against a provider.
"""

import io
import json
import urllib.request
from email.message import Message
from typing import Any

import pytest

from scrinium.infrastructure.llm.base import ChatMessage
from scrinium.infrastructure.llm.remote_openai import RemoteOpenAIChat
from scrinium.infrastructure.local_http import ModelError, Outcome, ProviderUnavailableError
from scrinium.infrastructure.remote_http import (
    ProviderAuthError,
    ProviderRequestError,
    RemoteHttpsClient,
    _NoRedirect,
    check_https_url,
)

KEY = "sk-remoto-0123456789"
ANSWER = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
          "usage": {"prompt_tokens": 5, "completion_tokens": 3}}  # fmt: skip


class FakeHttps(urllib.request.BaseHandler):
    """Answers every HTTPS request with the next scripted (status, headers, body). It runs
    before urllib's own HTTPS handler, so nothing reaches the network."""

    handler_order = 100

    def __init__(self, *replies: tuple[int, dict[str, str], Any]) -> None:
        self.replies = list(replies)
        self.seen: list[urllib.request.Request] = []

    def https_open(self, request: urllib.request.Request) -> Any:
        self.seen.append(request)
        status, headers, body = self.replies.pop(0)
        message = Message()
        for name, value in headers.items():
            message[name] = value
        response = urllib.response.addinfourl(
            io.BytesIO(json.dumps(body).encode()), message, request.full_url, status
        )
        response.msg = "x"  # type: ignore[attr-defined]
        return response


def _client(handler: FakeHttps) -> RemoteHttpsClient:
    opener = urllib.request.build_opener(handler, _NoRedirect())
    return RemoteHttpsClient(
        "https://api.example.com/v1", lambda: {"Authorization": f"Bearer {KEY}"}, opener
    )


def test_the_key_goes_in_each_request_to_its_host_only() -> None:
    handler = FakeHttps((200, {}, ANSWER))
    assert _client(handler).request("/chat/completions", {"x": 1}, 5) == ANSWER
    sent = handler.seen[0]
    assert sent.full_url == "https://api.example.com/v1/chat/completions"
    assert sent.get_header("Authorization") == f"Bearer {KEY}"


def test_a_redirect_is_not_followed() -> None:
    """Following it would carry the key to the host the redirect names."""
    handler = FakeHttps((302, {"Location": "https://evil.example.org/steal"}, {}))
    with pytest.raises(ProviderRequestError) as raised:
        _client(handler).request("/chat/completions", {"x": 1}, 5)
    assert raised.value.status == 302
    assert [request.host for request in handler.seen] == ["api.example.com"]


@pytest.mark.parametrize(
    ("status", "error", "outcome"),
    [
        (401, ProviderAuthError, Outcome.ERROR),
        (403, ProviderAuthError, Outcome.ERROR),
        (429, ProviderUnavailableError, Outcome.UNAVAILABLE),
        (503, ProviderUnavailableError, Outcome.UNAVAILABLE),
        (529, ProviderUnavailableError, Outcome.UNAVAILABLE),
        (400, ProviderRequestError, Outcome.ERROR),
    ],
)
def test_statuses_map_to_the_contract(
    status: int, error: type[ModelError], outcome: Outcome
) -> None:
    body = {"error": {"message": f"clave {KEY} rechazada"}}
    with pytest.raises(error) as raised:
        _client(FakeHttps((status, {}, body))).request("/chat/completions", {"x": 1}, 5)
    assert raised.value.outcome is outcome
    # Never the body, which may quote the request or the key.
    assert KEY not in str(raised.value) and raised.value.__cause__ is None


@pytest.mark.parametrize(
    "url", ["http://api.example.com", "https://u:p@api.example.com", "ftp://x.example.com"]
)
def test_only_https_without_credentials_in_the_url(url: str) -> None:
    with pytest.raises(ValueError):
        check_https_url(url)


def test_a_provider_without_json_schema_gets_json_mode_on_the_same_model() -> None:
    handler = FakeHttps(
        (400, {}, {"error": "response_format"}), (200, {}, ANSWER), (200, {}, ANSWER)
    )
    chat = RemoteOpenAIChat(
        "deepseek", "https://api.example.com", "m", lambda: KEY, _client(handler)
    )
    schema = {"type": "object"}
    messages = [ChatMessage("user", "hola")]
    assert chat.complete(messages, 64, schema).text == '{"ok": true}'
    chat.complete(messages, 64, schema)
    formats = [json.loads(request.data)["response_format"]["type"] for request in handler.seen]  # type: ignore[arg-type]
    # Asked once with the schema; from then on, JSON mode. Always the same model.
    assert formats == ["json_schema", "json_object", "json_object"]
    assert {json.loads(request.data)["model"] for request in handler.seen} == {"m"}  # type: ignore[arg-type]
    assert not chat.is_local and chat.model_id == "deepseek:m"


def test_status_never_spends_tokens() -> None:
    handler = FakeHttps()
    chat = RemoteOpenAIChat("glm", "https://api.example.com", "m", lambda: KEY, _client(handler))
    chat.status()
    assert handler.seen == []
