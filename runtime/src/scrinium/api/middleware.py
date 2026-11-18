"""HTTP boundaries of the local runtime. Anything not explicitly allowed is rejected."""

import hashlib
import hmac
import logging
import time
import uuid
from collections.abc import Mapping
from typing import Final

from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from scrinium.core.config import RuntimeConfig
from scrinium.core.logging import safe_log_value
from scrinium.core.session import LocalSessionManager

access_logger = logging.getLogger("scrinium.access")
security_logger = logging.getLogger("scrinium.security")

SESSION_COOKIE: Final = "scrinium_session"
CSRF_HEADER: Final = "x-csrf-token"
CONTROL_HEADER: Final = "x-scrinium-control"
CONTROL_PREFIX: Final = "/control/"

_SAFE_METHODS: Final = frozenset({"GET", "HEAD"})
_PUBLIC_API_PATHS: Final = frozenset({"/health", "/ready", "/auth/launch"})

_SECURITY_HEADERS: Final[Mapping[str, str]] = {
    "Content-Security-Policy": (
        "default-src 'self'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'; object-src 'none'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def _is_api_path(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


class RequestContextMiddleware:
    """Own request ID, security headers, metadata log and errors without details."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # The client's ID is ignored: an external value could forge the log trail.
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        path = scope["path"]
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_headers(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
                headers = MutableHeaders(scope=message)
                for name, value in _SECURITY_HEADERS.items():
                    headers[name] = value
                headers["X-Request-ID"] = request_id
                if _is_api_path(path) or path in _PUBLIC_API_PATHS:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception as error:
            # Type only: the message or traceback may contain code or documents.
            security_logger.error(
                "unhandled_error request_id=%s type=%s", request_id, type(error).__name__
            )
            if not response_started:
                response = JSONResponse(
                    {"error": "internal_error", "request_id": request_id}, status_code=500
                )
                await response(scope, receive, send_with_headers)
        finally:
            # The query string is never logged: the launch token travels in it.
            access_logger.info(
                "request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
                request_id,
                safe_log_value(scope["method"]),
                safe_log_value(path),
                status,
                (time.perf_counter() - started) * 1000,
            )


class LocalGuardMiddleware:
    """Validates Host and Origin, and requires a session and CSRF by default.

    New routes are protected without doing anything: what must be declared is the exception.
    """

    def __init__(self, app: ASGIApp, config: RuntimeConfig, sessions: LocalSessionManager) -> None:
        self.app = app
        self.config = config
        self.sessions = sessions

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return

        rejection = self._check(scope)
        if rejection is not None:
            status, reason = rejection
            state = scope.get("state", {})
            security_logger.warning(
                "request_rejected request_id=%s reason=%s method=%s path=%s",
                state.get("request_id", "-"),
                reason,
                safe_log_value(scope["method"]),
                safe_log_value(scope["path"]),
            )
            await JSONResponse({"error": reason}, status_code=status)(scope, receive, send)
            return
        await self.app(scope, receive, send)

    def _check(self, scope: Scope) -> tuple[int, str] | None:
        headers = Headers(scope=scope)

        # Exact Host: stops DNS rebinding, where a foreign domain points to 127.0.0.1.
        if headers.getlist("host") != [self.config.allowed_host]:
            return 403, "invalid_host"

        origins = headers.getlist("origin")
        if scope["path"].startswith(CONTROL_PREFIX):
            return self._check_control(headers, origins)
        if origins and origins != [self.config.allowed_origin]:
            return 403, "invalid_origin"

        method = scope["method"]
        path = scope["path"]
        mutating = method not in _SAFE_METHODS

        if not mutating and (not _is_api_path(path) or path in _PUBLIC_API_PATHS):
            return None
        if mutating and not origins:
            return 403, "origin_required"

        cookies = cookie_parser(headers.get("cookie", ""))
        session = self.sessions.validate(cookies.get(SESSION_COOKIE))
        if session is None:
            return 401, "session_required"
        scope.setdefault("state", {})["session"] = session

        if mutating and not self.sessions.csrf_matches(session, headers.get(CSRF_HEADER)):
            return 403, "csrf_invalid"
        return None

    def _check_control(self, headers: Headers, origins: list[str]) -> tuple[int, str] | None:
        """Command line only: with the instance-registry key and no Origin.

        A browser always sends Origin on a POST, and cannot read the key from disk.
        """
        expected = self.config.control_token
        if expected is None:
            return 404, "not_found"
        if origins:
            return 403, "invalid_origin"
        given = headers.get(CONTROL_HEADER, "")
        if not hmac.compare_digest(given.encode(), expected.encode()):
            return 403, "control_token_invalid"
        return None


MAX_BODY_BYTES: Final = 4 * 1024 * 1024
# Restore receives a whole export: the only large body accepted.
LARGE_BODY_PATHS: Final[Mapping[str, int]] = {"/api/v1/privacy/restore": 512 * 1024 * 1024}


class BodyLimitMiddleware:
    """Rejects bodies larger than the limit before the application reads them.

    With Content-Length, the HTTP server already stops more bytes than declared: comparing is
    enough. Without it (chunked upload), the body is buffered up to the limit before passing it
    to the application, so it can answer 413 instead of failing halfway through reading.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = LARGE_BODY_PATHS.get(scope["path"], MAX_BODY_BYTES)
        declared = Headers(scope=scope).get("content-length")
        too_large = JSONResponse({"error": "payload_too_large"}, status_code=413)
        if declared is not None:
            if not declared.isdigit() or int(declared) > limit:
                await too_large(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunk = message.get("body", b"")
            size += len(chunk)
            if size > limit:
                await too_large(scope, receive, send)
                return
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


class ETagMiddleware:
    """Weak ETags on the API's JSON answers to GET (phase 9.4).

    The body is fingerprinted once it is built; if the browser already holds that version
    (`If-None-Match`), the answer is a 304 with no body. Streams (server-sent events, exports)
    and anything that is not JSON pass through untouched. It saves the transfer and the
    console's re-rendering of large lists; the server still builds the answer.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "GET"
            or not scope["path"].startswith("/api/")
            or scope["path"].endswith("/events")
        ):
            await self.app(scope, receive, send)
            return
        asked = next(
            (value.decode() for key, value in scope["headers"] if key == b"if-none-match"), None
        )
        start: Message | None = None
        chunks: list[bytes] = []
        passthrough = False

        async def capture(message: Message) -> None:
            nonlocal start, passthrough
            if passthrough:
                await send(message)
                return
            if message["type"] == "http.response.start":
                headers = dict(message.get("headers", []))
                json_answer = headers.get(b"content-type", b"").startswith(b"application/json")
                if message["status"] != 200 or not json_answer:
                    passthrough = True
                    await send(message)
                    return
                start = message
                return
            chunks.append(message.get("body", b""))
            if message.get("more_body"):
                return
            body = b"".join(chunks)
            tag = f'W/"{hashlib.blake2b(body, digest_size=16).hexdigest()}"'
            opened = start or {"type": "http.response.start", "status": 200, "headers": []}
            answer = [(k, v) for k, v in opened.get("headers", []) if k != b"content-length"]
            answer.append((b"etag", tag.encode()))
            if asked is not None and tag in {value.strip() for value in asked.split(",")}:
                await send({"type": "http.response.start", "status": 304, "headers": answer})
                await send({"type": "http.response.body", "body": b""})
                return
            answer.append((b"content-length", str(len(body)).encode()))
            await send({**opened, "headers": answer})
            await send({"type": "http.response.body", "body": body})

        await self.app(scope, receive, capture)
