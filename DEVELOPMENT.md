# Development

Scrinium does not accept outside contributions for now. This file is for working on its code.

## Layout

- `runtime/`: the Python application (API, CLI and background jobs). Versions pinned in `runtime/uv.lock`.
- `ui/`: the React and TypeScript interface, built into the runtime. Versions pinned in `ui/pnpm-lock.yaml`.
- `site/`: the landing page (Astro).
- `docker/`, `Dockerfile`: the Docker image and its script.
- `installer/`: the installers shipped in the release zip.
- `docs/`: documentation, decisions and diagrams. Start with [docs/README.md](docs/README.md) and [Scrinium internals](docs/scrinium-internals.md).

Why this layout: [ADR-007](docs/decisions/ADR-007-repository-structure.md).

## Requirements

- [uv](https://docs.astral.sh/uv/), which manages Python 3.12.
- Node and pnpm, to build the UI.
- `make`.

## Run from source

```bash
make setup
```

```bash
make build
```

```bash
make start
```

Options go through `ARGS`, for example `make start ARGS="--port 8000 --no-browser"`. The address is always `127.0.0.1`.

| Command | What it does |
|---|---|
| `make tools` | Downloads llama.cpp, gitleaks and the local models (about 3.2 GB) into the data directory |
| `make check` | Formatting, lint, types and tests for `runtime/` and `ui/`, with a fake keyring |
| `make bench` | Extraction benchmark (`runtime/benchmarks/bench_extraction.py`) |
| `make demo` | A separate Scrinium with an example repository, on port 47630 |
| `make dev` | UI with hot reload, next to `make start` |
| `make release` | Builds `dist/scrinium-<version>.zip` and `dist/SHA256SUMS` |
| `make reproducible` | Builds the release twice and fails if the bytes differ |
| `make ollama`, `make models` | A private Ollama on `127.0.0.1:11435` and its models, as an alternative to llama.cpp |

## Tests that touch the real system

Run them from `runtime/`:

| Command | What it checks |
|---|---|
| `SCRINIUM_TEST_REAL_KEYRING=1 uv run pytest tests/integration/test_real_keyring.py` | Stores a temporary secret in your keyring (service `scrinium-test`) and deletes it |
| `SCRINIUM_TEST_NTFS_DIR=/path/on/ntfs uv run pytest tests/unit/test_data_dir.py` | Scrinium refuses a disk that ignores permissions |
| `SCRINIUM_TEST_LLAMA=1 uv run pytest tests/integration/test_local_models.py -k real` | Starts `llama-server` with the real models. Needs `make tools` |
| `SCRINIUM_TEST_OLLAMA=1 uv run pytest tests/integration/test_search.py -k real` | Search and the LLM with Ollama. Needs `make ollama` running |

The answer evaluation is `tests/evaluations/test_rag_eval.py` (about 4 minutes).

## Ollama instead of llama.cpp

With `SCRINIUM_MODEL_PROVIDER=ollama`, Scrinium uses its own Ollama on `127.0.0.1:11435`, with its models in `.models/` (ignored by git). The system Ollama is never touched. Run `make ollama` in one terminal and, the first time, `make models` in another. The variables are `SCRINIUM_OLLAMA_URL` (loopback only), `SCRINIUM_EMBEDDING_MODEL` and `SCRINIUM_LLM_MODEL`.

## A fake provider

`runtime/devtools/mock_provider.py` is a fake OpenAI-compatible provider (chat and embeddings) over HTTPS, for trying the provider flow without a real key or connection: register it, give a project consent, extract rules, use it for search, turn isolated mode on. It answers at `https://mock-provider.localhost:8443/v1` with a certificate it makes; Scrinium must trust it:

```bash
cd runtime && uv run python devtools/mock_provider.py
```

```bash
SSL_CERT_FILE=/tmp/scrinium-mock/mock-provider.pem uv run scrinium start
```

Then add it in Settings › AI models with any key of 20+ characters; the models are `mock-chat` and `mock-embed`. Its rules are made up. `tests/integration/test_mock_provider.py` runs the same flow. It is not part of the product.

## Database migrations

Scrinium applies migrations at startup. After changing `runtime/src/scrinium/infrastructure/sqlite/models.py`, run from `runtime/`:

```bash
SCRINIUM_DATA_DIR=/tmp/scrinium-migrations uv run alembic revision --autogenerate -m "description"
```

Use a temporary directory, never your real data, and review the generated file: Alembic does not detect triggers or data. `test_migrations_match_the_models` fails if the models and the migrations disagree.

## Demo instance

`make demo` starts a Scrinium with its own data in `~/.cache/scrinium-demo/data` (`SCRINIUM_DEMO_DIR` moves it), so your real projects are never touched. Its `tools/` and `models/` link to the real downloads. The first time it creates an example shop repository and a project for it, and opens the browser signed in. `rm -rf ~/.cache/scrinium-demo` starts over.

## Working on the UI

Run `make start` in one terminal and `make dev` in another. Sign in with the link from `make start`, then open `http://127.0.0.1:5173`. Use this mode only for development: see "Development mode" in [THREAT_MODEL.md](docs/security/THREAT_MODEL.md).

## Notes for the reference machine

- **NTFS.** The project sits on an NTFS disk, where file permissions have no effect. Private data goes in `~/.local/share/scrinium`.
- **pnpm store.** It lives in `/media/gris/GRIS/.pnpm-store`, outside the project, because pnpm cannot create hard links across disks.
