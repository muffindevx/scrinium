"""HTTPS to a remote model provider (ADR-010). Separate from `LocalHttpClient` on purpose.

`LocalHttpClient` only talks to this machine and ignores proxies; that boundary stays as it is.
This client is for a project whose person consented to a remote provider:

- HTTPS only, with certificate checks, to the base URL the provider was registered with;
- no redirects: a redirect would carry the API key to whatever host it names;
- it honours HTTPS_PROXY, like the tools installer: remote traffic leaves anyway, and some
  networks only have that way out;
- the key is asked for on every request, and never kept here;
- errors never carry what was sent or received, nor the key.
"""

import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, ClassVar, Final
from urllib.parse import urlsplit

from scrinium.core.log_hub import HUB
from scrinium.core.secret_patterns import redact_tokens
from scrinium.infrastructure.local_http import (
    MAX_RESPONSE_BYTES,
    ModelError,
    Outcome,
    ProviderUnavailableError,
    trace_content,
)

# Statuses worth trying again later: rate limits, overload and server errors.
_UNAVAILABLE: Final = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 529})


class ProviderAuthError(ModelError):
    """The provider rejected the key (401 or 403). Not retried: the key must be fixed."""

    outcome: ClassVar[Outcome] = Outcome.ERROR


class ProviderRequestError(ModelError):
    """The provider rejected the request itself (another 4xx)."""

    outcome: ClassVar[Outcome] = Outcome.ERROR

    def __init__(self, status: int, kind: str = "", detail: str = "") -> None:
        super().__init__()
        self.status = status
        # The provider's error code (`not_found_error`, `model_not_found`…) and its message,
        # without any key (see `error_detail`). Kept out of `str()` and of logs: only "Probar
        # conexión", whose request is a fixed sentence, shows them.
        self.kind = kind
        self.detail = detail


MAX_DETAIL: Final = 300


def error_detail(body: Any, secrets: tuple[str, ...] = ()) -> tuple[str, str]:
    """The error code and message of a provider's error body (OpenAI and Anthropic shapes),
    with known token formats and the request's own key removed."""
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return "", ""
    kind = str(error.get("type") or error.get("code") or "")[:60]
    message = str(error.get("message") or "")
    for secret in secrets:
        if secret:
            message = message.replace(secret, "[REDACTADO]")
    return kind, redact_tokens(message)[:MAX_DETAIL]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def check_https_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        raise ValueError("unsupported url")
    return url.rstrip("/")


class RemoteHttpsClient:
    def __init__(
        self,
        base_url: str,
        headers: Callable[[], dict[str, str]],
        opener: urllib.request.OpenerDirector | None = None,
    ) -> None:
        """`headers` is called for every request: that is where the key is read."""
        self._base_url = check_https_url(base_url)
        self._headers = headers
        self._opener = opener or urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=ssl.create_default_context()), _NoRedirect()
        )

    @property
    def host(self) -> str:
        return (urlsplit(self._base_url).hostname or "").lower()

    def request(self, path: str, body: dict[str, Any] | None, timeout: float) -> Any:
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
        tracing = HUB.wants_content()
        if tracing and data is not None:
            trace_content("->", path, data)
        request = urllib.request.Request(  # noqa: S310  https checked above
            f"{self._base_url}{path}",
            data=data,
            headers={"Content-Type": "application/json", **self._headers()},
            method="GET" if body is None else "POST",
        )
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            try:
                if error.code in (401, 403):
                    raise ProviderAuthError from None
                if error.code in _UNAVAILABLE:
                    raise ProviderUnavailableError from None
                try:
                    body = json.loads(error.read(MAX_RESPONSE_BYTES))
                except (ValueError, OSError):
                    body = None
                # Any long word of a header (the key after "Bearer", or an x-api-key).
                keys = tuple(
                    word
                    for value in request.headers.values()
                    for word in value.split()
                    if len(word) >= 16
                )
                raise ProviderRequestError(error.code, *error_detail(body, keys)) from None
            finally:
                error.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ProviderUnavailableError from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ModelError
        if tracing:
            trace_content("<-", path, raw)
        try:
            return json.loads(raw)
        except ValueError:
            raise ModelError from None
