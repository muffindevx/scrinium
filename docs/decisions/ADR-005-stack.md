# ADR-005: Languages and tools

**Status:** accepted, 2026-11-06. Choices marked (\*) were made by the owner.

## The question

Which languages, frameworks and tools does Scrinium use? They must suit:

- a single developer whose strongest skills are in frontend;
- a product built around AI models and code parsers;
- a strict supply-chain posture.

## What we decided

| Area | Choice |
|---|---|
| Backend | Python 3.12 (\*), FastAPI, Pydantic |
| Python packages | uv with `uv.lock` (\*) |
| Python quality | Ruff, mypy `strict`, pytest |
| CLI | `argparse` from the standard library |
| Frontend | React, TypeScript, Vite, pnpm (\*) |
| UI components | Tailwind and shadcn/ui (\*) |
| Frontend quality | oxlint, `tsc`, Vitest |
| Supply chain | `make audit` (uv and pnpm audits) and `make sbom` (CycloneDX for the shipped dependencies) |

The code lives in the current folder, on an NTFS disk, by the owner's decision. The folder layout is in [ADR-007](ADR-007-repository-structure.md).

## How it works

**Backend**

- `runtime/` is a uv project, locked by `uv.lock`, independent of the system Python (3.10 on the reference machine).
- `make check` runs Ruff, mypy `strict` and pytest, then oxlint, `tsc` and Vitest for the UI.

**Frontend**

- `ui/` is a pnpm project. `pnpm build` writes the compiled UI into `runtime/src/scrinium/web_dist`, which the runtime serves.
- The Geist font is bundled (`@fontsource-variable/geist`) and served by the runtime: no request to Google Fonts.

**Supply chain**

- `make audit` checks both lockfiles against advisories.
- `make sbom` writes CycloneDX SBOMs into `sbom/`, which is ignored by git.

## Why

- **Why Python for the backend?** The AI, parsing and data ecosystem is there: tree-sitter bindings, sqlglot, sqlite-vec, model clients. Rust would only pay off for a measured bottleneck, and none exists.
- **Why FastAPI and Pydantic?** Typed request and response models double as validation and documentation. Pydantic also validates the model's structured output.
- **Why uv?** Reproducible, fast installs that do not touch the system Python.
- **Why `argparse`?** A handful of commands do not justify a CLI dependency.
- **Why React and TypeScript?** That is where the owner works fastest. TypeScript catches API contract drift at build time.
- **Why shadcn/ui?** Its components are copied into the repository, so there is no runtime component library to update or trust.
- **Why oxlint and Vitest?** They came with the current Vite template. Vitest covers the API client.
- **Why audits and SBOMs?** Scrinium reads sensitive code, so its dependencies are part of its attack surface.

## Alternatives considered

| Option | Why not |
|---|---|
| **Node.js for the backend too** | One language, but a weaker ecosystem for parsers, vector stores and model tooling. |
| **Rust or Go for the backend** | Faster, but slower to build with, and no measured bottleneck asks for it. |
| **Poetry or pip-tools** | uv is faster and handles the Python version itself. |
| **Typer or Click** | An extra dependency for a few commands. |
| **A component library from npm** (MUI, Chakra) | More runtime dependencies and less control over markup and accessibility. |
| **ESLint** | Slower, and not what the template ships with. |

## Supply-chain notes

- `shadcn` installs the `cn` package instead of `clsx` and `tailwind-merge`. It was inspected: the `shadcn` maintainer publishes it from `shadcn-ui/cn`, and it has no install scripts. It is young and pre-1.0.
- The `shadcn` CLI stays in `dependencies` because `index.css` imports its CSS. Only that CSS reaches the build.

## Consequences

- Two toolchains: uv and pnpm. `make` wraps the common commands.
- Keeping the code on NTFS has file-permission implications. See `docs/security/THREAT_MODEL.md`.

## Revisit when

- a measured performance bottleneck appears in Python; or
- the frontend template or its linters change upstream.
