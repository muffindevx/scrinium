# ADR-007: Repository structure

**Status:** accepted and implemented, 2026-11-06. Owner's decision.

## The question

With `src/` and `frontend/` both at the root, it was not obvious that `src/` was only the backend. That layout was only an early suggestion. How should a repository with one Python service and one web UI be laid out?

## What we decided

- A **light monorepo**:
  - `runtime/` for the Python application;
  - `ui/` for the React interface;
  - a root `Makefile` for the common commands.

## How it works

- `runtime/` keeps the `src/` layout (`runtime/src/scrinium`), so tests run against the installed package and not against files that happen to be on the path.
- `ui/` builds into `runtime/src/scrinium/web_dist`, which git ignores. The runtime serves it.
- `make setup`, `make start`, `make check`, `make build` and the other targets run the right tool in the right folder.

## Why

- **What should a newcomer understand from the root listing?** That there are two programs, a runtime and a UI, and where the docs are. `runtime/` and `ui/` say that. `src/` and `frontend/` did not.
- **Why not a deeper structure?** There are two projects and nothing shared between them. Extra levels (`apps/`, `packages/`) would be empty ceremony.
- **Why a Makefile?** One entry point for commands that span both toolchains, uv and pnpm.

## Alternatives considered

| Option | Why not |
|---|---|
| **Only rename `frontend/` to `ui/`** | `src/` would still sit at the root next to `ui/`, with the same ambiguity. |
| **`apps/` or `packages/`** | One more level, with nothing to group across two projects. |
| **pnpm or uv workspaces** | Each language has one project and nothing to share. |
| **Two repositories** | The API contract changes together with the UI. Separate repositories would need coordinated releases for every change. |

## Consequences

- `uv run scrinium start` no longer works from the root. Use `make start`, or run it inside `runtime/`.
- `make` does not ship with Windows. This only affects development, not people who install Scrinium.
- If hybrid mode needs another service, a new top-level folder will be added then, or the layout revisited.

## Revisit when

- a third deployable (another service, a CLI distributed separately) appears; or
- code starts being shared between `runtime/` and `ui/` (for example generated API types).
