# ADR-001: Local-first, for one person

**Status:** accepted, 2026-11-06. Amended on 2026-11-14 by [ADR-010](ADR-010-remote-model-providers.md): a project may use a remote model with explicit consent; local stays the default. Amended on 2026-11-17 by [ADR-013](ADR-013-own-provider-by-default.md): local stays the home of the data and the runtime; the model that writes the rules is chosen at first run (own provider recommended, a local server, or isolated mode).

## The question

Where should Scrinium run, and for whom, given that:

- it reads source code, tickets and internal documents;
- one person builds and operates it;
- there is no budget to run cloud infrastructure?

## What we decided

Scrinium runs **on the user's own machine** as a **modular monolith**:

- one runtime process;
- one workspace;
- no user accounts.

Nothing it indexes or generates leaves the machine by default. Every knowledge entity still carries a `workspace_id`, so a later multi-workspace or hybrid version does not need a data migration.

## How it works

- The runtime listens only on `127.0.0.1` ([ADR-003](ADR-003-local-runtime-security.md)).
- Data lives in a private directory of the user's account (`0700`, or the equivalent ACL on Windows).
- Storage is embedded: SQLite, with vectors in sqlite-vec inside the same database ([ADR-002](ADR-002-storage.md)).
- Models run locally ([ADR-004](ADR-004-ai-data-egress.md)), unless a person turns a remote model on for a project ([ADR-010](ADR-010-remote-model-providers.md)).
- One process holds the API, the background jobs (sync, indexing, rule extraction) and serves the UI.

## Why

- **Who sees the data?** Code and tickets are often the most sensitive assets a team has. If nothing leaves the machine, the trust question is reduced to the machine itself, which the user already trusts with that code.
- **Who pays and operates it?** A SaaS needs servers, backups, an on-call person and a security posture for many tenants. None of that is justified before the product proves useful to one person.
- **What is the smallest thing that can prove the value?** One person, one workspace, real repositories. Accounts, sharing and permissions add work that teaches nothing about whether the core idea works.
- **Why a modular monolith and not services?** There is a single process and a single user. Services would add network boundaries, deployment and failure modes with no benefit. The modules stay separated in code (`domain`, `application`, `infrastructure`, `api`) so they can be split later if hybrid mode needs it.

## Alternatives considered

| Option | Why not |
|---|---|
| **SaaS from day one** | Cost and operational risk before validating the product. The user would have to send code to a third party just to try it. |
| **Desktop application** (Electron, Tauri) | It is not more secure than a loopback web runtime, and it adds packaging, signing and auto-update work for every OS. The browser is already installed. |
| **Self-hosted team server** | It brings back accounts, permissions and network exposure, for a team that does not exist yet. |

## Consequences

- **No collaboration.** One person, one machine. Sharing knowledge between people is out of scope.
- **Security depends on the user's machine.** Malware in an unlocked session can read what the user can read. Scrinium reports whether full-disk encryption is on, but does not encrypt its own data. See `docs/security/THREAT_MODEL.md`.
- **Performance depends on the user's hardware.** Rule extraction with a local model takes ~40 minutes on a GTX 1060 for a 50 000-line project (see `README.md`, "Hardware requirements").
- **Moving to hybrid or cloud** needs a new ADR on data egress and consent.

## Revisit when

- more than one person needs the same knowledge base; or
- a user needs Scrinium on a machine that cannot run the local models.
