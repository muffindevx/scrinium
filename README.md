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
- business-rule extraction from code, with every rule citing its lines. On two reference projects (a business API and a game) the candidate filter keeps 94–97 % of the reference rules, extraction finds about 80 % of them, and 96 % of the rules extracted from the game were correct;
- what a pasted diff or bug description touches, and what changed between releases;
- your own model provider if you want one: DeepSeek, GLM, Claude or any OpenAI-compatible server, per project and task, only after you consent. Without that, nothing leaves the machine.

Answering free questions has no screen today: general assistants connected to the repository already do it. The pipeline stays, for a command palette and for agents.

What it does not do yet is described in [Rule extraction](docs/rule-extraction.html#limits).

## Hardware requirements

By default everything runs on your machine. The heaviest task is extracting the rules of a project for the first time. After that, only changed code is read again, which takes seconds or a few minutes. With your own provider, extraction runs at its speed and these tiers matter much less.

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

The zip is reproducible: every timestamp is the last commit's date (`SOURCE_DATE_EPOCH`), so the same commit gives the same bytes, and `dist/SHA256SUMS` sits next to it. `make reproducible` builds it twice and fails if the two differ.

The console is in Spanish or English (Settings › General). The same setting fixes the language the models write new rules in.

Node and the source code are not needed. After unzipping:

```bash
sh install.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer:

1. Installs [uv](https://docs.astral.sh/uv/) if missing, after asking (`--yes` skips the question). It uses a pinned version and checks the SHA-256 of its installer.
2. Installs Scrinium. On Linux with a desktop it adds the Qt window (about 540 MB). `--no-window` skips it, and Scrinium uses the browser instead.
3. Downloads the tools and the search model (`scrinium tools install`, about 0.7 GB).
4. Ends with `scrinium doctor`, which checks the machine and explains how to fix anything missing. It can be run again at any time.

Then run `scrinium start`. The first time, Home asks which model writes the rules: your own provider (recommended), a local server that speaks the OpenAI API (Ollama, vLLM, llama-server) at a loopback address, or **isolated mode**, which blocks every egress and shows it in `security-status` (an organisation can force it with `SCRINIUM_PROFILE=isolated`).

To try Scrinium in five minutes without a model, a GPU or the network, run `scrinium demo`: it creates the example shop with three verified rules, changes one on a branch, and shows `scrinium check` failing on it.

Without the zip, install by hand:

```bash
uv tool install dist/scrinium-0.1.0-py3-none-any.whl -c dist/constraints.txt
```

For the window on Linux, install `"scrinium[window] @ file://$PWD/dist/scrinium-0.1.0-py3-none-any.whl"` instead. Then run `scrinium tools install` and `scrinium start`.

- **Tools and models.**
  - `scrinium tools install` downloads gitleaks, llama.cpp and the embedding model (about 0.7 GB) into the data directory, each pinned and checked by SHA-256. Search by meaning always runs here.
  - `scrinium tools install local` adds Scrinium's own chat model (2.5 GB, best with a GPU), for those who want the rules written on this machine without running a server of their own.
  - On Linux and Windows it picks the GPU build of llama.cpp (Vulkan) when the system supports it. `--cpu` forces the CPU-only build.
  - `scrinium tools status` shows what is installed.
  - `scrinium start` starts llama.cpp itself and stops it when Scrinium closes. Models load on first use.
- **Terminal.**
  - By default it shows only how to stop Scrinium, the entry link, warnings and errors.
  - `scrinium start --verbose` also shows every request and the window details.
- **See everything it does.**
  - With Scrinium open, `scrinium attach` in another terminal streams every request, sync, analysis and the llama.cpp output.
  - Ctrl+C detaches without stopping Scrinium.
  - `--content` adds the questions, prompts and model answers, with secrets hidden. It warns first, because terminal output can stay in its history.
- **Lost the entry link?** `scrinium link` asks the running Scrinium for a new one, without restarting it (`--open` also opens it in the browser). Using it closes the session open in another tab or window.
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
- **Reviewing rules.** The **Bandeja** screen shows one rule at a time, the most valuable to review first (a verified rule whose code now decides with other values, a critical rule many others depend on), with its code beside it, and with the code that was verified when it drifted. Keys: `j`/`k` move, `c` confirms, `r` rejects (the optional reason travels with the rule to the repository), `e` opens the rule, `u` undoes. Near-identical rules come as a group to confirm, reject or merge at once. The consistency review (duplicates, contradictions) runs on its own after each extraction.
- **Verified rules in the repository and in CI.**
  - `scrinium rules export` writes the verified rules of a source into its `.scrinium/rules/`, one file per rule: paths, lines and fingerprints, never code. Commit the folder. `--project` and `--source` choose when there are several.
  - `scrinium check --base main` fails (exit code 1) when the change gives a verified rule's decision other values and does not update that rule's file too. It needs no Scrinium data, no model and no network, and takes under a second on a typical change. `--format sarif` is for code-scanning annotations, `json` for scripts; Markdown is the default. `--include-local` also checks what is not committed; `--staged`, only what is staged for the next commit. In CI, clone with the base branch (`fetch-depth: 0` on GitHub Actions).
  - Edits that may matter but keep the values (a guard that gained a condition) and rules whose code is gone are warnings; `--strict` fails on them too.
  - `scrinium hooks install` runs the check before each commit, on what the commit records (`--staged`); `scrinium hooks uninstall` removes it. An existing shell hook keeps working: Scrinium adds its own marked block and removes only that.
  - `scrinium rules import` rebuilds the reviewed rules from `.scrinium/rules/` on another machine (a clean clone), with the same ids and reviewers. Rejected rules travel too, as tombstones, so extraction does not propose them again.
  - `scrinium mirrors` reads `scrinium.product.yaml` (the product's sources and which rules mirror each other, say the UI's discount limit and the API's) and fails when a pair decides with different values.
  - `--explain` asks the running Scrinium for one sentence per changed rule ("the threshold goes from 100 to 150"), with the model of the project's "Explicación del check" task. It is only a comment: the exit code does not change.
- **Uninstalling.** `scrinium uninstall` stops Scrinium and deletes everything: data, tools, models, keyring credentials and the program. It asks you to type BORRAR. uv stays, because other programs may use it.

### Rules for coding agents (MCP)

`scrinium mcp` is a Model Context Protocol server on stdio with this repository's rules. Add it to an agent from the repository's folder:

```bash
claude mcp add scrinium -- scrinium mcp
```

- `rules_for(path | symbol)`: the rules decided in a file, folder or function, to read before editing.
- `search(query)`: rules and code fragments by words and names.
- `explain_rule(id)`: one rule in full.
- `check_change(base)`: `scrinium check` on the working tree, committed and uncommitted.

Answers are JSON in tiers of trust: `verified` (a person confirmed them), `unverified` (proposals) and `fragments`, each with a notice that the text is repository data, not instructions. The server opens Scrinium's database read-only, works whether the console is open or not, and calls no model and no network. It finds the project whose source is the current folder; `--project NAME` picks another.

In the console, ⌘K (Ctrl+K) searches rules, functions, files and tickets by name.

### Changes, history and triage

- **Changes** compares any two refs of a Git source (branch, tag, commit, or your local changes) with the engine of `scrinium check` and the project's rules: which rules change values, were edited, retired or moved, and which new decisions have no rule yet. It exports business release notes in Markdown.
- **History** (in each rule) says since when its decision has its current values, and before that which ones, by bisection over the Git log of its file: no model, under 2 s for 300 commits.
- **Triage** follows a pasted stack trace (Python, JavaScript or Java/Kotlin) to the project's functions, the rules cited in them, the file's recent commits and its earlier fixes.

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

### A demo instance for trying changes

```bash
make demo
```

- Builds the UI and starts a Scrinium with its own data directory in `~/.cache/scrinium-demo/data` (`SCRINIUM_DEMO_DIR` moves it). Your real projects are never touched.
- `tools/` and `models/` are symbolic links to the real data directory: nothing is copied or downloaded. Run `make tools` first if you have no models.
- The first time, it creates a small example Git repository (`~/.cache/scrinium-demo/repo`: a shop with discounts, shipping and stock, in two commits), the project «Demo» with that repository as its source, and the profile «Usuario de pruebas».
- It opens the browser already signed in, on port 47630. Running `make demo` again while it runs only opens a new entry link; so does `SCRINIUM_DATA_DIR=~/.cache/scrinium-demo/data uv run --project runtime scrinium link --open` (with `uv run`: an installed `scrinium` may be an older copy without `link`).
- Ctrl+C stops it. `rm -rf ~/.cache/scrinium-demo` starts over from scratch (the links do not take the real models with them).

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

## Documentation

Start with the [documentation index](docs/README.md). It gives a reading order for developers:

- [Scope](docs/product.md)
- [Scrinium internals](docs/scrinium-internals.md): architecture, data model, code paths and how to work on the code
- [Rule extraction](docs/rule-extraction.html): what a rule is, when and how it is extracted, and its limits
- [Data flow](docs/data-flow.md), [decisions](docs/decisions/) and [security](docs/security/THREAT_MODEL.md)
