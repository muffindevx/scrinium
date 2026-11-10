"""The content sent to the model only reaches whoever asked with `attach --contenido`."""

import json
import logging
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from scrinium.core.log_hub import HUB, Subscription
from scrinium.core.logging import configure_logging
from scrinium.infrastructure.local_http import LocalHttpClient


class _Echo(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers["Content-Length"]))
        body = json.dumps({"respuesta": "el total suma las lineas"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
    Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()


def _drain(subscription: Subscription) -> str:
    lines: list[str] = []
    while not subscription.lines.empty():
        line = subscription.lines.get_nowait()
        if line is not None:
            lines.append(line)
    return "\n".join(lines)


def test_content_reaches_only_content_subscribers(
    server: str, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging(verbose=True)
    plain = HUB.subscribe(content=False)
    full = HUB.subscribe(content=True)
    try:
        prompt = {
            "prompt": "¿Cómo se calcula el total? token=ghp_abcdefghijklmnopqrstuvwxyz0123456789"
        }
        LocalHttpClient(server).request("/v1/chat/completions", prompt, 5)
        logging.getLogger("scrinium.prueba").debug("detalle de depuracion")
    finally:
        HUB.unsubscribe(plain)
        HUB.unsubscribe(full)

    seen_full = _drain(full)
    assert "-> /v1/chat/completions" in seen_full and "¿Cómo se calcula el total?" in seen_full
    assert "<- /v1/chat/completions" in seen_full and "el total suma las lineas" in seen_full
    # Secrets are hidden even in content mode.
    assert "ghp_abcdefghijklmnopqrstuvwxyz" not in seen_full

    seen_plain = _drain(plain)
    assert "detalle de depuracion" in seen_plain
    assert "calcula el total" not in seen_plain
    # The normal terminal never shows content, not even with --verbose.
    assert "calcula el total" not in capsys.readouterr().err


def test_logging_goes_back_to_normal_when_nobody_is_attached(server: str) -> None:
    configure_logging(verbose=False)
    subscription = HUB.subscribe(content=True)
    assert logging.getLogger().level == logging.DEBUG
    assert logging.getLogger("sqlalchemy").level == logging.WARNING
    HUB.unsubscribe(subscription)
    assert logging.getLogger().level == logging.WARNING
    assert HUB not in logging.getLogger().handlers
    assert not HUB.wants_content()
