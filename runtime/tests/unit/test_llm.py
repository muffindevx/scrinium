"""Ollama chat adapter and the local_only policy, against a fake server."""

import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from scrinium.application.ai.policy import RemoteProviderNotAllowedError, require_local
from scrinium.application.ai.service import LlmService
from scrinium.infrastructure.llm.base import (
    MAX_INPUT_CHARS,
    MAX_OUTPUT_TOKENS,
    ChatMessage,
    OutputBudgetExceededError,
)
from scrinium.infrastructure.llm.ollama import OllamaChat
from scrinium.infrastructure.local_http import MAX_RESPONSE_BYTES, ModelError, ModelStatus
from tests.conftest import FakeChat


class _FakeOllama(BaseHTTPRequestHandler):
    received: ClassVar[list[dict[str, Any]]] = []
    reply: ClassVar[dict[str, Any]] = {}
    raw: ClassVar[bytes | None] = None

    def log_message(self, *args: object) -> None:
        pass

    def _send(self, data: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._send(json.dumps({"models": [{"name": "qwen3:4b"}]}).encode())

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/show":
            capabilities = ["completion"] + (["thinking"] if body["model"] == "qwen3:4b" else [])
            self._send(json.dumps({"capabilities": capabilities}).encode())
            return
        type(self).received.append(body)
        self._send(type(self).raw or json.dumps(type(self).reply).encode())


@pytest.fixture
def ollama() -> Iterator[str]:
    _FakeOllama.received = []
    _FakeOllama.raw = None
    _FakeOllama.reply = {
        "message": {"role": "assistant", "content": "respuesta", "thinking": "PENSAMIENTO"},
        "done_reason": "stop",
        "prompt_eval_count": 7,
        "eval_count": 3,
    }
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_request_shape_and_thinking_is_discarded(ollama: str) -> None:
    chat = OllamaChat(ollama, "qwen3:4b")
    completion = chat.complete(
        [ChatMessage("system", "s"), ChatMessage("user", "u")],
        max_tokens=500,
        json_schema={"type": "object"},
    )
    assert completion.text == "respuesta"
    assert "PENSAMIENTO" not in repr(completion)
    assert (completion.prompt_tokens, completion.completion_tokens) == (7, 3)
    assert completion.model_id == "ollama:qwen3:4b"
    sent = _FakeOllama.received[0]
    assert sent["stream"] is False
    assert sent["think"] is True
    assert sent["options"]["num_ctx"] == 12_288
    assert sent["options"]["num_predict"] == 500
    assert sent["format"] == {"type": "object"}
    assert sent["messages"] == [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]


def test_think_is_only_sent_to_models_that_support_it(ollama: str) -> None:
    OllamaChat(ollama, "qwen3:4b-instruct").complete([ChatMessage("user", "u")])
    assert "think" not in _FakeOllama.received[-1]
    OllamaChat(ollama, "qwen3:4b").complete([ChatMessage("user", "u")])
    assert _FakeOllama.received[-1]["think"] is True


def test_inline_think_tags_are_stripped(ollama: str) -> None:
    _FakeOllama.reply = {
        "message": {"content": "<think>x</think>\n  hola  "},
        "done_reason": "stop",
    }
    assert OllamaChat(ollama, "qwen3:4b").complete([ChatMessage("user", "u")]).text == "hola"


def test_running_out_of_tokens_while_thinking_is_reported(ollama: str) -> None:
    _FakeOllama.reply = {"message": {"content": "", "thinking": "..."}, "done_reason": "length"}
    with pytest.raises(OutputBudgetExceededError):
        OllamaChat(ollama, "qwen3:4b").complete([ChatMessage("user", "u")])


@pytest.mark.parametrize(
    "reply",
    [{}, {"message": "x"}, {"message": {"content": 3}}, {"message": {"content": ""}}],
)
def test_malformed_replies_are_errors(ollama: str, reply: dict[str, Any]) -> None:
    _FakeOllama.reply = reply
    with pytest.raises(ModelError):
        OllamaChat(ollama, "qwen3:4b").complete([ChatMessage("user", "u")])


def test_oversized_reply_is_rejected(ollama: str) -> None:
    _FakeOllama.raw = b'{"x": "' + b"a" * MAX_RESPONSE_BYTES + b'"}'
    with pytest.raises(ModelError):
        OllamaChat(ollama, "qwen3:4b").complete([ChatMessage("user", "u")])


def test_input_and_output_limits_are_enforced_before_sending(ollama: str) -> None:
    chat = OllamaChat(ollama, "qwen3:4b")
    with pytest.raises(ValueError):
        chat.complete([ChatMessage("user", "x" * (MAX_INPUT_CHARS + 1))])
    with pytest.raises(ValueError):
        chat.complete([ChatMessage("user", "u")], max_tokens=MAX_OUTPUT_TOKENS + 1)
    with pytest.raises(ValueError):
        chat.complete([])
    assert _FakeOllama.received == []


def test_errors_never_carry_the_conversation() -> None:
    chat = OllamaChat("http://127.0.0.1:9", "qwen3:4b")
    with pytest.raises(ModelError) as raised:
        chat.complete([ChatMessage("user", "texto-confidencial")])
    assert "texto-confidencial" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.parametrize("url", ["http://10.0.0.5:11434", "https://api.example.com"])
def test_chat_refuses_remote_urls(url: str) -> None:
    with pytest.raises(ValueError):
        OllamaChat(url, "qwen3:4b")


def test_policy_refuses_a_remote_provider() -> None:
    with pytest.raises(RemoteProviderNotAllowedError):
        require_local(FakeChat(is_local=False))
    with pytest.raises(RemoteProviderNotAllowedError):
        LlmService(FakeChat(is_local=False))


@pytest.mark.parametrize(
    ("state", "reply", "expected"),
    [
        (ModelStatus.READY, "Listo.", "ok"),
        (ModelStatus.READY, "no se", "failed"),
        (ModelStatus.MODEL_MISSING, "listo", "model_missing"),
        (ModelStatus.UNAVAILABLE, "listo", "unavailable"),
    ],
)
def test_check_uses_a_fixed_prompt(state: ModelStatus, reply: str, expected: str) -> None:
    chat = FakeChat(reply)
    chat.state = state
    assert LlmService(chat).check().status == expected
    for messages in chat.received:
        assert messages == [ChatMessage("user", "Responde solo con la palabra: listo")]


@pytest.mark.skipif(
    os.environ.get("SCRINIUM_TEST_OLLAMA") != "1",
    reason="needs SCRINIUM_TEST_OLLAMA=1 and the model",
)
def test_real_model_answers_without_leaking_its_reasoning() -> None:
    from scrinium.core.config import default_llm_model, default_ollama_url

    chat = OllamaChat(default_ollama_url(), default_llm_model())
    assert LlmService(chat).check().status == "ok"
    completion = chat.complete(
        [
            ChatMessage("system", "Responde en espanol, en una sola frase."),
            ChatMessage("user", "¿Cuanto es 2 + 3? Responde solo con el numero."),
        ],
        max_tokens=1024,
    )
    assert "5" in completion.text
    assert "<think>" not in completion.text
    assert "Okay" not in completion.text  # qwen3's reasoning starts in English


def test_extraction_system_prompt_is_a_fixed_prefix() -> None:
    """T1: the system prompt is the same in every batch, so the server reuses it from
    its cache. The marker value only goes in the user message."""
    import uuid

    from scrinium.application.analysis import extraction

    fragment = extraction.Fragment(1, uuid.uuid4(), "a.py", 1, 2, "if x > 3:\n    raise E")
    first = extraction.build_messages([fragment], "a" * 16)
    second = extraction.build_messages([fragment], "b" * 16)

    assert first[0] == second[0] == ChatMessage("system", extraction.SYSTEM_PROMPT)
    assert "a" * 16 not in first[0].content
    assert first[1].content.startswith(f"CLAVE de las marcas de esta peticion: {'a' * 16}\n")
    assert f"<<FUENTE 1 {'a' * 16}>>" in first[1].content
    assert f"<<FIN {'b' * 16}>>" in second[1].content
