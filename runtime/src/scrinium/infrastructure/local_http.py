"""Minimal HTTP client for the local models on this same machine (ADR-004).

Only loopback addresses are accepted: a remote server would be data egress. Proxy
environment variables are ignored for the same reason: they would send the text to another machine.
The embeddings and the LLM share it, with llama.cpp or with Ollama.
"""

import ipaddress
import json
import logging
import urllib.error
import urllib.request
from enum import StrEnum
from typing import Any, Final
from urllib.parse import urlsplit

from scrinium.core.log_hub import HUB, content_extra

# A model response should never get close to this; if it does, something is wrong.
MAX_RESPONSE_BYTES: Final = 16 * 1024 * 1024
# What `scrinium attach --contenido` shows of each request and response.
_TRACE_CHARS: Final = 4000

content_logger = logging.getLogger("scrinium.contenido")


def _trace(direction: str, path: str, raw: bytes) -> None:
    text = raw.decode("utf-8", "replace")
    more = f" … ({len(text) - _TRACE_CHARS} caracteres mas)" if len(text) > _TRACE_CHARS else ""
    content_logger.debug(
        "%s %s %s%s", direction, path, text[:_TRACE_CHARS], more, extra=content_extra()
    )


class ModelStatus(StrEnum):
    READY = "ready"
    MODEL_MISSING = "model_missing"
    UNAVAILABLE = "unavailable"


class ModelError(RuntimeError):
    """The model did not answer as expected. Never includes the text sent or received."""


def check_loopback_url(url: str) -> str:
    """URL without a trailing slash; raises ValueError if it is not http to loopback."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme != "http" or parts.username or parts.password or parts.path not in {"", "/"}:
        raise ValueError("unsupported url")
    if host != "localhost":
        try:
            if not ipaddress.ip_address(host).is_loopback:
                raise ValueError("not loopback")
        except ValueError:
            raise ValueError("not loopback") from None
    return url.rstrip("/")


class LocalHttpClient:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self._base_url = check_loopback_url(base_url)
        self._headers = {"Content-Type": "application/json"}
        if api_key:
            self._headers["Authorization"] = f"Bearer {api_key}"
        # No ProxyHandler from the environment: urllib would use HTTP_PROXY if it existed.
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path: str, body: dict[str, Any] | None, timeout: float) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        tracing = HUB.wants_content()
        if tracing and data is not None:
            _trace("->", path, data)
        request = urllib.request.Request(  # noqa: S310  scheme and host validated above
            f"{self._base_url}{path}",
            data=data,
            headers=self._headers,
            method="GET" if body is None else "POST",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelError from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelError
        if tracing:
            _trace("<-", path, raw)
        try:
            return json.loads(raw)
        except ValueError:
            raise ModelError from None
