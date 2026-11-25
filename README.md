<p>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/scrinium-mark-dark.svg">
    <img src="docs/brand/scrinium-mark.svg" width="64" alt="Scrinium">
  </picture>
</p>

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

## Run it with Docker

The easiest way: you install only Docker, and one script, [`docker/scrinium.sh`](docker/scrinium.sh), does the rest. It works the same on your own computer and on a server you rent (a VPS).

You need about 2 GB of free disk: the image (about 330 MB) and, the first time, the tools and the search model (about 0.7 GB).

Tried on Linux. On macOS and on Windows (WSL) the steps are the same, but nobody has run them there yet.

### On your own computer

1. **Install Docker.**
   - Windows or macOS: install [Docker Desktop](https://docs.docker.com/desktop/) and open it. On Windows, type every command below in a WSL (Ubuntu) terminal, with Docker Desktop's WSL integration turned on.
   - Linux: install [Docker Engine](https://docs.docker.com/engine/install/), then let your account use it and log out and back in:

     ```bash
     sudo usermod -aG docker $USER
     ```

   To check it, type `docker run --rm hello-world`. You see `Hello from Docker!`.

2. **Get Scrinium.** Type:

   ```bash
   git clone https://github.com/muffindevx/scrinium.git
   ```

   ```bash
   cd scrinium
   ```

3. **Start it, giving it the folder of a project you want explained.** For example, if your project is in `~/code/shop`:

   ```bash
   sh docker/scrinium.sh start ~/code/shop
   ```

   The first time you see, one after the other:

   - `Construyendo la imagen de Scrinium...`: it builds Scrinium, a few minutes;
   - `Comprobando las herramientas y el modelo de busqueda...`: it downloads them, with a percentage;
   - `Scrinium arrancado.` and a link that starts with `http://127.0.0.1:47615/auth/launch?token=`;
   - the line `/repos/shop   (tu carpeta /home/you/code/shop)`.

   The next times it takes seconds.

4. **Open the link in your browser.** It works once and only for 2 minutes. If it expired, or you closed the tab, ask for a new one:

   ```bash
   sh docker/scrinium.sh link
   ```

5. **The first time, choose who writes the rules.** Home asks. Read [Which model writes the rules](#which-model-writes-the-rules) first: in the container, not every choice is available.

6. **Add your project.** In Projects, create a project and add a source with the path the script printed in step 3, for example `/repos/shop`. That is where your folder is inside the container. Scrinium can read it, never change it.

7. **When you finish:**

   ```bash
   sh docker/scrinium.sh stop
   ```

   Your projects and rules stay. To come back, run `sh docker/scrinium.sh start`: it remembers your folders. To add another folder, run `start` with it; the ones you had stay. Changing the folders restarts Scrinium, so it prints a new link.

| To... | Type |
|---|---|
| start, or get the link again | `sh docker/scrinium.sh start` |
| a new entry link | `sh docker/scrinium.sh link` |
| stop | `sh docker/scrinium.sh stop` |
| see what it is doing (Ctrl+C to leave; it keeps running) | `sh docker/scrinium.sh logs` |
| the latest Scrinium (`git pull`, rebuild, restart) | `sh docker/scrinium.sh update` |
| any other `scrinium` command, inside the container | `sh docker/scrinium.sh run doctor` |
| another port, if 47615 is busy | `SCRINIUM_PORT=47620 sh docker/scrinium.sh start` |

**What it creates.** The image `scrinium:local`, the container `scrinium`, the network `scrinium` and the volume `scrinium-data`, which holds the database, the tools and the models. Your folders are mounted read-only, never copied. Scrinium inside the container runs with your user id, not as root, with no Linux capabilities. To remove everything, data included:

```bash
docker rm -f scrinium && docker volume rm scrinium-data && docker network rm scrinium && docker image rm scrinium:local
```

**How the container keeps Scrinium local.** Scrinium only listens on `127.0.0.1` and only answers requests addressed to `127.0.0.1:<port>`. Inside the container, `socat` passes the published port to that address unchanged ([`docker/entrypoint.sh`](docker/entrypoint.sh)), and Docker publishes it only on your computer's `127.0.0.1`. Other machines on your network cannot reach it, and every Host, Origin, session and CSRF check applies as without Docker.

### Which model writes the rules

The image includes **no model**. The first `start` downloads into the volume what search needs: gitleaks, llama.cpp (its CPU build: the container sees no GPU) and the search model. Search by meaning then works.

Writing the rules needs a chat model. Home asks the first time. In the container:

- **A model server on the same machine** (Ollama, llama-server, vLLM): start Scrinium with its port, for Ollama:

  ```bash
  sh docker/scrinium.sh start --model-port 11434
  ```

  In Home choose **local server** with `http://127.0.0.1:11434` and the model's name. Inside the container that address leads to the same port on your computer, and only there. With Docker Desktop that is all. On Linux, the server must also listen on Docker's address, not only on `127.0.0.1`: for Ollama, `OLLAMA_HOST=172.17.0.1:11434` (the address of `docker0`, shown by `ip -4 addr show docker0`).
- **Scrinium's own local model** (2.5 GB more), on the CPU, so slow: hours for a large project.

  ```bash
  sh docker/scrinium.sh run tools install local
  ```

  Then `sh docker/scrinium.sh stop` and `start`.
- **Isolated mode**: no model writes; nothing leaves the machine.
- **Your own provider** (DeepSeek, GLM, Claude…): **not available in the container today.** Scrinium keeps provider keys only in the operating system's keyring and refuses to keep them in plain text ([ADR-006](docs/decisions/ADR-006-secrets.md)); a container has no keyring, so the console says the keyring is unavailable and `sh docker/scrinium.sh run doctor` says `Sin almacen de secretos`. For a provider, install the package ([Getting started](#getting-started)).

### On a VPS

Scrinium is made to be used from the machine it runs on. On a VPS that machine is the server, so you reach it through an **SSH tunnel**: your computer's `127.0.0.1:47615` becomes the server's `127.0.0.1:47615`, encrypted by SSH, and nothing else on the internet can reach it.

**Do not open Scrinium's port to the internet**, and do not add a firewall rule for it: the script publishes it only on the server's `127.0.0.1` on purpose.

1. **Rent a Linux server** (Ubuntu or Debian) with at least 4 GB of memory and 5 GB of free disk. You get an address, for example `203.0.113.10`.

2. **Log in with a normal account, not root.** If you only have root, create one (here `ana`) and log in with it:

   ```bash
   ssh root@203.0.113.10
   ```

   ```bash
   adduser ana && usermod -aG sudo ana
   ```

   ```bash
   exit
   ```

   ```bash
   ssh ana@203.0.113.10
   ```

3. **Install Docker Engine** with the steps for your system ([Ubuntu](https://docs.docker.com/engine/install/ubuntu/), [Debian](https://docs.docker.com/engine/install/debian/)), then let your account use it:

   ```bash
   sudo usermod -aG docker ana
   ```

   Log out (`exit`) and in again (`ssh ana@203.0.113.10`). `docker run --rm hello-world` prints `Hello from Docker!`.

4. **Put Scrinium and your project on the server:**

   ```bash
   git clone https://github.com/muffindevx/scrinium.git
   ```

   ```bash
   git clone https://example.com/you/shop.git
   ```

5. **Start Scrinium:**

   ```bash
   sh scrinium/docker/scrinium.sh start ~/shop
   ```

   It prints the entry link and, because you are connected by SSH, the exact tunnel command for your computer, for example `ssh -N -L 47615:127.0.0.1:47615 ana@203.0.113.10`.

6. **On your computer, open a new terminal and type that tunnel command.** It prints nothing and stays open: that is right. Leave it open while you use Scrinium.

7. **Open the link in your computer's browser.** If it expired, on the server: `sh scrinium/docker/scrinium.sh link`.

Closing the tunnel only cuts your access: Scrinium keeps running on the server, extracting rules if it was. To come back, open the tunnel again and ask for a link.

The port must be the same on both ends, because Scrinium checks it. If 47615 is busy on your computer, start Scrinium on the server with another one, `SCRINIUM_PORT=47620 sh scrinium/docker/scrinium.sh start`, and use the tunnel command it prints.

**Why not a domain with HTTPS behind a reverse proxy (nginx, Caddy, Traefik)?** Scrinium does not support it, and the proxy cannot fix that safely:

- it accepts only the Host `127.0.0.1:<port>`, and every change must come with the Origin `http://127.0.0.1:<port>` ([`runtime/src/scrinium/api/middleware.py`](runtime/src/scrinium/api/middleware.py), tested in `runtime/tests/security/test_host_origin.py`). Behind `https://scrinium.example.com` the browser sends that domain, and Scrinium refuses every change;
- making it work means the proxy rewriting Host and Origin, which switches off the check that stops other websites from acting in your session: the same weakness as development mode ([THREAT_MODEL.md](docs/security/THREAT_MODEL.md));
- it ignores forwarded headers, and its entry link and session were designed for one person on one machine. The threat model lists "a tunnel or proxy you set up yourself" as what would expose it.

The SSH tunnel needs none of that: to Scrinium, you are on its machine.

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
  - Short of space on that disk? `scrinium tools move /path/on/another/disk` (or Settings › AI models) moves llama.cpp and the models there, checking every byte; your data stays where it is. That disk may even be NTFS or exFAT: Scrinium checks those files before using them ([ADR-015](docs/decisions/ADR-015-downloads-folder.md)).
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

### Clone by URL

A Git source can also be an `https://` address: Scrinium clones only the latest commit of the main branch into its data directory, runs nothing from the repository (no checkout, no hooks), and uses this machine's Git credentials for private repositories. Each sync fetches the branch's latest commit first; that is the only network access of a sync, and only for sources added this way.

### Changes, history and triage

- **Changes** compares any two refs of a Git source (branch, tag, commit, or your local changes) with the engine of `scrinium check` and the project's rules: which rules change values, were edited, retired or moved, and which new decisions have no rule yet. It exports business release notes in Markdown.
- **History** (in each rule) says since when its decision has its current values, and before that which ones, by bisection over the Git log of its file: no model, under 2 s for 300 commits.
- **Coverage** shows where the code decides (guards, thresholds, tables, validations) and which decisions have a verified rule, a proposal or none, as tiles per folder; `scrinium coverage` prints the same from the repository and `.scrinium/rules/`, without the database. It also lists sentences in the project's documents whose numbers are not the ones the code decides with.
- **Processes** start at each entry point (an `action_*` handler, an HTTP route, a scheduled job) and list the functions with rules a run goes through, in call order, without a model. Changes and the MCP `check_change` say which processes a changed rule belongs to.
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
- `tools/` and `models/` are symbolic links to the real downloads (or, if you moved them, the demo uses that folder and its integrity record): nothing is copied or downloaded. Run `make tools` first if you have no models.
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
