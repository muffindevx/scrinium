# ADR-013: Your own model provider is the recommended start; local is one endpoint among others

**Status:** accepted by the owner, 2026-11-17 (proposed 2026-11-15). Amends [ADR-001](ADR-001-local-first.md), [ADR-004](ADR-004-ai-data-egress.md) and [ADR-010](ADR-010-remote-model-providers.md).

## The question

Scrinium was built to run its models on the user's machine ([ADR-004](ADR-004-ai-data-egress.md)), and [ADR-010](ADR-010-remote-model-providers.md) added remote providers as an opt-in per project, with local as the default. The pilot is the next step, and it raises a question the earlier ADRs did not have to answer: **what does a new person's first hour look like?**

With local as the default, it looks like this on the reference machine (a GTX 1060 and a 4B model):

- the install downloads about 3.2 GB of models and a llama.cpp build before anything works;
- the first rule map takes ~21 minutes and the statements ~18 more;
- the 4B model misses rules it sees, and the measured quality is the ceiling of that model, not of Scrinium;
- on a machine without a suitable GPU, extraction takes hours.

A pilot user judges Scrinium in that hour. So: what should the first run offer, what should the package contain, and how is privacy kept honest if the default changes?

## What we decided

1. **The first run asks for a choice, with your own provider recommended:**
   - **Your own provider** (DeepSeek, GLM, Claude or any OpenAI-compatible API), with the key in the keyring and the consent of [ADR-010](ADR-010-remote-model-providers.md);
   - **A local server** that speaks the OpenAI API (llama-server, Ollama, vLLM), at a loopback address;
   - **Isolated mode**, a profile that blocks every egress and can be audited.

   Nothing is sent until the person makes that choice. Consent stays per project and per task.
2. **The local model is an endpoint, not a component.** Scrinium no longer needs to ship and manage a model to work. llama-server and the models stay supported, behind the same OpenAI-compatible adapter.
3. **The default install contains no models and no llama-server.** `scrinium tools install local` adds them, pinned by version and SHA-256 as today.
4. **Embeddings stay local** ([ADR-010](ADR-010-remote-model-providers.md), decision 4). The small embedding model is part of the default install; it is what makes search by meaning work without sending the repository.
5. **Isolated mode is a first-class profile:** no provider can be registered or used, the privacy pill says so, and `security-status` reports it, so an organisation can check it.
6. **The cost estimate before a run** (already built) is shown on the first extraction with a provider.

## How it works

| Piece | Today | After |
|---|---|---|
| First run | Opens with the local engine | Asks: own provider (recommended), local server, or isolated |
| Default install | Runtime + llama.cpp + 2 models (~3.2 GB) | Runtime + embedding model |
| Local chat | Managed llama-server | Any OpenAI-compatible loopback endpoint; managed llama-server optional |
| Project default | `local_only` | `local_only` until the person consents; the first-run choice proposes the consent for the first project |
| Privacy pill | Local, or names the provider | Same, plus "Modo aislado" |

What does **not** change: the per-project consent and its retention notice, keys only in the keyring, never-send paths, masking, the budget, the record of what left, and the rule that nothing in `scrinium check` calls a model.

## Why

**Why recommend a provider at all, given the product was local-first?** Local-first was about where the data and the runtime live, and it still holds: the database, the index, the embeddings and the rules stay on the machine. What changes is which model writes the proposals. The measured ceiling on the reference machine is the 4B model's, and a pilot that judges Scrinium by it judges the wrong thing.

**Why still ask instead of defaulting silently to a provider?** Sending code to a third party is the person's decision ([ADR-010](ADR-010-remote-model-providers.md)). The default only changes which option is recommended and pre-selected; the consent step stays.

**Why drop the models from the default install?** They are most of the download, they need a GPU to be useful, and people who choose a provider never use them. Making them an add-on keeps the local path intact for those who want it.

**Why an isolated mode instead of just "local"?** Regulated users need to prove nothing can leave, not only that nothing did. A profile that refuses registration and is visible in `security-status` is checkable; "we chose local" is not.

**Why treat the local model as an OpenAI-compatible endpoint?** Many teams already run Ollama or vLLM. One adapter covers them and the managed llama-server alike, and removes special cases from the code.

## Alternatives considered

| Alternative | Why not |
|---|---|
| **Keep local as the default (today)** | The first hour is a 3 GB download and a 40-minute wait on mid-range hardware, with the small model's quality. The pilot would measure the wrong thing. |
| **Default silently to a hosted provider** | Sends code without an explicit decision; breaks [ADR-010](ADR-010-remote-model-providers.md). |
| **Ship a bigger local model** | Does not fit the reference GPU and makes the download larger. |
| **A Scrinium-hosted model** | Needs cloud infrastructure, accounts and billing that the product does not have ([ADR-001](ADR-001-local-first.md)). |
| **Two separate packages (local and remote)** | Doubles release work; `tools install local` reaches the same result with one package. |

## What this does not cover

- Which providers are offered and how their retention is shown: [ADR-010](ADR-010-remote-model-providers.md).
- Model choice research (stage 4 of the old plan): dropped from the plan.

## Consequences

- [ADR-001](ADR-001-local-first.md) and [ADR-004](ADR-004-ai-data-egress.md) get an amendment note: local stays the home of data and runtime; the writing model is chosen at first run.
- The installer and `make release` stop bundling models; `scrinium doctor` checks the chosen endpoint instead of llama-server.
- README hardware tiers apply only to the local path.
- Tests: first run in each of the three modes; isolated mode refusing a provider; the package without models still searching by meaning.

## As built (phase 5, 2026-11-17)

**First run.** `GET/PUT /api/v1/ai-setup` keeps the choice in `Workspace.settings["ai_setup"]` (no schema change; it travels with the backup). Home asks until someone chooses; Settings › AI models shows it and changes it. Every choice is recorded in the audit log (`ai_setup.choose`).

**Isolated mode** (`application/ai/setup.py`):

- Registering, editing, testing or consenting to a provider answers `403 isolated_mode`, and choosing a remote model for a task is refused (`refuse_if_isolated`).
- The router never routes a task outside: the local model, or `ModelNotPermittedError` when a run named a remote one.
- Choosing it revokes every consent in force, each recorded with the reason `isolated_mode`.
- `security-status` reports `profile: "isolated"`, `ai_mode: "isolated"`; the privacy pill says «Modo aislado».
- `SCRINIUM_PROFILE=isolated` forces it from the environment: the console cannot change it (`isolated_managed`), and `security-status` says `profile_managed: true`. `scrinium doctor` reports it.

**Local server.** Any OpenAI-compatible server at a loopback address (`check_loopback_url`: `http`, `127.0.0.1`/`localhost`, no path). It replaces the local chat model at once, without a restart (`SwitchableChat`); requests to it omit llama-server's `cache_prompt` and `id_slot`. `POST /api/v1/ai-setup/check-local` tests it; nothing leaves the machine.

**Default install** (`scrinium tools install`): gitleaks, llama-server and the embedding model, about 0.7 GB instead of 3.2 GB. One deviation from decision 3: **llama-server stays in the default install**, because it is what serves the embedding model (decision 4). Shipping embeddings in-process would add a new runtime dependency; llama-server is already pinned and verified. `scrinium tools install local` adds the chat model. Startup only warns if the embedding model is missing; `doctor` reports the chat model as optional and asks for 4 GB of memory without it (8 GB with it).

**Not measured:** «a stranger reaches their first rules in under 10 minutes with a provider» needs a person; it moves to the pilot (5.4).

## Revisit when

- The pilot shows people choosing local most of the time.
- A local model that fits mid-range hardware matches the providers on the reference set.
