"""OAuth 2.0 with PKCE for Linear, as a native app (RFC 8252; ADR-014, decision 3, amendment 1).

- The browser goes to Linear's consent page with a PKCE challenge (S256) and a random `state`.
- Linear sends it back to Scrinium itself, `http://127.0.0.1:<Scrinium's port>/oauth/linear/
  callback`: a fixed port, as Linear matches redirect URIs exactly, and no extra listener.
- The code is exchanged for tokens here, over HTTPS, with the verifier; no client secret.
- Access tokens are refreshed before they expire, and the refresh token rotates; on disconnect
  the token is revoked at Linear.

The `client_id` is the registered app's (`SCRINIUM_LINEAR_CLIENT_ID`); without it, OAuth is off
and a personal API key is the way in.
"""

import base64
import hashlib
import json
import os
import secrets
import ssl
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlencode

from scrinium.infrastructure.tickets.base import (
    MAX_RESPONSE_BYTES,
    TIMEOUT_SECONDS,
    HttpAnswer,
    NoRedirect,
    TrackerAuthError,
    TrackerResponseError,
    TrackerUnavailableError,
    parse_json,
)

AUTHORIZE_URL: Final = "https://linear.app/oauth/authorize"
TOKEN_URL: Final = "https://api.linear.app/oauth/token"  # noqa: S105  an endpoint, not a secret
REVOKE_URL: Final = "https://api.linear.app/oauth/revoke"
CALLBACK_PATH: Final = "/oauth/linear/callback"
# Reading is enough to sync; writing comments (10.7) needs its own scope, asked only if allowed.
READ_SCOPE: Final = "read"
WRITE_SCOPE: Final = "read,comments:create"
STATE_TTL_SECONDS: Final = 600
# Refresh this long before the access token expires.
REFRESH_MARGIN_SECONDS: Final = 120

# (url, form fields, headers) -> answer
FormTransport = Callable[[str, dict[str, str], dict[str, str]], HttpAnswer]


def client_id() -> str | None:
    value = os.environ.get("SCRINIUM_LINEAR_CLIENT_ID", "").strip()
    return value or None


def https_form_transport() -> FormTransport:
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()), NoRedirect()
    )

    def send(url: str, form: dict[str, str], headers: dict[str, str]) -> HttpAnswer:
        if not url.startswith(("https://api.linear.app/", "https://linear.app/")):
            raise TrackerResponseError
        request = urllib.request.Request(  # noqa: S310  fixed https endpoints
            url, data=urlencode(form).encode(), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", **headers},
        )  # fmt: skip
        try:
            with opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                return HttpAnswer(response.status, {}, response.read(MAX_RESPONSE_BYTES + 1))
        except urllib.error.HTTPError as error:
            try:
                return HttpAnswer(error.code, {}, error.read(MAX_RESPONSE_BYTES + 1))
            finally:
                error.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            raise TrackerUnavailableError from None

    return send


@dataclass(frozen=True)
class Pending:
    """A flow started and not finished: what the callback needs, kept in memory only."""

    project_id: str
    verifier: str
    redirect_uri: str
    created: float


@dataclass(frozen=True)
class Tokens:
    access: str
    refresh: str | None
    # Seconds since the epoch; None if Linear did not say.
    expires_at: float | None


class OAuthFlows:
    """The flows in progress: a `state` is used once and expires in ten minutes."""

    def __init__(self, now: Callable[[], float] = time.time) -> None:
        self._pending: dict[str, Pending] = {}
        self._lock = threading.Lock()
        self._now = now

    def start(self, project_id: str, redirect_uri: str, client: str, scope: str) -> str:
        """The URL of Linear's consent page for a new flow."""
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        state = secrets.token_urlsafe(24)
        with self._lock:
            self._forget_old()
            self._pending[state] = Pending(project_id, verifier, redirect_uri, self._now())
        query = urlencode({
            "client_id": client, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": scope, "state": state, "prompt": "consent",
            "code_challenge": challenge.decode().rstrip("="), "code_challenge_method": "S256",
        })  # fmt: skip
        return f"{AUTHORIZE_URL}?{query}"

    def finish(self, state: str) -> Pending | None:
        """The flow for this `state`, once: a second use, or an expired one, is None."""
        with self._lock:
            self._forget_old()
            return self._pending.pop(state, None)

    def _forget_old(self) -> None:
        limit = self._now() - STATE_TTL_SECONDS
        for state in [s for s, p in self._pending.items() if p.created < limit]:
            del self._pending[state]


def _tokens(answer: HttpAnswer, now: float) -> Tokens:
    if answer.status in (400, 401, 403):
        raise TrackerAuthError
    if answer.status >= 500:
        raise TrackerUnavailableError
    data: Any = parse_json(answer.body)
    if answer.status != 200 or not isinstance(data, dict):
        raise TrackerResponseError
    access = data.get("access_token")
    if not isinstance(access, str) or not access:
        raise TrackerResponseError
    refresh = data.get("refresh_token")
    expires = data.get("expires_in")
    return Tokens(
        access,
        refresh if isinstance(refresh, str) and refresh else None,
        now + float(expires) if isinstance(expires, int | float) and expires > 0 else None,
    )


def exchange(
    pending: Pending, code: str, client: str, transport: FormTransport | None = None,
    now: Callable[[], float] = time.time,
) -> Tokens:  # fmt: skip
    """The code for tokens, with the PKCE verifier; no client secret."""
    send = transport or https_form_transport()
    answer = send(TOKEN_URL, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": pending.redirect_uri,
        "client_id": client, "code_verifier": pending.verifier,
    }, {})  # fmt: skip
    return _tokens(answer, now())


def refresh(
    refresh_token: str, client: str, transport: FormTransport | None = None,
    now: Callable[[], float] = time.time,
) -> Tokens:  # fmt: skip
    """New tokens; Linear rotates the refresh token, so the new one replaces the old."""
    send = transport or https_form_transport()
    answer = send(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": refresh_token,
                              "client_id": client}, {})  # fmt: skip
    return _tokens(answer, now())


def revoke(access_token: str, transport: FormTransport | None = None) -> bool:
    """Best effort: True if Linear accepted the revocation."""
    send = transport or https_form_transport()
    try:
        answer = send(REVOKE_URL, {}, {"Authorization": f"Bearer {access_token}"})
    except TrackerUnavailableError:
        return False
    return answer.status in (200, 204)


def encode_secret(tokens: Tokens) -> str:
    """What goes to the keyring for an OAuth connection: one secret, never in the database."""
    # Compact: a stored token has no spaces.
    return json.dumps({"access": tokens.access, "refresh": tokens.refresh,
                       "expires_at": tokens.expires_at}, separators=(",", ":"))  # fmt: skip


def decode_secret(value: str) -> Tokens | None:
    try:
        data = json.loads(value)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("access"), str):
        return None
    refresh_token = data.get("refresh")
    expires = data.get("expires_at")
    return Tokens(
        data["access"],
        refresh_token if isinstance(refresh_token, str) else None,
        float(expires) if isinstance(expires, int | float) else None,
    )
