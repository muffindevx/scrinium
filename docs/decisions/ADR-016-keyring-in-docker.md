# ADR-016: A keyring inside the container, unlocked by a password you type

**Status:** accepted by the owner, 2026-11-25 (the typed password; the host password file was not chosen). Revisits [ADR-006](ADR-006-secrets.md) for the case it named: Scrinium running headless.

## The question

The Docker path ([`Dockerfile`](../../Dockerfile), [`docker/scrinium.sh`](../../docker/scrinium.sh)) runs Scrinium in a container. A container has no operating-system keyring: `KeyringSecretStore.from_system()` finds no secure backend, `GET /api/v1/model-providers` answers `keyring: false`, and registering DeepSeek, GLM or Claude fails with `keyring_unavailable`. On a VPS, where Docker is the recommended path, an own provider is often the only model fast enough to write the rules.

ADR-006 refuses plain text and refuses to invent crypto next to the OS keyring. So: **where can a provider key live inside a container so that a copy of the data volume does not reveal it, without Scrinium writing a secret in plain text or growing its own crypto?** And who holds what unlocks it?

## What we decided

1. **The container runs its own Secret Service.** The image adds `dbus` and `gnome-keyring-daemon` (only the daemon: see below). The entrypoint starts a private D-Bus session bus and `gnome-keyring-daemon --components=secrets` before Scrinium. The keyring file lives in the data volume (`/data/keyring/keyrings/login.keyring`), encrypted by gnome-keyring with a password.
2. **Scrinium's secret store does not change.** The backend is the Secret Service, already on ADR-006's closed list. Scrinium never sees the password.
3. **The password is typed, never stored.** `sh docker/scrinium.sh unlock` asks for it with echo off (twice the first time, to create the keyring) and pipes it into the container's stdin (`docker exec -i … scrinium-keyring unlock`, which runs `gnome-keyring-daemon --replace --unlock`). It is never an argument, an environment variable, a label or a file, so `docker inspect`, `ps` and the shell history never show it.
4. **`start` asks for it when a keyring exists.** If the volume already has a keyring and the terminal is interactive, `start` asks; Enter skips it. Skipping, or a restart of the container, leaves the keyring **locked**: local models and isolated mode work; provider calls fail with `keyring_unavailable` until `unlock`.
5. **Still fail-closed.** A locked keyring, a wrong password or a daemon that is not running all fail the same way they fail today. Nothing falls back to plain text.

## How it works

Verified on 2026-11-25 in a throwaway image (`python:3.12.16-slim-bookworm` + `gnome-keyring` + `dbus`, `keyring` 25.7.0), run as a non-root user with `--cap-drop ALL --security-opt no-new-privileges`, as `docker/scrinium.sh` runs Scrinium:

| Step | Result |
|---|---|
| Daemon running, no keyring yet | `keyring` selects `SecretService`; a write fails at once (`Prompt dismissed`: there is no display for gnome-keyring's password window). No hang. |
| `--replace --unlock` with a password on stdin | Creates `login.keyring`; `set_password` and `get_password` work. |
| Container restarted | Keyring locked: `KeyringLocked`. |
| Wrong password | Stays locked; the keyring file is not replaced. |
| Right password | The stored key comes back. |
| The volume's files | `grep` finds no trace of the key: it is only in the encrypted file. |

Then end to end with the real image, on 2026-11-25: `sh docker/scrinium.sh unlock` created the keyring; DeepSeek was registered from Settings › AI models with a test key; `provider_key` returned it; no file in `/data` or `/tmp` contained it; after `docker restart` the key was refused (`keyring_unavailable`) and the console said the keyring "is not available or is locked"; after `unlock` the key came back. A wrong password, Enter at `start`, a non-interactive `start` and `unlock --reset` behaved as described below.

Details the implementation must keep:

- **`--replace --unlock`, not `--unlock`.** On a running daemon, `--unlock` alone starts a second daemon that unlocks nothing; `--start --unlock` is refused. `--replace` hands the bus name to a new daemon that opened the keyring with the password. Scrinium opens a new D-Bus connection on every call, so it does not need a restart.
- **The `default` alias.** gnome-keyring creates `login` but `keyring` writes to the `default` collection. The entrypoint writes `keyrings/default` containing `login` before the first start; without it every write tries to create a collection and fails.
- **A wrong password exits 0.** `unlock` checks the result itself (reads the collection's `Locked` property) and says whether it worked.
- **The bus and the daemon's sockets** live in `/tmp/scrinium-run` (`0700`), private to the `scrinium` account.
- **Only the daemon, not the package.** The `gnome-keyring` package pulls in `gcr` and GTK for its password window: 129 MB more, measured. A build stage installs it and the image takes only `/usr/bin/gnome-keyring-daemon` and the GSettings schemas, plus its two libraries from apt (`libgck-1-0`, `libgcr-base-3-1`). With no password window and no D-Bus activation file, a locked keyring refuses a write in 0.01 s, and the log stays clean. Security updates arrive the same way as for the rest of the image: `update` rebuilds it.
- **The bus is the image's.** `DBUS_SESSION_BUS_ADDRESS` and `XDG_RUNTIME_DIR` are set in the image, so Scrinium and every `docker exec` reach the same bus. `XDG_DATA_HOME` is set only for the daemon, so nothing else in the container moves.

| Piece | Where |
|---|---|
| `dbus` and `gnome-keyring-daemon` in the image; the bus address | [`Dockerfile`](../../Dockerfile) |
| Starting the bus and the daemon before Scrinium | [`docker/entrypoint.sh`](../../docker/entrypoint.sh) |
| `start`, `status`, `unlock`, `reset` inside the container | [`docker/keyring.sh`](../../docker/keyring.sh), installed as `scrinium-keyring` |
| Asking for the password; `unlock`, `unlock --reset`; the offer at `start` | [`docker/scrinium.sh`](../../docker/scrinium.sh) |
| The console's error says the keyring may be locked | `keyring_unavailable` in [`ui/src/lib/labels.ts`](../../ui/src/lib/labels.ts) |

## Why

- **Why gnome-keyring and not something new?** It is the backend ADR-006 already trusts on Linux desktops, unlocked the same way (PAM passes the login password on stdin). Inside the container it is the same program, the same file format and the same code path in Scrinium. We add a process, not crypto.
- **Why does a copy of the volume not reveal the key?** The key exists only inside `login.keyring`, encrypted with a key derived from the password. A backup of `scrinium-data`, or the volume of a VPS that changed hands, holds the ciphertext without what opens it.
- **Why type the password instead of storing it?** Anything stored on the same machine to unlock automatically is a key next to the lock: the argument ADR-006 used against an encrypted SQLite column. Typing it is the only option where the machine at rest holds no key.
- **Why does skipping leave Scrinium usable?** A key is needed only for one feature. Search, local models and isolated mode should not wait for a password, and a container restarted by Docker should come back up.
- **Why not change ADR-006's closed list?** The list is what stops a plain-text backend from sneaking in. This decision works within it.

## What it does not protect

Said plainly, so nobody reads more into it:

- **While unlocked, the key is readable by anyone who can enter the container**, and anyone in the host's `docker` group can, which on Linux is equivalent to root. It is the same as ADR-006's caveat on desktops: the keyring does not protect against code running in your unlocked session.
- **The password protects the volume at rest only as well as it is long.** gnome-keyring's file format uses an iterated SHA-256 key derivation, weaker than a modern KDF: a stolen volume can be attacked offline. `unlock` asks for at least 12 characters when it creates the keyring, and says why.
- **The daemon's memory is not locked.** In the test it showed `VmLck: 0`: running without capabilities, it did not lock its pages, so an unlocked key could reach swap.

## Alternatives considered

| Option | Why not (or why not alone) |
|---|---|
| **Keep fail-closed: no provider in the container** | Safe and zero work, and the README already says so. But a VPS often has no GPU, so the CPU model takes hours: people would leave Docker for the package, or put a key in an environment variable themselves. Remains the behaviour when no keyring was ever created. |
| **The same keyring, unlocked from a password file on the host** (for example `~/.config/scrinium/keyring-password`, `0600`, outside the volume) | Lets `start` and a boot-time service unlock without a person. It still beats plain text in one case: a copy of the volume alone reveals nothing. But anyone who reads the home folder, or a full-disk backup, gets both the lock and the key — ADR-006's argument against an encrypted column. Could be added later as an explicit opt-in (`unlock --from-file`) if unattended restarts matter. |
| **API key in an environment variable or a Docker/Compose secret** | Plain text: visible in `docker inspect`, in `/proc/*/environ`, or as a file in `/run/secrets`. Exactly what ADR-006 refuses. |
| **Mount the host's Secret Service bus into the container** | Works only on a Linux desktop with a session, never on a VPS or on Docker Desktop for macOS/Windows. And the container would reach every secret of the host user, not only Scrinium's. |
| **`keyrings.cryptfile`** (an encrypted file backend for `keyring`) | Same idea as gnome-keyring, but it means adding a backend to the closed list and trusting a small package's crypto. It still needs the same typed password. gnome-keyring gives the same result with a backend we already accept. |
| **Ask for the key on every use, keep it only in memory** | ADR-006 already rejected it: unusable for a queue that writes rules for hours. And every restart would lose every key. |
| **A secrets manager (Vault, a cloud KMS)** | A second service to run, secure and unlock — the same question moved one level up. Out of proportion for one person's VPS. |

## Consequences

- The image grows by 7 MB (329 → 336 MB).
- A provider in Docker needs one more step, `sh docker/scrinium.sh unlock`, after each `start` that was skipped and after every restart of the container or the host.
- Losing the password loses the stored keys, not the data: `unlock --reset` (with confirmation) deletes the keyring, and the providers are registered again.
- The connector tokens of [ADR-014](ADR-014-ticket-connectors.md) use the same store, so ticket connectors start working in the container too.
- The console cannot tell a locked keyring from a missing one: both are `keyring_unavailable`, whose text now says "not available or locked". `GET /api/v1/model-providers` answers `keyring: true` while the daemon runs, locked or not. The README says how to unlock it in Docker.
- `scrinium doctor` reports the Secret Service, not whether it is locked; `sh docker/scrinium.sh unlock` says that.

## Revisit when

- people need unattended restarts (a VPS rebooting at night): consider the host password file as an explicit opt-in; or
- gnome-keyring's file format gains a modern key derivation, or Debian stops shipping it for headless use; or
- hybrid mode needs secrets shared between machines (still open from ADR-006).
