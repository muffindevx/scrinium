# Supply chain

How Scrinium's dependencies are checked, updated and rolled back.

The checks need the network, because they query vulnerability databases, so they are not part of `make check`. Run them by hand:

- before each release;
- after each update.

## Checks

| Command | What it does | Fails if |
|---|---|---|
| `make audit` | `uv audit` of the shipped Python dependencies and `pnpm audit --prod` of the UI. Then a full `pnpm audit`, as a report only | There is a known vulnerability in something that ships |
| `make sbom` | CycloneDX SBOM of what ships: `sbom/runtime.cdx.json` (`uv export`) and `sbom/ui.cdx.json` (`pnpm sbom --prod`). `sbom/` is not versioned | The lockfile cannot be read |
| `make secrets` | Scans Scrinium's code with gitleaks. The rules are in `.gitleaks.toml`: default rules, no dependencies or build output, and only the known fake credentials allowed in tests | It finds a secret, **or gitleaks is not installed**: not having scanned does not count as passing |

**Installing gitleaks.** It is not installed with the project for development. `scrinium tools install` downloads a pinned copy into the data directory. To install it yourself:

1. Download the build for your system from the official releases at `github.com/gitleaks/gitleaks`.
2. Check its SHA-256 against the checksums file of that same release.
3. Put the binary on your `PATH`.

## Status as of 2026-11-07

- **`make audit`:** no known vulnerabilities in what ships: 43 Python packages, and the production UI.
- **Accepted risk, development only:** `pnpm audit` reports `deepmerge` (low, no fixed version) and `minimatch` (moderate).
  - They come through the `shadcn` CLI, which is only used to generate components.
  - The UI only imports one CSS file from that package at build time. The vulnerable code never runs and is not in the bundle (verified). That is why `shadcn` is a development dependency.
- **`make secrets`:** not run, because gitleaks was not installed on the development machine.
  - There was a provisional check with Scrinium's own detector over the code, with no findings.
  - That detector only recognizes known formats, and does not replace gitleaks.

## Added on 2026-11-14

- **`anthropic` 2.3.0**, for Claude as a remote provider ([ADR-010](../decisions/ADR-010-remote-model-providers.md)). It is only used by projects whose person consented. It brings `httpx2`/`httpcore2` (its HTTP client), `truststore` (the system's certificate store), `jiter`, `sniffio` and `docstring-parser`; `httpx2-jsfetch` is in the lockfile only for browsers (emscripten) and is never installed here.
- **`jiter` is held below 0.19** (`[tool.uv] constraint-dependencies`): 0.19.0 was eight days old. Lift the constraint once it is two weeks old.
- `make audit` (2026-11-14): no known vulnerabilities in the 79 shipped Python packages; the UI reports only the known development-only `deepmerge` and `minimatch`. `make sbom` was not run.

## Added on 2026-11-18

- **`react-call` 2.0.2** (MIT, UI), to open dialogs from anywhere with `await confirm.call({...})` (`ui/src/components/ui/confirm.tsx`). No dependencies of its own; `react-dom` and `vite` are optional peers already in the project. Pinned exactly. Released 2026-06-21.

## Updating dependencies

1. Start from a green `make check`.
2. Update in a branch or a copy. Prefer one package at a time over everything at once:
   - Python: `cd runtime && uv lock --upgrade-package <package>`;
   - UI: `pnpm --dir ui update <package>`.
3. Review the lockfile change:
   - new versions;
   - unexpected new dependencies;
   - in Python especially, anything that brings network clients.
4. Check that the version is at least two weeks old, unless it is an urgent security patch.
5. Run `make check`, `make audit` and `make sbom`. With the models installed, also run the evaluations against the real model: `SCRINIUM_TEST_LLAMA=1`, or `SCRINIUM_TEST_OLLAMA=1` for Ollama.
6. Record what changed and why in the commit message.

## Rolling back

Dependencies are pinned in `runtime/uv.lock` and `ui/pnpm-lock.yaml`. Rolling back means restoring the previous lockfiles and reinstalling with `make setup`.

If an update included a database migration, Scrinium will not start with a database from a newer version (`IncompatibleDatabaseError`). To go back, restore an export made with the previous version.

## Reporting a vulnerability

See `SECURITY.md` at the root of the project.
