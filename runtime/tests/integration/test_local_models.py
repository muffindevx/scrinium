"""Local models through the OpenAI-compatible API, and llama-server managed by Scrinium.

The adapters are tested against a real HTTP server on loopback; the manager, with a
fake executable that behaves like llama-server. With SCRINIUM_TEST_LLAMA=1, also against the
real llama-server and models, installed with `scrinium tools install`.
"""

import json
import os
import socket
import stat
import sys
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any, ClassVar

import pytest

from scrinium.infrastructure.embeddings.base import QUERY_INSTRUCTION
from scrinium.infrastructure.embeddings.openai_compatible import OpenAICompatibleEmbeddings
from scrinium.infrastructure.llama_server import CHAT_UBATCH, LlamaServer, ServedModel, presets
from scrinium.infrastructure.llm.base import ChatMessage, OutputBudgetExceededError
from scrinium.infrastructure.llm.openai_compatible import OpenAICompatibleChat
from scrinium.infrastructure.local_http import ModelError, ModelStatus
from scrinium.infrastructure.tools.manifest import CHAT_MODEL, EMBEDDING_MODEL

KEY = "clave-de-prueba"


class _FakeOpenAI(BaseHTTPRequestHandler):
    received: ClassVar[list[tuple[str, dict[str, Any], str | None]]] = []
    replies: ClassVar[dict[str, Any]] = {}

    def _reply(self, status: int, body: Any) -> None:
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {KEY}":
            return True
        self._reply(401, {"error": {"message": "Invalid API Key"}})
        return False

    def do_GET(self) -> None:
        if self._authorized():
            self._reply(200, self.replies["/v1/models"])

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.received.append((self.path, body, self.headers.get("Authorization")))
        if self._authorized():
            self._reply(200, self.replies[self.path])

    def log_message(self, *_args: object) -> None:
        pass


def _chat_reply(content: str, finish: str = "stop", **message: Any) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content, **message},
             "finish_reason": finish}
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3},
    }  # fmt: skip


@pytest.fixture
def server() -> Iterator[str]:
    _FakeOpenAI.received = []
    _FakeOpenAI.replies = {
        "/v1/models": {"data": [{"id": CHAT_MODEL.name}, {"id": EMBEDDING_MODEL.name}]},
        "/v1/chat/completions": _chat_reply("hola"),
        "/v1/embeddings": {
            "data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1, 0]}]
        },
    }
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOpenAI)
    Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


def test_chat_request_shape_and_reply(server: str) -> None:
    chat = OpenAICompatibleChat(server, CHAT_MODEL.name, KEY)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}}

    completion = chat.complete([ChatMessage("system", "s"), ChatMessage("user", "u")], 64, schema)

    assert (completion.text, completion.prompt_tokens, completion.completion_tokens) == (
        "hola", 11, 3,
    )  # fmt: skip
    assert completion.model_id == chat.model_id == "llama:qwen3-4b-instruct-2507"
    path, body, authorization = _FakeOpenAI.received[0]
    assert path == "/v1/chat/completions"
    assert authorization == f"Bearer {KEY}"
    assert body["model"] == CHAT_MODEL.name
    assert body["max_tokens"] == 64
    assert body["messages"] == [
        {"role": "system", "content": "s"}, {"role": "user", "content": "u"}
    ]  # fmt: skip
    assert body["response_format"]["json_schema"]["schema"] == schema
    # Prompt cache and always the same slot: the shared start is not read again.
    assert body["cache_prompt"] is True
    assert body["id_slot"] == 0
    assert completion.cached_tokens == 0


@pytest.mark.parametrize(
    ("extra", "cached"),
    [
        # OpenAI and recent llama-server.
        ({"usage": {"prompt_tokens": 11, "completion_tokens": 3,
                    "prompt_tokens_details": {"cached_tokens": 8}}}, 8),
        # llama-server that only reports it in its timings.
        ({"timings": {"cache_n": 5}}, 5),
    ],
)  # fmt: skip
def test_tokens_reused_from_the_cache_are_reported(
    server: str, extra: dict[str, Any], cached: int
) -> None:
    _FakeOpenAI.replies["/v1/chat/completions"] = {**_chat_reply("hola"), **extra}
    completion = OpenAICompatibleChat(server, CHAT_MODEL.name, KEY).complete(
        [ChatMessage("user", "u")]
    )
    assert completion.cached_tokens == cached


def test_reasoning_is_discarded(server: str) -> None:
    _FakeOpenAI.replies["/v1/chat/completions"] = _chat_reply(
        "<think>secreto</think>respuesta", reasoning_content="pensando en voz alta"
    )
    text = OpenAICompatibleChat(server, CHAT_MODEL.name, KEY).complete([ChatMessage("user", "u")])
    assert text.text == "respuesta"


def test_running_out_of_tokens_is_reported(server: str) -> None:
    _FakeOpenAI.replies["/v1/chat/completions"] = _chat_reply("", finish="length")
    with pytest.raises(OutputBudgetExceededError):
        OpenAICompatibleChat(server, CHAT_MODEL.name, KEY).complete([ChatMessage("user", "u")])


@pytest.mark.parametrize(
    "reply", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}, _chat_reply("")]
)
def test_malformed_chat_replies_are_errors(server: str, reply: dict[str, Any]) -> None:
    _FakeOpenAI.replies["/v1/chat/completions"] = reply
    with pytest.raises(ModelError):
        OpenAICompatibleChat(server, CHAT_MODEL.name, KEY).complete([ChatMessage("user", "u")])


def test_status_needs_the_key_and_the_model(server: str) -> None:
    assert OpenAICompatibleChat(server, CHAT_MODEL.name, KEY).status() == ModelStatus.READY
    assert OpenAICompatibleChat(server, "otro", KEY).status() == ModelStatus.MODEL_MISSING
    assert OpenAICompatibleChat(server, CHAT_MODEL.name, "mala").status() == (
        ModelStatus.UNAVAILABLE
    )


def test_without_a_server_models_are_unavailable() -> None:
    chat = OpenAICompatibleChat(None, CHAT_MODEL.name)
    embeddings = OpenAICompatibleEmbeddings(None, EMBEDDING_MODEL.name)
    assert chat.status() == embeddings.status() == ModelStatus.UNAVAILABLE
    with pytest.raises(ModelError):
        chat.complete([ChatMessage("user", "u")])
    with pytest.raises(ModelError):
        embeddings.embed_query("q")


def test_embeddings_follow_the_index_and_instruct_queries(server: str) -> None:
    embeddings = OpenAICompatibleEmbeddings(server, EMBEDDING_MODEL.name, KEY)

    assert embeddings.embed_documents(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    _, body, _ = _FakeOpenAI.received[-1]
    assert body["input"] == ["a", "b"]

    _FakeOpenAI.replies["/v1/embeddings"] = {"data": [{"index": 0, "embedding": [0.5]}]}
    assert embeddings.embed_query("q") == [0.5]
    assert _FakeOpenAI.received[-1][1]["input"] == [QUERY_INSTRUCTION + "q"]


@pytest.mark.parametrize(
    "reply",
    [
        {"data": [{"index": 0, "embedding": [1.0]}]},  # one missing
        {"data": [{"index": 0, "embedding": [1.0]}, {"index": 0, "embedding": [1.0]}]},
        {"data": [{"index": 0, "embedding": ["x"]}, {"index": 1, "embedding": [1.0]}]},
        {"data": "no"},
    ],
)
def test_malformed_embeddings_are_errors(server: str, reply: dict[str, Any]) -> None:
    _FakeOpenAI.replies["/v1/embeddings"] = reply
    with pytest.raises(ModelError):
        OpenAICompatibleEmbeddings(server, EMBEDDING_MODEL.name, KEY).embed_documents(["a", "b"])


# A fake llama-server: reads the INI and answers /health and /v1/models only with the
# environment key. It records what it received so the test can check it.
_FAKE_LLAMA = """\
import configparser, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

args = sys.argv[1:]
option = lambda name: args[args.index(name) + 1]
presets = configparser.ConfigParser()
presets.read(option("--models-preset"))
key = os.environ.get("LLAMA_API_KEY")
with open("recibido.json", "w") as out:
    json.dump({"args": args, "key": key, "cwd": os.getcwd()}, out)
if os.environ.get("FAKE_LLAMA_FAIL"):
    sys.exit(1)

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/health" and self.headers.get("Authorization") != "Bearer " + key:
            self.send_response(401); self.end_headers(); return
        body = json.dumps({"data": [{"id": name} for name in presets.sections()]}).encode()
        self.send_response(200); self.end_headers(); self.wfile.write(body)
    def log_message(self, *a):
        pass

HTTPServer((option("--host"), int(option("--port"))), Handler).serve_forever()
"""


def _fake_llama(tmp_path: Path) -> Path:
    executable = tmp_path / "llama-server"
    executable.write_text(f"#!{sys.executable}\n{_FAKE_LLAMA}")
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def _served(tmp_path: Path) -> list[ServedModel]:
    return [
        ServedModel(CHAT_MODEL, tmp_path / "chat.gguf"),
        ServedModel(EMBEDDING_MODEL, tmp_path / "embed.gguf", embeddings=True),
    ]


@pytest.mark.skipif(sys.platform == "win32", reason="fake executable with a shebang")
def test_managed_server_is_private_and_stops(tmp_path: Path) -> None:
    workdir = tmp_path / "llama"
    llama = LlamaServer(_fake_llama(tmp_path), _served(tmp_path), workdir)
    assert llama.start()
    try:
        assert llama.url is not None
        assert llama.url.startswith("http://127.0.0.1:")
        received = json.loads((workdir / "recibido.json").read_text())
        # The key goes through the environment, never on the command line.
        assert received["key"] == llama.api_key
        assert llama.api_key not in " ".join(received["args"])
        assert "--offline" in received["args"]
        assert stat.S_IMODE((workdir / "presets.ini").stat().st_mode) == 0o600
        assert stat.S_IMODE(workdir.stat().st_mode) == 0o700

        chat = OpenAICompatibleChat(llama.url, CHAT_MODEL.name, llama.api_key)
        embeddings = OpenAICompatibleEmbeddings(llama.url, EMBEDDING_MODEL.name, llama.api_key)
        assert chat.status() == embeddings.status() == ModelStatus.READY
        assert OpenAICompatibleChat(llama.url, CHAT_MODEL.name).status() == (
            ModelStatus.UNAVAILABLE
        )
        port = int(llama.url.rsplit(":", 1)[1])
    finally:
        llama.stop()
    assert llama.url is None
    with pytest.raises(OSError), socket.create_connection(("127.0.0.1", port), timeout=1):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="fake executable with a shebang")
def test_a_server_that_does_not_start_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LLAMA_FAIL", "1")
    llama = LlamaServer(_fake_llama(tmp_path), _served(tmp_path), tmp_path / "llama")
    assert not llama.start()
    assert llama.url is None


def test_presets_fix_each_model_context(tmp_path: Path) -> None:
    ini = presets(_served(tmp_path))
    chat, embed = ini.split("\n\n")
    assert chat.startswith("[qwen3-4b-instruct-2507]")
    assert "ctx-size = 12288" in chat
    assert "embeddings" not in chat
    assert embed.startswith("[qwen3-embedding-0.6b]")
    assert "embeddings = true" in embed
    assert "ubatch-size = 8192" in embed


def test_presets_limit_model_memory_and_cpu(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(os, "cpu_count", lambda: 12)
    chat, embed = presets(_served(tmp_path)).split("\n\n")
    assert "threads = 6" in chat
    assert "threads = 6" in embed
    assert "cache-type-k = q8_0" in chat
    assert "cache-type-v = q8_0" in chat
    assert "cache-type" not in embed
    # Without this, llama-server keeps up to 8 GiB of prompts in RAM.
    assert "cache-ram = 512" in chat and "cache-ram = 0" in embed
    # V5 removed (it accepted 4-8 %); V9: reading in fixed blocks for chat.
    assert "spec-type" not in chat
    assert f"ubatch-size = {CHAT_UBATCH}" in chat
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert "threads = 1" in presets(_served(tmp_path))


@pytest.mark.skipif(
    os.environ.get("SCRINIUM_TEST_LLAMA") != "1",
    reason="needs SCRINIUM_TEST_LLAMA=1 and `scrinium tools install` in SCRINIUM_DATA_DIR",
)
def test_real_llama_server_answers_and_embeds() -> None:
    from scrinium.cli.main import start_llama_server
    from scrinium.core.config import default_data_dir

    llama = start_llama_server(default_data_dir())
    assert llama is not None
    try:
        embeddings = OpenAICompatibleEmbeddings(llama.url, EMBEDDING_MODEL.name, llama.api_key)
        query = embeddings.embed_query("como se guarda la sesion")
        near, far = embeddings.embed_documents(
            ["La sesion se guarda en una cookie firmada.", "Receta de pan con masa madre."]
        )
        assert len(query) == 1024
        assert sum(a * b for a, b in zip(query, near, strict=True)) > sum(
            a * b for a, b in zip(query, far, strict=True)
        )
        chat = OpenAICompatibleChat(llama.url, CHAT_MODEL.name, llama.api_key)
        schema = {
            "type": "object",
            "properties": {"capital": {"type": "string"}},
            "required": ["capital"],
        }
        reply = chat.complete(
            [ChatMessage("user", "Capital de Francia, en JSON.")], max_tokens=64, json_schema=schema
        )
        assert "par" in json.loads(reply.text)["capital"].lower()
    finally:
        llama.stop()
