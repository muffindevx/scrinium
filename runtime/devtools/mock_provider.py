"""A fake model provider for trying the provider flow end to end, with no real connection.

Development only: it is not in the product, its provider list or its console. It serves the
OpenAI-compatible API Scrinium calls (`/v1/models`, `/v1/chat/completions`, `/v1/embeddings`)
over HTTPS at https://mock-provider.localhost:<port>/v1, with a certificate made on the fly.
Scrinium only registers HTTPS providers on a named host, so it is registered like any real one;
start Scrinium with SSL_CERT_FILE pointing at the certificate this prints, so it trusts it.

    uv run python devtools/mock_provider.py [--port 8443] [--dir <folder>]

Then, in Settings > AI models, add an OpenAI-compatible provider:
    base URL  https://mock-provider.localhost:8443/v1
    key       any text of 20 or more characters (it is checked to be present, nothing more)
    model     mock-chat (answers) or mock-embed (search)

Answers: rule extraction gets one made-up rule per decision it is asked about (D1, D2...), so
rules appear in the console; any other request gets a short fixed answer. Embeddings are
deterministic vectors from a hash of the text: search works, it just is not meaningful.
"""

import argparse
import hashlib
import json
import math
import re
import ssl
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socket import AF_INET6
from typing import Any, Final

HOST: Final = "mock-provider.localhost"
CHAT_MODEL: Final = "mock-chat"
EMBED_MODEL: Final = "mock-embed"
DIMENSIONS: Final = 1024
_DECISION: Final = re.compile(r"\bD(\d+)\b")


def _certificate(folder: Path) -> tuple[Path, Path]:
    cert, key = folder / "mock-provider.pem", folder / "mock-provider-key.pem"
    if not cert.exists():
        command = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "30",
            "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={HOST}",
            "-addext", f"subjectAltName=DNS:{HOST}",
        ]  # fmt: skip
        # openssl from PATH: a development tool, never part of the product.
        subprocess.run(command, check=True, capture_output=True)  # noqa: S603
    return cert, key


def _reply(body: dict[str, Any]) -> str:
    """Rule extraction, both phases, by the JSON schema and the prompt; anything else, text."""
    schema = (body.get("response_format") or {}).get("json_schema", {}).get("schema", {})
    statements = schema.get("properties", {}).get("s", {}).get("prefixItems")
    if statements:  # phase 2: one statement per rule the schema asks for
        answers = []
        for item in statements:
            ref = item["properties"]["i"]["const"]
            answer = {"i": ref, "s": f"Enunciado de prueba de {ref}."}
            if "t" in item["properties"]:
                answer["t"] = f"Regla de prueba {ref}"
            answers.append(answer)
        return json.dumps({"s": answers})
    text = "\n".join(str(message.get("content", "")) for message in body.get("messages", []))
    decisions = sorted({int(number) for number in _DECISION.findall(text)})
    if decisions:  # phase 1: one made-up rule per decision
        return json.dumps(
            {
                "d": [
                    {
                        "i": f"D{n}",
                        "r": {
                            "t": f"Regla de prueba {n}",
                            "s": "Regla inventada por el proveedor de prueba.",
                            "c": "cal",
                        },
                    }
                    for n in decisions
                ]
            }
        )
    return "Respuesta del proveedor de prueba."


def _vector(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    raw = [(seed[i % len(seed)] - 127.5) * (1 + i % 7) for i in range(DIMENSIONS)]
    norm = math.sqrt(sum(v * v for v in raw)) or 1.0
    return [v / norm for v in raw]


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        key = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if len(key) < 20:
            self._send(401, {"error": {"type": "invalid_api_key", "message": "missing key"}})
            return False
        return True

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path.rstrip("/") == "/v1/models":
            models = [{"id": name, "object": "model"} for name in (CHAT_MODEL, EMBED_MODEL)]
            self._send(200, {"object": "list", "data": models})
        else:
            self._send(404, {"error": {"type": "not_found", "message": self.path}})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if self.path == "/v1/chat/completions":
            content = _reply(body)
            self._send(
                200,
                {
                    "id": "mock",
                    "object": "chat.completion",
                    "model": body.get("model", CHAT_MODEL),
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                },
            )
        elif self.path == "/v1/embeddings":
            inputs = body.get("input", [])
            texts = [inputs] if isinstance(inputs, str) else list(inputs)
            data = [
                {"object": "embedding", "index": i, "embedding": _vector(text)}
                for i, text in enumerate(texts)
            ]
            self._send(200, {"object": "list", "data": data, "model": body.get("model")})
        else:
            self._send(404, {"error": {"type": "not_found", "message": self.path}})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"mock-provider: {format % args}")


class _DualStack(ThreadingHTTPServer):
    address_family = AF_INET6  # mock-provider.localhost resolves to ::1 first, then 127.0.0.1


def serve(port: int, folder: Path) -> ThreadingHTTPServer:
    """Starts serving on a thread-friendly server; the caller runs `serve_forever`."""
    cert, key = _certificate(folder)
    server = _DualStack(("::", port), _Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--dir", type=Path, default=Path(tempfile.gettempdir()) / "scrinium-mock")
    args = parser.parse_args()
    args.dir.mkdir(parents=True, exist_ok=True)
    server = serve(args.port, args.dir)
    print(f"Mock provider at https://{HOST}:{args.port}/v1")
    print(f"Start Scrinium with: SSL_CERT_FILE={args.dir / 'mock-provider.pem'} scrinium start")
    print(f"Models: {CHAT_MODEL} (answers), {EMBED_MODEL} (search). Any key of 20+ characters.")
    server.serve_forever()


if __name__ == "__main__":
    main()
