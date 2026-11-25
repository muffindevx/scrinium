<p>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/brand/scrinium-mark-dark.svg">
    <img src="docs/brand/scrinium-mark.svg" width="64" alt="Scrinium">
  </picture>
</p>

# Scrinium

Scrinium reads your code and writes down, in plain sentences, the business rules it applies: who can approve an order, when a discount applies, what happens when a payment fails. Every rule points to the line that applies it, a person confirms it, and Scrinium warns you when the code changes it.

It runs on your machine. Your code leaves it only if you choose an external model provider.

Website: https://muffindevx.github.io/scrinium/

## Features

- **Business rules from code.** A language model writes each decision in the code as a rule, with the exact lines that apply it. A rule whose values are not in the code never reaches you.
- **Review inbox.** Confirm, reject or merge proposed rules, one at a time or in groups of near-duplicates. Only confirmed rules count as verified.
- **Rules in the repository.** Verified rules are exported to `.scrinium/rules/` (paths, lines and fingerprints, never code) and imported on another machine.
- **Change checks.** `scrinium check` fails in CI or in a pre-commit hook when a change moves a verified rule's values without updating the rule.
- **Changes between versions.** Which rules a branch, tag or commit changes, with business release notes in Markdown.
- **Rule history.** Since when a rule has its current values, and which ones it had before.
- **Coverage.** Where the code decides something and which of those decisions have a verified rule.
- **Processes.** From each entry point (HTTP route, handler, scheduled job), the rules a run goes through, in order.
- **Triage.** A pasted stack trace leads to the functions, rules and recent commits involved.
- **Search.** By text and by meaning, over code, rules, documents and tickets (Jira, Linear).
- **Rules for coding agents.** `scrinium mcp` gives agents the rules of the repository, verified ones apart from proposals.
- **Private by default.** It listens only on `127.0.0.1`, hides secrets, and sends nothing out unless you give consent per project and task. Isolated mode blocks every egress.
- **Any model.** A local server (llama.cpp, Ollama, vLLM) or your own provider (DeepSeek, GLM, Claude, any OpenAI-compatible API).

## Requirements

Writing the rules is the heavy part: a model reads every place where the code decides something. The first time it reads the whole project; after that, only what changed.

To do it well on your own machine you need a model built for code, not a small general one. A 3–8B model fits in a modest GPU but misses rules and misreads conditions. The tiers below are sized for the open coding models that do this well today.

| | Medium codebase (up to ~250 000 lines) | Large codebase (250 000+ lines, monorepos) |
|---|---|---|
| **Model class** | Dense coding model of 24–32B, 4-bit (for example Qwen3.6-27B or Devstral Small 2) | Coding model of ~80B (mixture of experts), 4-bit (for example Qwen3-Coder-Next) |
| **GPU** | 24–32 GB of VRAM: RTX 3090, RTX 4090, RTX 5090 | 48 GB of VRAM or more: two RTX 3090/4090, RTX PRO 6000 |
| **Or a Mac** | Apple silicon with 32 GB of unified memory or more (M4 Pro/Max) | Apple silicon with 64–128 GB (M4 Max, M3 Ultra) |
| **RAM** | 32 GB | 64 GB |
| **Disk** | 50 GB free, SSD | 100 GB free, SSD |

- Speed depends mostly on the memory bandwidth of the GPU: the faster it reads the model, the sooner the rules are written.
- Rules are written in the background, appear as they are ready, and resume where they stopped if Scrinium closes.
- Without such a machine, use your own provider: your computer then only needs 4 GB of RAM and 5 GB of free disk.
- Model names are examples of size and quality. Scrinium does not fix a model: you choose it.

## Install with Docker

The easiest way. You install only Docker; [`docker/scrinium.sh`](docker/scrinium.sh) does the rest.

1. Install [Docker Desktop](https://docs.docker.com/desktop/) (Windows, macOS) or [Docker Engine](https://docs.docker.com/engine/install/) (Linux). On Windows, run the commands below in a WSL terminal. On Linux, let your account use Docker, then log out and back in:

   ```bash
   sudo usermod -aG docker $USER
   ```

2. Get Scrinium:

   ```bash
   git clone https://github.com/muffindevx/scrinium.git
   ```

   ```bash
   cd scrinium
   ```

3. Start it with the folder of your project:

   ```bash
   sh docker/scrinium.sh start ~/code/your-project
   ```

   The first time it builds the image and downloads the search tools (a few minutes). Your folder becomes a project and Scrinium starts reading it right away. The script prints a one-time link.

4. Open the link in your browser. The first time, choose the model that writes the rules (see [Choose the model](#choose-the-model)).

A folder that is not a Git repository is read too, without Changes, rule history or `scrinium check`, which need Git. To add another project, run `start` again with its folder. Scrinium only sees the folders given to `start`, mounted read-only. Projects and rules stay in the Docker volume `scrinium-data` when you stop Scrinium.

### On a server (VPS)

The same steps work on a Linux server with a normal (non-root) account. Scrinium only listens on the server's `127.0.0.1`, so you reach it through an SSH tunnel: when you run `start` over SSH, the script prints the exact tunnel command to run on your computer, for example:

```bash
ssh -N -L 47615:127.0.0.1:47615 you@203.0.113.10
```

Leave it open and open the link in your computer's browser. Do not expose Scrinium's port to the internet or put it behind a reverse proxy: it refuses requests that do not come to `127.0.0.1` ([threat model](docs/security/THREAT_MODEL.md)).

## Install without Docker

Needed to use **your own model provider**, because Scrinium keeps its API key in the operating system's keyring, which a container does not have. Download the release zip, unzip it and run:

```bash
sh install.sh
```

On Windows:

```powershell
powershell -ExecutionPolicy Bypass -File install.ps1
```

The installer adds [uv](https://docs.astral.sh/uv/) if it is missing (after asking), installs Scrinium and its search tools, and runs `scrinium doctor` to check the machine. Then:

```bash
scrinium start
```

## Choose the model

The first time, Scrinium asks which model writes the rules:

- **A local server** (llama.cpp, Ollama, vLLM) on this machine. With Docker, start Scrinium with the server's port, for Ollama `sh docker/scrinium.sh start --model-port 11434`. The container does not use the GPU, so the model must run outside it. On Linux the server must also listen on Docker's address (for Ollama, `OLLAMA_HOST=172.17.0.1:11434`).
- **Your own provider**, without Docker only. Each project chooses which tasks it may send, after you consent.
- **Isolated mode**: no model writes rules and nothing leaves the machine.

## Commands

With Docker:

| Command | What it does |
|---|---|
| `sh docker/scrinium.sh start [folder...]` | Starts Scrinium, adds each folder as a project and reads it, prints the entry link |
| `sh docker/scrinium.sh link` | Prints a new entry link |
| `sh docker/scrinium.sh stop` | Stops Scrinium; your data stays |
| `sh docker/scrinium.sh logs` | Shows what Scrinium is doing |
| `sh docker/scrinium.sh update` | Updates the code, rebuilds the image and restarts |
| `sh docker/scrinium.sh run <command>` | Runs any `scrinium` command inside the container |

Scrinium:

| Command | What it does |
|---|---|
| `scrinium start` | Starts Scrinium and opens the console |
| `scrinium stop` | Stops every running Scrinium |
| `scrinium link` | A new entry link for the running Scrinium |
| `scrinium doctor` | Checks the machine and says how to fix what is missing |
| `scrinium demo` | Tries Scrinium in five minutes, without a model or network |
| `scrinium tools install` | Downloads the search tools and model |
| `scrinium attach` | Streams everything the running Scrinium does |
| `scrinium rules export` / `import` | Writes verified rules to `.scrinium/rules/`, or reads them back |
| `scrinium check --base main` | Fails when a change moves a verified rule without updating it |
| `scrinium hooks install` | Runs `scrinium check` before each commit |
| `scrinium coverage` | Where the code decides and which decisions have a rule |
| `scrinium mirrors` | Fails when rules that must match across sources do not |
| `scrinium mcp` | MCP server with the repository's rules for coding agents |
| `scrinium clean` | Deletes leftovers and old downloads, never your data |
| `scrinium uninstall` | Removes Scrinium and all its data |

## Documentation

- [Documentation index](docs/README.md)
- [Rule extraction](docs/rule-extraction.html): what a rule is and how it is written
- [Data flow](docs/data-flow.md), [decisions](docs/decisions/) and [security](docs/security/THREAT_MODEL.md)
- [Development](DEVELOPMENT.md)
