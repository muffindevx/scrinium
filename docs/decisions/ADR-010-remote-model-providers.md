# ADR-010: Remote model providers, by project and with consent

**Status:** accepted, 2026-11-14. The owner made these decisions:

- which providers;
- embeddings stay local;
- consent per project;
- a failed attempt is handled per batch;
- retention is shown when a provider and model are chosen;
- one contract for every provider;
- keys never in plain text.

Not implemented yet. Amends [ADR-001](ADR-001-local-first.md) and [ADR-004](ADR-004-ai-data-egress.md).

## The question

Scrinium runs its models on the user's machine ([ADR-004](ADR-004-ai-data-egress.md)). On the owner's machine that means a 4B model on a GTX 1060:

- it is slow (~40 minutes to extract rules from a 50 000-line project);
- it misses rules it sees (Q2 in the plan);
- larger local models do not fit.

ADR-004 left the door open: a remote provider "may be offered later only as an explicit, informed opt-in", with "its own consent screen and ADR". This is that ADR.

The questions:

1. **Which data may leave, to whom, and who decides?**
2. **How do we keep it out of projects or tasks where nobody decided it?**
3. **How do we spend as few tokens as possible, and reduce what a third party learns?**
4. **How do different providers behave as one system, the same as the local model?**
5. **Where does an API key live so it cannot leak?**
6. **What does this leave uncovered?**

## What we decided

1. **`local_only` stays the default, for every project.** Nothing changes for a project until a person turns a provider on for it.
2. **Consent is per project and per task.**
   - A project can be switched to `remote_allowed` for named tasks and a named provider and model.
   - Before that, the person sees what leaves, where it goes, and **what the provider says it keeps and for how long**.
   - A sensitive repository can stay local while another project uses a remote model.
3. **Two kinds of provider in the first version:**
   - **OpenAI-compatible over HTTPS:** DeepSeek, GLM and any API that speaks `/v1/chat/completions`. Scrinium's own small client.
   - **Anthropic (Claude):** the official `anthropic` Python SDK.
4. **Embeddings are always local.** Search by meaning reads the whole indexed project; sending it would mean sending the repository. Only the passages a task needs ever leave.
5. **A model per task, per project (M2):** questions, the rule map, rule statements, consistency review and change impact. Each is local by default.
6. **One contract.** Every provider is an adapter behind the same `LLMProvider` interface. Each adapter turns its provider's answer into the same `Completion`, with a normalized outcome, normalized token usage and the model that really answered. Nothing above the adapter knows which provider ran.
7. **A failed attempt is a failed batch.** Overflow, refusal, invalid JSON, timeout and provider errors all follow the rule extraction already has (F9):
   - the batch is split in two and retried;
   - a single fragment that still fails is left for later, with its reason.

   No request is silently retried on another model or provider.
8. **Few tokens, by design.** See "Spending as few tokens as possible": minimal input, a stable cacheable prefix, no repeated requests (T5), tight output limits, a budget per project (T6), and an estimate before a run.
9. **Less exposure to a third party.** See "Reducing what a third party learns":
   - only the needed passages leave;
   - secrets are redacted;
   - paths can be marked "never send";
   - optional masking of personal data and literals;
   - a local record of every request: which files and lines left, not their content.
10. **API keys never in plain text.** They live only in the OS keyring ([ADR-006](ADR-006-secrets.md)). A key is tied to the host it was registered for, never shown again, never logged or exported. Without a secure keyring, remote providers cannot be registered.
11. **Honest privacy surfaces.** The "100% local y privado" pill, the data-flow endpoint and the "Nada sale de aquí" texts show the real mode of the current project. In a remote project they name the provider and its host.
12. **Provenance:**
    - every rule and answer shows the provider and model that produced it (N6);
    - changing a model warns that rules from different models will coexist (M6), and re-extraction is offered.

## How it works

### Modes and consent

- `AiMode` gains `REMOTE_ALLOWED` next to `LOCAL_ONLY`. The mode belongs to the **project**, not to the app.
- `require_local` keeps its meaning. A `local_only` project can only build local providers; asking it for a remote one raises `RemoteProviderNotAllowedError`, as today.
- **Turning a remote model on records a consent:**
  - project, provider, host and model;
  - tasks;
  - the retention notice shown, with its date;
  - person (local profile) and date.

  The consent goes to the audit log too. It can be revoked at any time: the project returns to `local_only`. What Scrinium already stored is kept; what the provider received cannot be taken back, and the screen says so.

### Choosing a provider and a model: retention is part of the choice

When a person picks a provider and a model, the same screen shows, before anything is sent:

- **What leaves, per task:**
  - the system prompt;
  - the passages or candidate functions of each request;
  - the question text, for questions.
- **What never leaves:**
  - the rest of the repository;
  - vectors;
  - other projects;
  - files excluded as sensitive;
  - paths marked "never send".
- **What the provider says it keeps:**
  - whether it keeps prompts and answers, and for how long;
  - whether it trains on them;
  - where it processes them, if it says;
  - a link to its terms.

Retention notices come from a **catalog shipped with Scrinium**, one entry per known provider and, where it differs, per model. Each entry has:

- the notice in plain words;
- the link to the source;
- the date it was checked.

**Scrinium never states retention it has not checked.** A provider or model without a checked entry, including any custom OpenAI-compatible host, shows "Retención no verificada por Scrinium: lee sus términos" with the link the person gives.

Rules for showing the notice:

- If the catalog entry is older than six months, it is shown as possibly stale.
- The person must acknowledge the notice to finish the consent.
- A change of model shows the notice of the new model; for example, some newer Claude models require 30-day retention.

### One contract for every provider

The contract lives in Python, in `infrastructure/llm/base.py`, and conformance tests check it. It is not a separate YAML file. Typed code and its tests cannot drift apart from what runs; a YAML description beside them could. The contract is also written out for people in `docs/scrinium-internals.md`.

**What a task sends** (unchanged for local and remote):

- `messages`: a constant system prompt, then the user turn, which starts with the per-request key line (`key_line(nonce)`);
- `max_tokens`: set per task from the expected output, never the provider's maximum;
- `json_schema`: the task's output schema.

**What every adapter returns, a `Completion`:**

| Field | Meaning |
|---|---|
| `text` | The answer's text only. Never reasoning or thinking text: adapters drop it. |
| `outcome` | `completed`, `length` (ran out of output tokens), `refused` (the provider declined), `invalid` (no JSON that matches the schema) or `error`. Adapters translate their provider's stop reasons into these. |
| `model_id` | `provider:model` of the model that **actually** answered. |
| `prompt_tokens` | All input tokens, cached or not. |
| `cached_tokens` | The input tokens served from the provider's cache. |
| `completion_tokens` | Output tokens, including any reasoning the provider bills. |
| `duration_ms` | Wall time of the request. |
| `request_id` | The provider's request id, if any, for support. Never content. |

**Errors** are `ModelError` subclasses that never carry the text sent or received, as today:

- `OutputBudgetExceededError`;
- `ModelRefusedError` (new);
- `ProviderUnavailableError` (new, for network, rate limit and 5xx).

The analysis code turns any of them into a failed batch.

**Conformance tests** feed each adapter recorded provider responses (a fixture per case: completed, length, refusal, invalid JSON, rate limit, server error). They assert the same `Completion` or error comes out. A new provider is accepted only when it passes them.

**Provider-specific mapping, in the adapters only:**

- **OpenAI-compatible:**
  - sends `response_format` with the JSON schema;
  - reads `finish_reason` (`stop` → `completed`, `length` → `length`, `content_filter` → `refused`);
  - reads `usage.prompt_tokens`, `usage.completion_tokens` and `usage.prompt_tokens_details.cached_tokens`;
  - drops `reasoning_content` and think tags.
- **Anthropic:**
  - sends `output_config.format` with the JSON schema and sets `output_config.effort` per task, explicitly (the default differs between models);
  - reads `stop_reason` (`end_turn` → `completed`, `max_tokens` → `length`, `refusal` → `refused`);
  - computes input as `input_tokens` + `cache_creation_input_tokens` + `cache_read_input_tokens`, because Anthropic's `input_tokens` leaves out cached tokens; `cached` is `cache_read_input_tokens`;
  - drops thinking blocks;
  - sends no server-side fallback: a refusal is a refused batch (decision 7).
- **Local llama-server and Ollama:** already map to this. Only `outcome` and `request_id` are new.

The task's JSON schema is the same for every provider. Scrinium already validates every answer against it and drops what does not fit, so a provider that only honours "JSON mode" still works, with more dropped answers.

### Spending as few tokens as possible

Most of this already exists for the local model, where tokens cost time instead of money:

- **Only what the task needs:**
  - extraction sends candidate functions that the code filter scored, not files;
  - questions send the retrieved passages, not documents;
  - tests and excluded files are never sent;
  - indentation and blank lines are already compacted.
- **A stable prefix, so the provider caches it.** The system prompt is constant, and the per-request key goes in the first user line. Providers that cache prefixes (Anthropic, DeepSeek and others) then charge cached input at a fraction of the price.
- **No repeated requests (T5).** A request with the same model, prompt version and input hash reuses the stored output. Runs already store `input_hash`.
- **Tight output limits.** `max_tokens` comes from what each batch should produce, as `output_tokens()` already computes, so a confused model cannot run up a long answer.
- **Cheap work stays local.** The code filter that picks candidates and the embeddings never use a provider. The person can keep cheap tasks local, such as consistency review, and send only the ones that need a stronger model.
- **Effort per task** for models that reason (Anthropic's `effort`): low for the rule map, which is many short classifications; higher only where quality measurably needs it.
- **Request limits stay those of the local model** in the first version (`MAX_INPUT_CHARS`, `CONTEXT_TOKENS`), so batches and benchmarks stay comparable. Larger batches would cut the overhead of repeated instructions. That is a measured, later decision.
- **Budget and estimate (T6):**
  - each project has a monthly token budget for remote calls;
  - before an extraction, Scrinium shows the expected input and output tokens, from the candidates it will send;
  - when the budget is reached, background work for that project stops with a clear status, and questions ask first;
  - prices change, so Scrinium counts tokens; a person may type a price per million tokens to see an estimate.
- **Not in the first version, worth measuring:**
  - asynchronous batch APIs, which are cheaper for background extraction (Anthropic's Message Batches);
  - a local model as a first pass that decides what deserves a remote call (V3 in the plan).

### Reducing what a third party learns

A person who uses a provider must assume that whatever is sent is in the hands of a third party. Scrinium cannot change that. It can make what is sent smaller and less revealing:

1. **Minimal passages.** Each request carries only the lines that task needs (see above), never whole files or the repository.
2. **Secrets redacted at the edge.** Ingestion already leaves out files that look like they hold secrets. On top of that, every message sent to a remote model goes through `redact_tokens`. That includes question passages, which are sent unredacted today because they never leave.
3. **"Never send" paths.** Each project can mark paths that are indexed and searchable locally but are never sent to a provider: a `contracts/` folder, customer data fixtures, a payments module. A task whose input would include them sends the rest, or runs locally for those parts. The record says which.
4. **Masking personal data and literals (optional, per project, off by default).** Before sending, Scrinium replaces:
   - email addresses, phone numbers, IP addresses and URLs with credentials;
   - optionally, long string literals.

   Each one becomes a stable placeholder (`<EMAIL_1>`). The map stays local, and placeholders in the answer are put back before storing. Identifiers (function, variable and table names) are **not** masked: rules are about them, and citations come from the code by line, so masking them would cost quality for little gain.
5. **A local record of every request.** Every remote call stores:
   - provider, model and project;
   - date;
   - token counts;
   - the list of files and line ranges that left.

   It never stores the content itself; that is already in the index. The privacy card lists it and can export it, so a person can answer exactly what a provider received.
6. **Choosing the provider is part of the protection.** The retention notice lets the person prefer providers or plans that keep less: zero retention, no training, a region. An organization's own enterprise contract with a provider can be used by registering its key and host.

### API keys

The requirement: an API key is never stored in plain text and cannot leak through Scrinium.

**Where it lives: the OS keyring.** Secret Service or libsecret, the macOS Keychain, or the Windows Credential Manager, behind `SecretStore` ([ADR-006](ADR-006-secrets.md)). These encrypt secrets at rest with a key derived from the user's login and unlock them with the session. The database keeps only an opaque reference (`keyring:<hex>`). Without a secure backend, a remote provider cannot be registered; there is no plain-text fallback.

**Why not our own public/private-key encryption?** The private key would have to live on the same machine, readable by the same user, so that Scrinium can decrypt the API key to use it. Whoever can read Scrinium's files could read that private key too. It would move the problem without solving it, and add our own cryptography to review. The keyring is the operating system's answer to exactly this problem, with the login as the root secret.

**What Scrinium adds on top:**

- **Write-only.** The key is typed once. The API never returns it; the UI shows only that a key is set and its last four characters.
- **Tied to its host.** The key is registered together with its provider's host. An adapter sends it only to that host over HTTPS with certificate checks. Changing a provider's base URL requires typing the key again, so a changed URL cannot carry an existing key elsewhere.
- **Read when needed.** The key is read from the keyring for each request and not kept in memory beyond it, nor passed to child processes or written to environment variables.
- **Never in logs, errors or exports.** Key-shaped strings (`sk-…` and the providers' known formats) are part of the patterns that `redact_tokens` and the log filter already apply. `ModelError` never carries request content. Privacy exports leave out `secret_ref`, as they do today.
- **No ambient credentials.** Scrinium never uses keys from the environment (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) or SDK profiles. Only a key registered in Scrinium is used.
- **Revocation.** Removing a provider deletes its key from the keyring and its reference from the database. The screen recommends setting a spending limit on the provider's side as well.

What remains: malware running as the same user, with the session unlocked, can ask the keyring for the key, as it can read the code itself. `THREAT_MODEL.md` already accepts this for the local machine (ADR-001).

### What changes in the application layer

- `LlmService` stops being one object for the whole app (`main.py`). A small resolver picks the provider for a project and a task. The local provider stays shared.
- `MemoryGuard` and the one-generation-at-a-time rule apply only to the local model. Remote calls also run one at a time in the first version.
- A new `RemoteHttpsClient`, separate from `LocalHttpClient`:
  - verifies certificates and limits response size;
  - honours `HTTPS_PROXY`, like the tools installer already does, because corporate networks need it.

  `LocalHttpClient` is not touched: it still accepts only `http` to loopback and ignores proxies.

### What stays the same

- Ingestion, indexing, embeddings, the FTS index and vectors never involve a remote provider.
- Citations are still checked against the code, and extraction citations come from the code, not the model.
- Answers are plain text, the model has no tools, and sources sit between random per-request delimiters (see the prompt-injection row of `THREAT_MODEL.md`).

## Why

- **Why let anything leave at all?** On the hardware this project targets, the local model makes the product slow and incomplete. The owner reviewed its rules and decided, on 2026-11-13, that a stronger model is needed. A person who accepts sending passages of their code to a provider they trust should be able to.
- **Why per project and per task, and not one switch?**
  - Trust is about a repository, not about Scrinium.
  - Tasks send very different amounts: a question sends a handful of passages, the rule map many candidate functions.
  - A global switch would silently extend consent to projects added later.
- **Why keep embeddings local?** Embeddings must see every indexed chunk. Remote embeddings would turn "some passages per request" into "the whole repository, once and on every change".
- **Why a failed batch and not an automatic retry elsewhere?** A retry on another model or provider sends the same data to a party nobody chose for it. It also mixes models inside one run without a decision. Splitting and retrying the batch on the chosen model is what extraction already does; it keeps runs explainable and the cost bounded.
- **Why show retention at the moment of choice?** That is when the person decides. Retention buried in a settings page would make the consent uninformed. And Scrinium only states what it checked, because a wrong reassurance is worse than none.
- **Why the contract in code and not in YAML?** The contract is only useful if every adapter is held to it. Types plus conformance tests do that on every `make check`; a YAML file would be a second description that nothing enforces.
- **Why a separate HTTPS client and not a relaxed `LocalHttpClient`?** The loopback-only client is a safety property, with tests that prove it. Relaxing it would make every local call one bug away from leaving the machine.
- **Why the official SDK for Claude, and our own client for the others?**
  - For Python, the official SDK is the supported way to call Claude. It tracks API changes (sampling parameters are no longer accepted on current models, for example), retries what is retryable, and has typed errors.
  - The OpenAI-compatible APIs are a convention more than a contract, and Scrinium already builds that request for llama-server.
- **Why are results not reproducible by sampling?**
  - Current Claude models reject `temperature` and take no seed, and other providers do not guarantee determinism.
  - Remotely, reproducibility comes from T5 (the same input reuses the stored output) and from provenance (N6).

## Alternatives considered

| Option | Why not |
|---|---|
| **One global "remote" switch** | Consent would cover projects nobody looked at, including ones added later. The owner chose per project. |
| **Remote embeddings too** | It means sending the whole indexed repository, and again on every change. |
| **Server-side fallback to another Claude model on refusal** | Recommended by Anthropic for availability, but it retries on a model nobody picked. The owner chose per-batch failure. |
| **Fall back to another provider, or from local to remote, on failure** | Data would leave, or go to a new party, without a decision. A failure must never be the reason something leaves the machine. |
| **The contract as a YAML or JSON Schema file** | A second description beside the code, enforced by nothing. The types and conformance tests are the contract. |
| **Our own public/private-key encryption for API keys** | The private key would sit next to the data it protects, readable by the same user. The OS keyring solves this with the login as the root secret. |
| **Masking identifiers (function and table names)** | Rules are about those names; masked, the model's statements lose meaning, and citations come from the code anyway. Personal data and literals are masked instead, optionally. |
| **A gateway (LiteLLM, OpenRouter)** | OpenRouter adds a third party that sees the code. LiteLLM is a large dependency with its own network behaviour to review. |
| **The `openai` SDK for DeepSeek and GLM** | Another dependency for a request Scrinium already builds. The quirks stay per vendor anyway. |
| **Raw HTTP for Claude** | Scrinium would have to follow API changes and error semantics by hand. |
| **Reading keys from environment variables or SDK profiles** | A key the person never gave to Scrinium could be used without them knowing. |
| **Larger request limits for remote models from the start** | Batching would change and benchmarks would stop being comparable. To be decided later with measurements. |

## What this does not cover

These are out of this decision. Each needs its own when it comes up:

- **Remote embeddings,** and the Hybrid and Cloud modes of the old roadmap.
- **A self-hosted model on another machine** (vLLM, Ollama on a LAN server):
  - plain `http` to a non-loopback host is refused;
  - `https` with a private certificate authority is not supported yet.
- **Claude or other models through cloud platforms:** Amazon Bedrock, Google Vertex AI, Microsoft Foundry, Azure OpenAI. They authenticate with cloud credentials, not an API key, and have their own data terms.
- **Choosing a processing region** where a provider offers it (for example Anthropic's `inference_geo`). The retention notice can mention it, but there is no setting yet.
- **Parallel requests and rate-limit pacing** beyond retrying a failed batch.
- **Asynchronous batch APIs** for cheaper background extraction.
- **Several keys per provider,** or a different key per project.
- **Shared consent** for several people in one workspace (ADR-001 is still one person).
- **Automatic price tables.** Prices are typed by the person or left out.
- **Deleting data at the provider.** Scrinium cannot; the provider's own tools and terms apply.
- **Benchmarks against remote models** (`make bench`). They cost money and need their own consent and budget.
- **Ticket contents** from future connectors (T-012, T-013). When they exist, what they add to requests needs its own line on the consent screen.
- **The keyring on Windows and macOS** is part of ADR-006 but tested only on Linux.
- **A model the provider retires.** Its batches fail with the provider's error until the person picks another model. There is no automatic replacement.

## Consequences

- **"Local-first" now means local by default, not local always.** ADR-001's "nothing leaves the machine by default" still holds. Its "models run locally" holds for `local_only` projects.
- **For a remote project, privacy depends on the provider's terms.** Scrinium shows checked notices and links the terms, but cannot verify what a provider does.
- **A retention catalog to maintain.** Each entry has a date and a source, and goes stale after six months.
- **New dependency:** the `anthropic` Python SDK (built on `httpx2`). It is added and pinned following `docs/security/SUPPLY_CHAIN.md`.
- **Tests that assert locality stay,** now scoped to `local_only` projects: the adversarial no-network test (`tests/security/test_adversarial.py`) and the loopback tests. New tests prove these things:
  - a `local_only` project never builds a remote client;
  - consent, with its retention notice, is required and recorded;
  - redaction, "never send" paths and masking apply to everything sent;
  - adapters pass the conformance fixtures;
  - a key is sent only to its registered host and never appears in logs, errors or exports;
  - the privacy surfaces report the project's real mode.
- **Rules from different models will coexist** in one rulebook. Provenance (N6) and the warning on model change (M6) make that visible.
- **Remote calls cost money.** The budget (T6), the estimate before a run and the request cache (T5) are part of this decision, not extras.

## Revisit when

- a provider is needed that speaks neither the OpenAI-compatible API nor Anthropic's;
- remote request limits, parallelism or batch APIs are measured to pay off;
- someone asks for remote embeddings with a reason that outweighs sending the whole repository; or
- several people share a workspace, and consent has to be per person as well as per project.
