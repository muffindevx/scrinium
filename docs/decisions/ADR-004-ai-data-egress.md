# ADR-004: Local AI by default, and data egress

**Status:** accepted, 2026-11-06. Updated on later dates:

- embeddings implemented on 2026-11-07;
- LLM answers implemented later;
- managed llama.cpp on 2026-11-09;
- amended on 2026-11-14 by [ADR-010](ADR-010-remote-model-providers.md): remote providers per project, with consent.

## The question

Answering questions and extracting rules needs a language model and an embedding model. Both see the user's code and tickets. Which models run where, and under what conditions may any data leave the machine?

## What we decided

- The default mode is **`local_only`**. Models run on the user's machine, behind the `LLMProvider` and `EmbeddingProvider` interfaces.
- `require_local` (`application/ai/policy.py`) refuses any provider that is not local. **There is no remote provider in the code.** Adding one requires a new mode with explicit consent that shows the provider, the model and what data may leave. That mode is decided in [ADR-010](ADR-010-remote-model-providers.md); `local_only` stays the default for every project.
- The default runtime is **`llama-server` from llama.cpp, managed by Scrinium**. Ollama remains an alternative.

## How it works

**llama-server (default, `SCRINIUM_MODEL_PROVIDER=llama`)**

- `scrinium start` starts and stops `llama-server`. One process serves chat and embeddings: router mode, with an INI of presets, through the OpenAI-compatible API.
- It listens only on `127.0.0.1`, on a free port, with `--offline`.
- It requires a new API key on every start. The key goes through the environment (`LLAMA_API_KEY`), not the command line, which other users can read.
- On Linux the kernel stops it if Scrinium dies (`PR_SET_PDEATHSIG`).

**Models**

- They are GGUF files from Hugging Face, pinned to a commit and checked by SHA-256. They are installed with `scrinium tools install`:
  - chat: `Qwen3-4B-Instruct-2507-Q4_K_M` (unsloth, 2.5 GB);
  - embeddings: `Qwen3-Embedding-0.6B-Q8_0` (Qwen, 640 MB).
- Fixed context per model: 12 288 tokens for chat (`infrastructure/llm/base.py`) and 8 192 for embeddings. With a longer prompt, llama-server fails instead of silently truncating.

**Embeddings**

- `qwen3-embedding:0.6b` has 1024 dimensions, is multilingual and was also trained on code.
- It can be changed with `SCRINIUM_EMBEDDING_MODEL`. Vectors from a previous model are recomputed automatically.

**Chat model**

- The chat model is `qwen3:4b-instruct`. It can be changed with `SCRINIUM_LLM_MODEL`.
- If a model can reason (`thinking`), Scrinium asks for it and **discards the reasoning**: it is never returned, stored or logged.
- One generation runs at a time (one chat slot).

**Ollama (alternative, `SCRINIUM_MODEL_PROVIDER=ollama`)**

- Scrinium's own instance runs on `127.0.0.1:11435`, with its models in `.models/` inside the project (ignored by git).
- `make ollama` starts it and `make models` downloads the models. The system Ollama is never touched.
- `SCRINIUM_OLLAMA_URL` only accepts loopback, and proxy variables are ignored when talking to Ollama.

## Why

- **Can we know what a remote provider keeps?** Not without reviewing each contract and configuration. The safe default is that nothing leaves.
- **Why a 4B instruct model?** It has to fit next to the embeddings in 6 GB of VRAM.
  - `qwen3:4b`, which always reasons, used up 4 096 tokens on real documents without answering, and took up to 3 minutes.
  - With `instruct`, evaluation went from 14–141 s to 9–29 s per question, with better abstention. (Owner's decision, 2026-11-07.)
- **Why move from Ollama to llama.cpp?**
  - Scrinium controls the whole lifecycle: start, stop, key, port, offline mode and model hashes.
  - One process serves both models.
  - Scrinium can pass the llama.cpp options it needs: grammars for structured output, prompt cache and slots.
- **Why pin models by commit and hash?** A model is code that shapes every answer. A silent update would change results and could be tampered with.

## Alternatives considered

| Option | Why not |
|---|---|
| **A remote API by default** (OpenAI, Anthropic…) | Sends the user's code off the machine. It may be offered later only as an explicit, informed opt-in. |
| **Ollama as the only runtime** | Less control over lifecycle, keys and options. Its own API was the only one that allowed setting the context per request, so it stays as an alternative. |
| **Larger models (7B and up)** | They do not fit with embeddings in 6 GB of VRAM. They would push layers to the CPU and be slower overall. |
| **A 1.7B model for the rule map** | Measured 2.6× faster, but it found 9 of 28 reference rules versus 24 with the 4B. Rejected. |

## Consequences

- Quality and speed are bounded by the user's hardware (see `README.md`, "Hardware requirements").
- First use downloads ~3 GB of models.
- Any future remote provider is a new decision, with its own consent screen and ADR. That decision is [ADR-010](ADR-010-remote-model-providers.md).

## Revisit when

- smaller models match the 4B on the extraction benchmark; or
- a user asks for a remote provider, and the consent design is ready.
