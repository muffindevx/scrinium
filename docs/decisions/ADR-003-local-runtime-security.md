# ADR-003: Security of the local runtime

**Status:** accepted and implemented, 2026-11-06.

## The question

Scrinium is an HTTP server on the user's machine that can read their code and run analyses. Any web page the user visits, and any other account on the machine, can try to talk to it. How do we make sure only the user's own browser tab can use it?

## What we decided

1. **Bind to `127.0.0.1` only.** The address is a constant. There is no `--host` option.
2. **Protected by default.** A middleware requires a session on every `/api/*` route. Every method other than GET or HEAD also needs a matching `Origin` and a CSRF token. Exceptions are declared one by one.
3. **Exact `Host` and `Origin`.** Only `127.0.0.1:<port>` is accepted. Even `localhost` is rejected, so there is exactly one valid origin.
4. **No CORS.** There is no CORS middleware, so no other origin can read responses.
5. **Entry through a single-use launch token.** `scrinium start` opens `http://127.0.0.1:<port>/auth/launch?token=...` in the browser. The token works once and expires after 2 minutes (`core/session.py`, `LAUNCH_TOKEN_TTL_SECONDS = 120`).
6. **One in-memory session.** It is never persisted, and expires after 24 hours (`SESSION_TTL_SECONDS`).

## How it works

- At startup the runtime creates the session manager and issues a launch token. It then opens the launch URL.
- `/auth/launch` redeems the token: it must be the first use and within 2 minutes. It then sets the session cookie and redirects to the UI.
- Every API request goes through the middleware:
  1. it checks `Host`;
  2. for writes, it checks `Origin` and the CSRF token;
  3. it checks the session.
- Tests cover this in `tests/security/` (host and origin, session and CSRF, static files).

## Why

- **Who can reach a port on loopback?** Any process on the machine, and any web page through the user's browser (DNS rebinding, cross-site requests). Binding to `127.0.0.1` removes the network. The `Host` check stops DNS rebinding. The `Origin` and CSRF checks stop cross-site writes.
- **Why reject `localhost` too?** Each extra accepted name is another origin to validate and another cookie scope to reason about. One origin is easier to get right.
- **Why a launch token instead of a password?** One person, one machine: a password adds friction and still has to be stored somewhere. A token that works once, for 2 minutes, proves the browser tab was opened by whoever started Scrinium.
- **Why keep the session in memory?** Restarting Scrinium logs everyone out. That is acceptable for one user, and it means there is no session secret on disk to steal.

## Alternatives considered

| Option | Why not |
|---|---|
| **A `file://` page with `0600` permissions that redirects** | This was the first implementation. It kept the token off the command line. It was withdrawn on 2026-11-06: it worked in Zen with temporary profiles, but did not reach the server with the owner's real profile. The exact cause is unknown. |
| **Typing a code into the UI** | More private against other accounts on the same machine, but adds a step on every start. The owner chose the direct link. |
| **Local TLS** | Requires installing a certificate authority on the machine, which is a larger risk than it removes. Traffic never leaves loopback. |
| **Also accept `localhost` and `::1`** | More origins to validate, no current benefit. |
| **A persistent session or "remember me"** | A secret on disk that malware could reuse, to save one restart a day. |

## Consequences

- After logging out or after the session expires, the user has to restart Scrinium to get in.
- The token is briefly visible to other accounts on the machine in the process list (`ps`). Whoever uses it first gets the session; the user's browser would then show "invalid link". The reference machine has a single human account.
- The link can stay in browser history, but it no longer works after use.
- The cookie has no `Secure` flag, and cookies ignore the port. See `docs/security/THREAT_MODEL.md`.

## Revisit when

- Scrinium must run on shared multi-user machines (consider typed codes or OS-level IPC); or
- hybrid mode exposes anything beyond loopback.
