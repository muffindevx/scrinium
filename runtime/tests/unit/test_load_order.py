"""V4: with llama-server, chat loads before the embeddings. Against a fake router."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from scrinium.infrastructure.embeddings.openai_compatible import OpenAICompatibleEmbeddings
from scrinium.infrastructure.llm.base import ChatMessage
from scrinium.infrastructure.llm.load_order import ChatFirst
from scrinium.infrastructure.llm.openai_compatible import OpenAICompatibleChat
from scrinium.infrastructure.local_http import LocalHttpClient

CHAT = "chat"
EMBED = "embed"


class _FakeRouter(BaseHTTPRequestHandler):
    """Like llama-server's router: loads each model with its first request, and unloading
    takes time (the status stays `loaded` for `unload_delay` queries)."""

    states: ClassVar[dict[str, str]] = {}
    calls: ClassVar[list[str]] = []
    load_order: ClassVar[list[str]] = []
    unload_delay: ClassVar[int] = 0
    pending_unload: ClassVar[dict[str, int]] = {}
    broken: ClassVar[bool] = False

    def log_message(self, *args: object) -> None:
        pass

    def _send(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _load(self, model: str) -> None:
        cls = type(self)
        if cls.states.get(model) != "loaded":
            cls.states[model] = "loaded"
            cls.load_order.append(model)

    def do_GET(self) -> None:
        cls = type(self)
        cls.calls.append("GET " + self.path)
        if cls.broken:
            self._send({"error": "roto"}, 500)
            return
        for model, left in list(cls.pending_unload.items()):
            if left <= 0:
                cls.states[model] = "unloaded"
                del cls.pending_unload[model]
            else:
                cls.pending_unload[model] = left - 1
        data = [{"id": m, "status": {"value": s}} for m, s in cls.states.items()]
        self._send({"data": data})

    def do_POST(self) -> None:
        cls = type(self)
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        cls.calls.append("POST " + self.path)
        model = body.get("model")
        if self.path == "/models/unload":
            cls.pending_unload[model] = cls.unload_delay
            self._send({"success": True})
        elif self.path == "/models/load":
            self._load(model)
            self._send({"success": True})
        elif self.path == "/v1/chat/completions":
            self._load(model)
            message = {"role": "assistant", "content": "hola"}
            self._send({"choices": [{"message": message, "finish_reason": "stop"}], "usage": {}})
        elif self.path == "/v1/embeddings":
            self._load(model)
            self._send({"data": [{"index": 0, "embedding": [0.1, 0.2]}]})


@pytest.fixture
def router() -> Iterator[str]:
    _FakeRouter.states = {CHAT: "unloaded", EMBED: "unloaded"}
    _FakeRouter.calls = []
    _FakeRouter.load_order = []
    _FakeRouter.unload_delay = 0
    _FakeRouter.pending_unload = {}
    _FakeRouter.broken = False
    server = HTTPServer(("127.0.0.1", 0), _FakeRouter)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def _clients(url: str) -> tuple[OpenAICompatibleChat, OpenAICompatibleEmbeddings]:
    order = ChatFirst(LocalHttpClient(url), CHAT, EMBED, wait_seconds=5)
    chat = OpenAICompatibleChat(url, CHAT, before_request=order.before_chat)
    embeddings = OpenAICompatibleEmbeddings(url, EMBED, before_request=order.before_embeddings)
    return chat, embeddings


def _ask(chat: OpenAICompatibleChat) -> None:
    chat.complete([ChatMessage("user", "hola")], 16)


def test_embeddings_are_unloaded_before_the_chat_loads(router: str) -> None:
    chat, embeddings = _clients(router)
    embeddings.embed_query("busqueda")
    _FakeRouter.unload_delay = 3  # the router takes time to release the memory
    _ask(chat)
    assert _FakeRouter.load_order == [EMBED, CHAT]
    unload = _FakeRouter.calls.index("POST /models/unload")
    load = _FakeRouter.calls.index("POST /models/load")
    # Between unloading and loading, wait until the status says the memory is free.
    assert _FakeRouter.calls[unload + 1 : load].count("GET /models") >= 3


def test_embeddings_come_back_after_the_chat(router: str) -> None:
    chat, embeddings = _clients(router)
    embeddings.embed_query("busqueda")
    _ask(chat)
    embeddings.embed_query("otra")
    assert _FakeRouter.load_order == [EMBED, CHAT, EMBED]
    assert _FakeRouter.states == {CHAT: "loaded", EMBED: "loaded"}


def test_nothing_is_unloaded_once_the_chat_is_loaded(router: str) -> None:
    chat, embeddings = _clients(router)
    _ask(chat)
    embeddings.embed_query("busqueda")
    _ask(chat)
    _ask(chat)
    assert "POST /models/unload" not in _FakeRouter.calls
    assert _FakeRouter.load_order == [CHAT, EMBED]


def test_chat_alone_is_loaded_without_touching_embeddings(router: str) -> None:
    chat, _ = _clients(router)
    _ask(chat)
    assert "POST /models/unload" not in _FakeRouter.calls
    assert _FakeRouter.load_order == [CHAT]


def test_a_router_that_fails_does_not_stop_the_chat(router: str) -> None:
    chat, _ = _clients(router)
    _FakeRouter.broken = True
    _ask(chat)  # no exception: the request loads the model on its own
    assert _FakeRouter.load_order == [CHAT]
