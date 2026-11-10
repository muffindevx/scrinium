"""Local single-person session: launch token, session cookie and CSRF token."""

import hashlib
import hmac
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

LAUNCH_TOKEN_TTL_SECONDS = 120.0
SESSION_TTL_SECONDS = 24 * 60 * 60.0
_TOKEN_BYTES = 32


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


@dataclass(frozen=True)
class Session:
    # Only the hash is kept: a memory dump or an accidental log of the object
    # does not hand over a usable cookie.
    id_digest: bytes
    csrf_token: str
    expires_at: float


class LocalSessionManager:
    """Keeps in memory a single-use launch token and a single active session.

    Nothing is persisted: restarting Scrinium invalidates everything.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._launch_digest: bytes | None = None
        self._launch_expires_at = 0.0
        self._session: Session | None = None

    def issue_launch_token(self) -> str:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        with self._lock:
            self._launch_digest = _digest(token)
            self._launch_expires_at = self._clock() + LAUNCH_TOKEN_TTL_SECONDS
        return token

    def redeem_launch_token(self, token: str) -> str | None:
        """Exchanges a valid launch token for a new session cookie."""
        with self._lock:
            expected = self._launch_digest
            if expected is None or self._clock() >= self._launch_expires_at:
                return None
            if not hmac.compare_digest(expected, _digest(token)):
                return None
            self._launch_digest = None
            return self._new_session_locked()

    def rotate(self, session_id: str) -> str | None:
        """Replaces the session and its CSRF token; the previous cookie stops working."""
        with self._lock:
            if self._valid_locked(session_id) is None:
                return None
            return self._new_session_locked()

    def validate(self, session_id: str | None) -> Session | None:
        if session_id is None:
            return None
        with self._lock:
            return self._valid_locked(session_id)

    def end(self) -> None:
        with self._lock:
            self._session = None

    @staticmethod
    def csrf_matches(session: Session, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(
            session.csrf_token.encode(), candidate.encode()
        )

    def _new_session_locked(self) -> str:
        session_id = secrets.token_urlsafe(_TOKEN_BYTES)
        self._session = Session(
            id_digest=_digest(session_id),
            csrf_token=secrets.token_urlsafe(_TOKEN_BYTES),
            expires_at=self._clock() + SESSION_TTL_SECONDS,
        )
        return session_id

    def _valid_locked(self, session_id: str) -> Session | None:
        session = self._session
        if session is None or self._clock() >= session.expires_at:
            return None
        if not hmac.compare_digest(session.id_digest, _digest(session_id)):
            return None
        return session
