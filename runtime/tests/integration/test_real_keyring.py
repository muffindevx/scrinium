"""Test against the real keyring. Writes a temporary secret and deletes it.

Only runs with SCRINIUM_TEST_REAL_KEYRING=1: it must not touch anyone's keyring unasked.
"""

import os

import pytest

from scrinium.infrastructure.secret_store import KeyringSecretStore

pytestmark = [
    pytest.mark.real_keyring,
    pytest.mark.skipif(
        os.environ.get("SCRINIUM_TEST_REAL_KEYRING") != "1",
        reason="needs SCRINIUM_TEST_REAL_KEYRING=1",
    ),
]


def test_round_trip_in_the_system_keyring() -> None:
    store = KeyringSecretStore.from_system(service="scrinium-test")
    assert store.backend_name is not None, "there is no secure keyring on this machine"
    ref = store.put("scrinium-test-valor-temporal")
    try:
        assert store.get(ref) == "scrinium-test-valor-temporal"
    finally:
        store.delete(ref)
    assert store.get(ref) is None
