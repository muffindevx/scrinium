# Scrinium

Local-first software change intelligence. It explains:

- what changed;
- what evidence supports it;
- what may be affected;
- what we still do not know.

**Status:** early. What works today:

- the secure local runtime, a status screen and projects;
- data-protection checks;
- ingestion of Git repositories and local document folders, with citations to exact lines;
- search by text and by meaning, with local models;
- questions about your sources: the local LLM answers citing every claim, or says it has no evidence;
- business-rule extraction from code.

What is still open is listed in [Scrinium internals](docs/scrinium-internals.md) and in each document's limits.

## Hardware requirements

Everything runs on your machine. The heaviest task is extracting the rules of a project for the first time. After that, only changed code is read again, which takes seconds or a few minutes. Questions work on all three tiers.

| Tier | Machine | First extraction of a project like `reference-a` (~50 000 lines, ~500 rules) |
|---|---|---|
| **Minimum** | 16 GB of RAM, 4-core CPU with AVX2, no dedicated GPU, ~5 GB free disk | Hours (estimated, not measured). Runs in the background: it can be paused and resumes when Scrinium opens again |
| **Recommended** | GPU with 6 GB of VRAM and ≥ 190 GB/s (such as a GTX 1060 6 GB or an RX 580 8 GB) and 16 GB of RAM | **Rule map in ~21 min, then statements in ~18 min more, in the background (measured)** |
| **Ideal** | GPU with 8–12 GB of VRAM and ≥ 300 GB/s with tensor cores (RTX 3060 12 GB, RTX 4060 Ti), or an Apple M Pro or Max Mac, and 32 GB of RAM | ~15 min in total (estimated, not measured) |

- Rules appear while they are being extracted. A rule opened before it has its statement gets it written on the spot.
- Base Apple M1 and M2 Macs have less memory bandwidth than a GTX 1060. They sit between minimum and recommended.
- Measured with `make bench` (`runtime/benchmarks/bench_extraction.py`). To validate another tier, run it on that machine.

## Getting started

### Install the package

`make release` builds `dist/scrinium-0.1.0.zip` with everything a tester needs:

- the package and its pinned dependencies;
- the installers;
- `README.txt` and `LEEME.txt`, the end-user readme in English and Spanish.

Node and the source code are not needed. After unzipping:

```bash
sh install.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer:

1. Installs [uv](https://docs.astral.sh/uv/) if missing, after asking (`--si` skips the question). It uses a pinned version and checks the SHA-256 of its installer.
2. Installs Scrinium. On Linux with a desktop it adds the Qt window (about 540 MB). `--sin-ventana` skips it, and Scrinium uses the browser instead.
3. Downloads the tools and models (`scrinium tools install`).
4. Ends with `scrinium doctor`, which checks the machine and explains how to fix anything missing. It can be run again at any time.

Then run `scrinium start`.

Without the zip, install by hand:

```bash
uv tool install dist/scrinium-0.1.0-py3-none-any.whl -c dist/constraints.txt
```

For the window on Linux, install `"scrinium[window] @ file://$PWD/dist/scrinium-0.1.0-py3-none-any.whl"` instead. Then run `scrinium tools install` and `scrinium start`.

- **Tools and models.**
  - `scrinium tools install` downloads gitleaks, llama.cpp and the two local models (about 3.2 GB) into the data directory, each pinned and checked by SHA-256.
  - On Linux and Windows it picks the GPU build of llama.cpp (Vulkan) when the system supports it. `--cpu` forces the CPU-only build.
  - `scrinium tools status` shows what is installed.
  - `scrinium start` starts llama.cpp itself and stops it when Scrinium closes. Models load on first use.
- **Terminal.**
  - By default it shows only how to stop Scrinium, the entry link, warnings and errors.
  - `scrinium start --verbose` also shows every request and the window details.
- **See everything it does.**
  - With Scrinium open, `scrinium attach` in another terminal streams every request, sync, analysis and the llama.cpp output.
  - Ctrl+C detaches without stopping Scrinium.
  - `--contenido` adds the questions, prompts and model answers, with secrets hidden. It warns first, because terminal output can stay in its history.
- **Stopping.** Do any of these:
  - close the window;
  - press Ctrl+C in the terminal where it started;
  - run `scrinium stop` from any terminal, which stops every running Scrinium and its llama.cpp.

  Closing the browser tab does not stop it.
- **Where it lives.**
  - The program goes in `~/.local/share/uv/tools/scrinium` (`%APPDATA%\uv\tools\scrinium` on Windows).
  - The `scrinium` command goes in `~/.local/bin`.
  - Your data is kept separately:

    | OS | Data directory |
    |---|---|
    | Linux | `~/.local/share/scrinium` |
    | macOS | `~/Library/Application Support/scrinium` |
    | Windows | `%LOCALAPPDATA%\scrinium` |
- **Cleaning up.** `scrinium clean` deletes:
  - half-finished downloads;
  - old versions of tools and models;
  - leftovers from stopped processes.

  It shows how much space it frees and asks for confirmation. It never touches projects, rules or questions.
- **Uninstalling.** `scrinium uninstall` stops Scrinium and deletes everything: data, tools, models, keyring credentials and the program. It asks you to type BORRAR. uv stays, because other programs may use it.

### From source

You need uv, Node, pnpm and `make`. To search and ask questions, `make tools` downloads llama.cpp and the local models. [Ollama](https://ollama.com) is the alternative: see [Development](#development).

```bash
make setup
```

```bash
make build
```

```bash
make start
```

Scrinium only accepts connections from this machine. Verification and development commands are in [Development](#development).

## Development

### Layout

- `runtime/`: the Python application (API, CLI and background jobs). Versions pinned in `runtime/uv.lock`.
- `ui/`: the React and TypeScript interface, built into the runtime. Versions pinned in `ui/pnpm-lock.yaml`.
- `docs/`: documentation, decisions and diagrams.

Why this layout: [ADR-007](docs/decisions/ADR-007-repository-structure.md).

### Requirements

- [uv](https://docs.astral.sh/uv/), which manages Python 3.12.
- Node and pnpm, to build the UI.
- `make`, for the root commands.

### Install and run

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

### Verify

```bash
make check
```

It runs formatting, lint, types and tests for `runtime/` and `ui/`, with a fake keyring.

Optional tests touch the real system. Run them from `runtime/`:

| Command | What it checks |
|---|---|
| `SCRINIUM_TEST_REAL_KEYRING=1 uv run pytest tests/integration/test_real_keyring.py` | Stores a temporary secret in your keyring (service `scrinium-test`) and deletes it |
| `SCRINIUM_TEST_NTFS_DIR=/path/on/ntfs uv run pytest tests/unit/test_data_dir.py` | Scrinium refuses a disk that ignores permissions |
| `SCRINIUM_TEST_LLAMA=1 uv run pytest tests/integration/test_local_models.py -k real` | Starts `llama-server` with the real models and checks embeddings and chat. Needs `make tools` |
| `SCRINIUM_TEST_OLLAMA=1 uv run pytest tests/integration/test_search.py -k real` | Search and the LLM with Ollama. Needs `make ollama` running |

The answer evaluation is in `tests/evaluations/test_rag_eval.py` (about 4 minutes). The extraction benchmark is `make bench`.

### Local models

By default Scrinium starts and stops its own `llama-server`. The first time, download llama.cpp, gitleaks and the models (about 3.2 GB) into the data directory:

```bash
make tools
```

Without them, search only matches exact text, and questions fail with "the local model is not available". `make start ARGS=--verbose` shows what `llama-server` is doing.

### Alternative: Ollama

With `SCRINIUM_MODEL_PROVIDER=ollama`, Scrinium uses its own Ollama:

- it listens on `127.0.0.1:11435`;
- its models live inside the project, in `.models/` (about 3 GB, ignored by git);
- the system Ollama is never touched.

Start it in a separate terminal:

```bash
make ollama
```

The first time, with `make ollama` running, download the models:

```bash
make models
```

This downloads `qwen3-embedding:0.6b` (search by meaning) and `qwen3:4b-instruct` (answers). The variables are `SCRINIUM_OLLAMA_URL` (loopback only), `SCRINIUM_EMBEDDING_MODEL` and `SCRINIUM_LLM_MODEL`.

### Database migrations

Scrinium applies migrations at startup. To create one after changing `runtime/src/scrinium/infrastructure/sqlite/models.py`, run this from `runtime/`:

```bash
SCRINIUM_DATA_DIR=/tmp/scrinium-migrations uv run alembic revision --autogenerate -m "description"
```

Use a temporary directory, never your real data, and review the generated file: Alembic does not detect triggers or data. `test_migrations_match_the_models` fails if the models and the migrations disagree.

### Working on the UI

Hot reload needs two terminals. In the first:

```bash
make start
```

In the second:

```bash
make dev
```

Sign in first with the link from `make start`, then open `http://127.0.0.1:5173`. Use this mode only for development: see "Development mode" in [THREAT_MODEL.md](docs/security/THREAT_MODEL.md).

### Notes for the reference machine

- **NTFS.** The project sits on an NTFS disk, where file permissions have no effect. Private data goes in `~/.local/share/scrinium`.
- **pnpm store.** It lives in `/media/gris/GRIS/.pnpm-store`, outside the project, because pnpm cannot create hard links across disks.

### Not in git

Plans, proposals, handoffs and implementation notes are kept out of git (see `.gitignore`).

## Documentation

- [Scope](docs/product.md)
- [Threat model](docs/security/THREAT_MODEL.md) and [security glossary](docs/security/SECURITY_GLOSSARY.md)
- [Data flow](docs/data-flow.md) and [decisions](docs/decisions/)
- [Scrinium internals](docs/scrinium-internals.md): architecture, data model and how to work on the code
- [Rule extraction](docs/rule-extraction.html): what a rule is, when and how it is extracted, and its limits
