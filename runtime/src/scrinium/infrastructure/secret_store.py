"""Connector secrets in the operating system's keyring (ADR-006).

The database only stores the reference `put` returns. Without a secure backend,
the store is unavailable: it never falls back to plain text.
"""

import re
import uuid
from collections.abc import Callable
from typing import Final, Protocol

import keyring
from keyring.backend import KeyringBackend

SERVICE_NAME: Final = "scrinium"
MAX_SECRET_LENGTH: Final = 16 * 1024
_REF_PREFIX: Final = "keyring:"
_REF_PATTERN: Final = re.compile(r"^keyring:[0-9a-f]{32}$")

# Closed list. `keyring` configuration (PYTHON_KEYRING_BACKEND, keyringrc.cfg) or a
# package like keyrings.alt can pick a plain-text backend; those are left out.
_SECURE_BACKENDS: Final = {
    ("keyring.backends.SecretService", "Keyring"): "Secret Service",
    ("keyring.backends.libsecret", "Keyring"): "Secret Service (libsecret)",
    ("keyring.backends.macOS", "Keyring"): "Llavero de macOS",
    ("keyring.backends.Windows", "WinVaultKeyring"): "Administrador de credenciales de Windows",
}


class SecretStoreUnavailableError(RuntimeError):
    """There is no secure keyring, or the keyring failed. Never includes the secret."""


def secure_backend_name(backend: KeyringBackend) -> str | None:
    cls = type(backend)
    return _SECURE_BACKENDS.get((cls.__module__, cls.__name__))


class SecretStore(Protocol):
    @property
    def backend_name(self) -> str | None:
        """Readable name of the backend, or None if there is no secure one."""

    def put(self, value: str) -> str:
        """Stores a new secret and returns its reference."""

    def get(self, ref: str) -> str | None: ...

    def replace(self, ref: str, value: str) -> None:
        """A new value under the same reference (an OAuth token that was refreshed)."""

    def delete(self, ref: str) -> None: ...


class KeyringSecretStore:
    def __init__(
        self,
        backend: KeyringBackend | None,
        backend_name: str | None,
        service: str = SERVICE_NAME,
    ) -> None:
        self._backend = backend
        self._backend_name = backend_name
        self._service = service

    @classmethod
    def from_system(cls, service: str = SERVICE_NAME) -> "KeyringSecretStore":
        try:
            chosen = keyring.get_keyring()
        except Exception:
            return cls(None, None, service)
        # With several backends installed, keyring picks a ChainerBackend that writes to the
        # first one that accepts: the secure one inside is used directly, never the chain.
        for backend in (chosen, *getattr(chosen, "backends", ())):
            name = secure_backend_name(backend)
            if name is not None:
                return cls(backend, name, service)
        return cls(None, None, service)

    @property
    def backend_name(self) -> str | None:
        return self._backend_name

    def put(self, value: str) -> str:
        if not value or len(value) > MAX_SECRET_LENGTH:
            raise ValueError("secret empty or too long")
        # A random reference: it reveals no connector and is not derived from the secret.
        ref = f"{_REF_PREFIX}{uuid.uuid4().hex}"
        self._call(lambda backend: backend.set_password(self._service, ref, value))
        return ref

    def get(self, ref: str) -> str | None:
        _check_ref(ref)
        result: str | None = self._call(lambda backend: backend.get_password(self._service, ref))
        return result

    def replace(self, ref: str, value: str) -> None:
        _check_ref(ref)
        if not value or len(value) > MAX_SECRET_LENGTH:
            raise ValueError("secret empty or too long")
        self._call(lambda backend: backend.set_password(self._service, ref, value))

    def delete(self, ref: str) -> None:
        _check_ref(ref)
        if self.get(ref) is not None:
            self._call(lambda backend: backend.delete_password(self._service, ref))

    def _call[T](self, operation: Callable[[KeyringBackend], T]) -> T:
        if self._backend is None:
            raise SecretStoreUnavailableError
        try:
            return operation(self._backend)
        except Exception:
            # Any failure, without the original message: some backends include call data,
            # and D-Bus can raise exceptions that are not keyring's.
            raise SecretStoreUnavailableError from None


def _check_ref(ref: str) -> None:
    if not _REF_PATTERN.match(ref):
        raise ValueError("invalid secret reference")
