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

4. Open the link in your browser. To extract rules you need an answer model: see [Choose the model](#choose-the-model).

A folder that is not a Git repository is read too, without Changes, rule history or `scrinium check`, which need Git. To add another project, run `start` again with its folder. Scrinium only sees the folders given to `start`, mounted read-only. Projects and rules stay in the Docker volume `scrinium-data` when you stop Scrinium.

### On a server (VPS)

The same steps work on a Linux server with a normal (non-root) account. Scrinium only listens on the server's `127.0.0.1`, so you reach it through an SSH tunnel: when you run `start` over SSH, the script prints the exact tunnel command to run on your computer, for example:

```bash
ssh -N -L 47615:127.0.0.1:47615 you@203.0.113.10
```

Leave it open and open the link in your computer's browser. Do not expose Scrinium's port to the internet or put it behind a reverse proxy: it refuses requests that do not come to `127.0.0.1` ([threat model](docs/security/THREAT_MODEL.md)).

## Install without Docker

For running Scrinium straight on your computer, with its keyring and its GPU. Download the release zip, unzip it and run:

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

Scrinium uses two models:

| Model | What it does | Where it comes from |
|---|---|---|
| **Answer model** | Reads the code and writes the rules. Without it, no rule is extracted. | **You provide it**: a large coding model on your GPU (see [Requirements](#requirements)), or a provider. |
| **Search model** | Finds code and documents by meaning. | Included: `start` downloads it (0.6 GB) and it runs without a GPU. You can change it. |

Set both in **Settings › AI models**, under **1 · Answer model** and **2 · Search model**. Each can be a model server on your machine, a provider, or the included model. Any model works; the names below are examples. If you change the search model, Scrinium indexes the text again in the background and searches by words meanwhile.

### A model server on your machine

Run a model server (Ollama, llama.cpp or vLLM) on your computer, with its GPU, and download a model. With [Ollama](https://ollama.com), for example:

```bash
ollama pull gemma3:27b
```

For the search model, download an embedding model instead, for example `ollama pull nomic-embed-text`.

**With Docker.** The server runs outside the container. On Linux, make it listen on Docker's address (on macOS and Windows, skip this):

```bash
OLLAMA_HOST=172.17.0.1:11434 ollama serve
```

Then start Scrinium with the server's port:

```bash
sh docker/scrinium.sh start --model-port 11434
```

For llama.cpp, use its own port, for example `llama-server -m <model>.gguf --host 172.17.0.1 --port 8080` and `--model-port 8080`.

**Installed on your machine.** Start the server as usual, for example `ollama serve`, or `llama-server -m <model>.gguf --port 8080`.

**Then, in both cases:** in **Settings › AI models**, choose **A server on your computer** and type the address and the model's name as the server lists it. Even with Docker the address stays `http://127.0.0.1:11434`. Press **Test connection**, then **Use this server**, or **Use for search** for the search model.

### A provider

Any OpenAI-compatible API, Claude, DeepSeek or GLM, with your API key. Your code is sent to that provider. Scrinium keeps the key in a keyring and sends nothing until each project gives its consent in its **AI** page.

**With Docker.** The key goes into a keyring inside the container, encrypted with a password you choose and never stored ([ADR-016](docs/decisions/ADR-016-keyring-in-docker.md)). Open it first; the first time, this creates it:

```bash
sh docker/scrinium.sh unlock
```

After every restart the keyring is locked again: run `unlock` again, or type the password when `start` asks.

**Installed on your machine.** The key goes into the operating system's keyring, with nothing to open first.

**Then, in both cases:** in **Settings › AI models**, choose **A provider on the internet** and add it with its key. A provider can also be the search model. Then the indexed text of every project is sent to it, and the console asks you to accept that first.

### The included models

The search model is included and already installed. For the answer model, Scrinium can download a small trial model (2.5 GB) that runs without a GPU. It is slow and misses rules, so use it only to see how Scrinium works.

**With Docker:**

```bash
sh docker/scrinium.sh run tools install local
```

**Installed on your machine:**

```bash
scrinium tools install local
```

Then choose **The included trial model** in **Settings › AI models**.

### Isolated mode

It works the same with Docker and installed on your machine. Turn it on in **Settings › AI models**, and Scrinium itself refuses to register or use any provider, for either model. Models on your machine, search, rules and change alerts keep working. Providers are blocked, and every project's consent is withdrawn. Your providers and their keys are kept, unused. If your answer model was a provider, choose a model server on your machine afterwards.

## Commands

With Docker:

| Command | What it does |
|---|---|
| `sh docker/scrinium.sh start [folder...]` | Starts Scrinium, adds each folder as a project and reads it, prints the entry link |
| `sh docker/scrinium.sh link` | Prints a new entry link |
| `sh docker/scrinium.sh unlock` | Opens the keyring that holds provider keys (creates it the first time) |
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
| `scrinium tools install local` | Also downloads a small answer model, only for trying Scrinium |
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
