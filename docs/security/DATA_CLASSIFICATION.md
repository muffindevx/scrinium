# Data classification

| Class | What it includes | Rule |
|---|---|---|
| `public` | Publishable content | No special restrictions |
| `internal` | Information for internal use | Does not go to third parties without explicit configuration |
| `confidential` | Private code, tickets, architecture, rules, chunks, embeddings, prompts, answers | Indexed locally; leaves only with opt-in and context selection |
| `restricted` | Keys, tokens, passwords, private certificates, secrets | Blocked or excluded by default; never reaches the index or the LLM |

Embeddings are `confidential`. They are not an encrypted copy, and can reveal information.

## How it is applied

- Source content is treated as `confidential` and stored only locally.
- Anything that looks `restricted` is excluded before it is stored, in two ways:
  - **by name:** `.env`, keys, certificates;
  - **by content:** known token formats, private keys, assigned passwords, URLs with credentials.

## Data Scrinium excludes by default

- `.env` files;
- private keys and private certificates;
- credential directories;
- dumps;
- large binaries;
- dependencies and build artifacts.
