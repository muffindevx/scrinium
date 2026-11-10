"""Chunking, FTS5 queries and the Ollama provider, without a database."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, ClassVar

import pytest

from scrinium.application.retrieval.chunking import MAX_LINES, chunk_text
from scrinium.application.retrieval.search import fts_query
from scrinium.infrastructure.embeddings.ollama import OllamaEmbeddings
from scrinium.infrastructure.local_http import ModelError, ModelStatus, check_loopback_url


def _assert_exact(text: str, chunks: Any) -> None:
    """Each chunk is exactly its lines: the citation cannot drift."""
    lines = text.splitlines()
    for chunk in chunks:
        assert chunk.text == "\n".join(lines[chunk.start_line - 1 : chunk.end_line])


def test_markdown_is_split_by_heading_but_not_inside_code() -> None:
    text = "intro\n# Uno\na\n```\n# no es titulo\n```\n## Dos\nb\n"
    chunks = chunk_text("docs/guia.md", text)
    assert [(c.heading, c.start_line, c.end_line) for c in chunks] == [
        (None, 1, 1),
        ("Uno", 2, 6),
        ("Dos", 7, 8),
    ]
    _assert_exact(text, chunks)


def test_code_is_split_in_blocks_that_prefer_blank_lines() -> None:
    lines = [f"linea {n}" if n % 70 else "" for n in range(1, 301)]
    text = "\n".join(lines)
    chunks = chunk_text("src/app.py", text)
    assert all(c.end_line - c.start_line + 1 <= MAX_LINES for c in chunks)
    assert chunks[0].end_line == 70  # cuts at the blank line
    assert chunks[-1].end_line == 300
    starts = [c.start_line for c in chunks]
    ends = [c.end_line for c in chunks]
    assert starts[1:] == [end + 1 for end in ends[:-1]]  # no gaps or overlaps
    _assert_exact(text, chunks)


def test_very_long_lines_are_split_by_size() -> None:
    text = "\n".join("x" * 500 for _ in range(40))
    chunks = chunk_text("data.json", text)
    assert len(chunks) > 1
    assert all(len(c.text) <= 6000 for c in chunks)
    _assert_exact(text, chunks)


def test_empty_and_blank_files_have_no_chunks() -> None:
    assert chunk_text("a.md", "") == []
    assert chunk_text("a.py", "\n\n   \n") == []


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("calcular total", '{path body} : ("calcular" AND "total")'),
        (
            'chunk_id:abc OR body:"x',
            '{path body} : ("chunk_id" AND "abc" AND "OR" AND "body" AND "x")',
        ),
        ("NEAR(a b) * ^c", '{path body} : ("NEAR" AND "a" AND "b" AND "c")'),
        ("camión", '{path body} : ("camión")'),
        ('"" () * - :', None),
    ],
)
def test_fts_query_only_quotes_words(query: str, expected: str | None) -> None:
    assert fts_query(query, "AND") == expected


def test_or_fallback_drops_tiny_words() -> None:
    assert fts_query("si me roban el portátil", "OR") == '{path body} : ("roban" OR "portátil")'
    assert fts_query("a b", "OR") == '{path body} : ("a" OR "b")'


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:11434",
        "http://localhost:11434/",
        "http://[::1]:11434",
        "http://127.0.0.2:1",
    ],
)
def test_loopback_urls_are_accepted(url: str) -> None:
    assert check_loopback_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://192.168.1.10:11434",
        "https://127.0.0.1:11434",
        "http://ollama.example.com",
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434/proxy",
        "http://127.0.0.1.example.com",
        "file:///etc/passwd",
    ],
)
def test_remote_or_odd_urls_are_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        check_loopback_url(url)
    with pytest.raises(ValueError):
        OllamaEmbeddings(url, "modelo")


class _FakeOllama(BaseHTTPRequestHandler):
    received: ClassVar[list[dict[str, Any]]] = []
    fail = False

    def log_message(self, *args: object) -> None:
        pass

    def _reply(self, body: object, status: int = 200) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self._reply({"models": [{"name": "modelo:latest"}]})

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).received.append(body)
        if type(self).fail:
            self._reply({"error": "boom " + body["input"][0]}, 500)
            return
        self._reply({"embeddings": [[0.5, 0.5] for _ in body["input"]]})


@pytest.fixture
def fake_ollama() -> Iterator[str]:
    _FakeOllama.received = []
    _FakeOllama.fail = False
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_ollama_round_trip_ignores_proxies(
    fake_ollama: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A proxy in the environment must not receive the text: it points to a closed port.
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    provider = OllamaEmbeddings(fake_ollama, "modelo")
    assert provider.status() is ModelStatus.READY
    assert provider.embed_documents(["a", "b"]) == [[0.5, 0.5], [0.5, 0.5]]
    provider.embed_query("pregunta")
    assert _FakeOllama.received[0] == {"model": "modelo", "input": ["a", "b"], "truncate": True}
    assert _FakeOllama.received[1]["input"][0].endswith("Query: pregunta")


def test_missing_model_and_unreachable_server(fake_ollama: str) -> None:
    assert OllamaEmbeddings(fake_ollama, "otro").status() is ModelStatus.MODEL_MISSING
    assert OllamaEmbeddings("http://127.0.0.1:9", "m").status() is ModelStatus.UNAVAILABLE


def test_errors_never_carry_the_text(fake_ollama: str) -> None:
    _FakeOllama.fail = True
    with pytest.raises(ModelError) as raised:
        OllamaEmbeddings(fake_ollama, "modelo").embed_documents(["texto-confidencial"])
    assert "texto-confidencial" not in str(raised.value)
    assert raised.value.__cause__ is None
