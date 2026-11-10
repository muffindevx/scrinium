"""SecretStore: only secure backends, opaque references and errors without the secret."""

import keyring
import keyring.backends.fail
import keyring.backends.null
import keyring.backends.SecretService
import pytest
from keyring.backend import KeyringBackend

from scrinium.infrastructure.secret_store import (
    MAX_SECRET_LENGTH,
    SERVICE_NAME,
    KeyringSecretStore,
    SecretStoreUnavailableError,
    secure_backend_name,
)
from tests.conftest import MemoryKeyring

SECRET = "ghp_" + "a" * 36


def test_round_trip_through_an_opaque_reference() -> None:
    backend = MemoryKeyring()
    store = KeyringSecretStore(backend, "prueba")
    ref = store.put(SECRET)
    assert SECRET not in ref
    assert list(backend.entries) == [(SERVICE_NAME, ref)]
    assert store.get(ref) == SECRET
    store.delete(ref)
    assert store.get(ref) is None
    store.delete(ref)  # deleting something that no longer exists does not fail


def test_each_secret_gets_a_new_reference() -> None:
    store = KeyringSecretStore(MemoryKeyring(), "prueba")
    assert store.put(SECRET) != store.put(SECRET)


@pytest.mark.parametrize("ref", ["", "keyring:abc", "../keyring:x", "otro:" + "a" * 32])
def test_foreign_references_are_rejected(ref: str) -> None:
    store = KeyringSecretStore(MemoryKeyring(), "prueba")
    with pytest.raises(ValueError):
        store.get(ref)
    with pytest.raises(ValueError):
        store.delete(ref)


@pytest.mark.parametrize("value", ["", "x" * (MAX_SECRET_LENGTH + 1)])
def test_empty_or_huge_secrets_are_rejected(value: str) -> None:
    with pytest.raises(ValueError):
        KeyringSecretStore(MemoryKeyring(), "prueba").put(value)


def test_without_a_backend_nothing_is_stored() -> None:
    store = KeyringSecretStore(None, None)
    with pytest.raises(SecretStoreUnavailableError):
        store.put(SECRET)
    with pytest.raises(SecretStoreUnavailableError):
        store.get("keyring:" + "a" * 32)


class ExplodingKeyring(MemoryKeyring):
    def set_password(self, service: str, username: str, password: str) -> None:
        raise RuntimeError(f"fallo guardando {password}")


def test_backend_errors_never_carry_the_secret() -> None:
    store = KeyringSecretStore(ExplodingKeyring(), "prueba")
    with pytest.raises(SecretStoreUnavailableError) as raised:
        store.put(SECRET)
    assert SECRET not in str(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__


def test_secret_service_is_accepted() -> None:
    assert secure_backend_name(keyring.backends.SecretService.Keyring()) == "Secret Service"


class PlaintextKeyring(MemoryKeyring):
    """Mimics keyrings.alt: stores in an unencrypted file."""


PlaintextKeyring.__module__ = "keyrings.alt.file"


@pytest.mark.parametrize(
    "backend",
    [
        keyring.backends.fail.Keyring(),
        keyring.backends.null.Keyring(),
        PlaintextKeyring(),
        MemoryKeyring(),
    ],
)
def test_insecure_or_unknown_backends_leave_the_store_unavailable(
    monkeypatch: pytest.MonkeyPatch, backend: KeyringBackend
) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: backend)
    store = KeyringSecretStore.from_system()
    assert store.backend_name is None
    with pytest.raises(SecretStoreUnavailableError):
        store.put(SECRET)
    assert getattr(backend, "entries", {}) == {}


class FakeChainer(MemoryKeyring):
    def __init__(self, *backends: KeyringBackend) -> None:
        super().__init__()
        self.backends = list(backends)


def test_secure_backend_inside_a_chain_is_used_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    plaintext = PlaintextKeyring()
    secure = keyring.backends.SecretService.Keyring()
    monkeypatch.setattr(keyring, "get_keyring", lambda: FakeChainer(plaintext, secure))
    store = KeyringSecretStore.from_system()
    assert store.backend_name == "Secret Service"
    assert store._backend is secure


def test_chain_without_a_secure_backend_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyring, "get_keyring", lambda: FakeChainer(PlaintextKeyring()))
    assert KeyringSecretStore.from_system().backend_name is None


def test_a_broken_keyring_leaves_the_store_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> KeyringBackend:
        raise RuntimeError("no D-Bus")

    monkeypatch.setattr(keyring, "get_keyring", broken)
    assert KeyringSecretStore.from_system().backend_name is None
