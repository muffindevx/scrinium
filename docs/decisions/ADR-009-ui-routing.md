# ADR-009: react-router for the UI screens

**Status:** accepted and implemented in T-016, 2026-11-07. Owner's decision. Routes renamed to Spanish later, matching the UI language.

## The question

T-016 replaced a single page of stacked cards with separate screens, each with linkable detail views: rules, flows and analyses. How should the UI navigate between screens so that links can be shared, reloaded and bookmarked?

## What we decided

- **`react-router` 7.18.5**, in library mode (`BrowserRouter`), with **real paths**.
- The current routes are in `ui/src/App.tsx`. They are in Spanish, like the rest of the UI:

| Path | Screen |
|---|---|
| `/` | Home |
| `/preguntar` | Ask |
| `/reglas/:entityId?` | Rules |
| `/mapa/:entityId?` | System map |
| `/impacto/:analysisId?` | Impact analysis |
| `/versiones/:analysisId?` | Release comparison |
| `/fuentes` | Sources |
| `/ajustes` | Settings |

- Any other path redirects to `/`.

## How it works

- The runtime serves the built UI with `InterfaceFiles` (`api/static.py`).
- When a request matches a UI route prefix, it returns `index.html` so the router can render the screen. This makes reloading a screen work.
- The prefixes are a closed set, `UI_ROUTES`. Any other missing path still returns 404, and the static handler never serves anything outside its folder. `tests/security/test_static_files.py` covers both.

## Why

- **Why real paths and not the hash?** Real paths read better, and can be shared and opened directly.
- **Why a router library at all?** Nested detail views, history and active links are routing work. Hand-rolling them would recreate a router badly.
- **Why library mode and not a framework?** The runtime already serves the UI. A framework with its own server would add a second process and a second security boundary.
- **Why a closed list of fallback prefixes?** A catch-all that returns `index.html` for any path would hide real 404s, and would make path-traversal tests meaningless.

## Alternatives considered

| Option | Why not |
|---|---|
| **Hash tabs (`#/rules`), no dependency** | It was the recommended option, with no new dependency. The owner preferred real paths. |
| **A framework with its own server** (Next.js, Remix) | The runtime already serves the UI. It would mean another process to run and secure. |
| **Catch-all fallback to `index.html`** | It hides real 404s and weakens the static-file tests. |

## Consequences

- Adding a screen means adding its path in **two places**: `ui/src/App.tsx` and `UI_ROUTES` in `api/static.py`.
- The comment in `api/static.py` still points to `ui/src/routes.tsx`, which does not exist. The routes are in `ui/src/App.tsx`.

## Revisit when

- the number of screens grows enough that the duplicated prefix list causes mistakes (generate it from one source); or
- the UI needs server-side rendering.
