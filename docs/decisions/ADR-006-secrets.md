# ADR-006: The OS keyring for secrets

**Status:** accepted, 2026-11-06. Implemented in T-007, 2026-11-07.

## The question

Connectors (for example a ticket tracker) need tokens. Where do those tokens live? We must not store them in the database or in a config file in plain text, and must not silently fall back to doing so.

## What we decided

- Connector tokens live in the **operating system's keyring**, behind a `SecretStore` interface.
- The database stores only an opaque reference (`secret_ref`).
- If no secure keyring backend is available, the connection is **disabled**. Scrinium never falls back to plain text.

## How it works

`KeyringSecretStore` accepts only a closed list of backends:

- Secret Service;
- libsecret;
- the macOS Keychain;
- the Windows Credential Manager.

It rejects the rest, including the plain-text backends in `keyrings.alt`.

- **Chained backends.** If `keyring` chains several backends, Scrinium picks the secure one inside the chain and never uses the chain itself, which could write to another backend.
- **References.** Each secret gets a random reference (`keyring:<hex>`). It does not reveal the connector and is not derived from the value.
- **Errors.** Backend errors become `SecretStoreUnavailableError`, without the original message, which could contain details of the secret or the store.
- **Verified.** `keyring` 25.7.0 stores, reads and deletes a secret in this machine's `gnome-keyring` (`tests/integration/test_real_keyring.py`, run with `SCRINIUM_TEST_REAL_KEYRING=1`).
- **Exports.** Privacy exports leave out the `secret_ref` column.

## Why

- **Who should protect a token at rest?** The OS already protects the user's other credentials, unlocked with their login. Scrinium should not invent weaker crypto next to it.
- **Why only a reference in the database?** A database backup, export or bug report then contains no secret.
- **Why fail closed?** A user who sees "keyring unavailable" can fix it. A user whose token silently went into a plain-text file never finds out.
- **Why random references?** A reference derived from the connector or the value would leak information through the database.

## Alternatives considered

| Option | Why not |
|---|---|
| **Encrypted column in SQLite** | The key would have to live somewhere on the same machine, which only moves the problem. |
| **`.env` or config file** | Plain text on disk, easy to commit or back up by mistake. |
| **Any backend `keyring` offers** | Includes plain-text backends. |
| **Asking for the token on every use** | Safe, but unusable for background sync. |

## Consequences

- On Linux, the user needs a running Secret Service (GNOME Keyring, KWallet) for connectors.
- The keyring does **not** protect against malware running in the user's unlocked session.

## Revisit when

- Scrinium must run headless (servers, CI) where no keyring exists; or
- hybrid mode needs secrets shared between machines.
