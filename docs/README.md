# Scrinium documentation

Documentation for developers who want to understand how Scrinium works and change it. Setup and
commands are in the root [README](../README.md).

## Reading order

| Step | Document | What you get |
|---|---|---|
| 1 | [Product scope](product.md) | What Scrinium is for, and what is out of scope |
| 2 | [Scrinium internals](scrinium-internals.md) | Architecture, data flow, data model, security, critical code paths, practical guide, known traps and glossary |
| 3 | [Rule extraction](rule-extraction.html) | What a rule is, when and how it is extracted, why it shows few lines, and its limits. Open it in a browser |
| 4 | [Data flow](data-flow.md) | What is stored, what leaves the machine (nothing), export, deletion and trust boundaries |
| 5 | [Decisions](#decisions) | Why the architecture is the way it is |
| 6 | [Security](#security) | Threat model, data classification, secrets and supply chain |

## Diagrams

All diagrams are SVG files in [diagrams/](diagrams/), generated from `diagrams/src/` with
`build.py`. The [diagram table](scrinium-internals.md#diagrams) in the internals says what each one
shows and where it is used.

| Area | Diagrams |
|---|---|
| Runtime | [runtime-architecture](diagrams/runtime-architecture.svg), [data-boundaries](diagrams/data-boundaries.svg) |
| Sources and questions | [sync-flow](diagrams/sync-flow.svg), [question-flow](diagrams/question-flow.svg) |
| Rules | [rule-anatomy](diagrams/rule-anatomy.svg), [extraction-map](diagrams/extraction-map.svg), [extraction-statements](diagrams/extraction-statements.svg), [model-queue](diagrams/model-queue.svg), [understanding-layers](diagrams/understanding-layers.svg) |
| Data | [data-model](diagrams/data-model.svg) |
| Security | [sign-in](diagrams/sign-in.svg) |

## Decisions

Architecture decision records: what was decided, how, why, and the alternatives rejected.

| ADR | Decision |
|---|---|
| [ADR-001](decisions/ADR-001-local-first.md) | Local-first, for one person |
| [ADR-002](decisions/ADR-002-storage.md) | SQLite, FTS5 and embedded vectors (sqlite-vec since phase 9.1) |
| [ADR-003](decisions/ADR-003-local-runtime-security.md) | Security of the local runtime |
| [ADR-004](decisions/ADR-004-ai-data-egress.md) | Local AI by default, and data egress |
| [ADR-005](decisions/ADR-005-stack.md) | Languages and tools |
| [ADR-006](decisions/ADR-006-secrets.md) | The OS keyring for secrets |
| [ADR-007](decisions/ADR-007-repository-structure.md) | Repository structure |
| [ADR-008](decisions/ADR-008-sql-parser.md) | sqlglot to analyze SQL as text |
| [ADR-009](decisions/ADR-009-ui-routing.md) | react-router for the UI screens |
| [ADR-010](decisions/ADR-010-remote-model-providers.md) | Remote model providers, by project and with consent |
| [ADR-011](decisions/ADR-011-rule-identity.md) | A rule is identified by its anchors, not its title |
| [ADR-012](decisions/ADR-012-rules-as-code.md) | Verified rules live in the repository, one file per rule (proposed) |
| [ADR-013](decisions/ADR-013-own-provider-by-default.md) | Your own provider is the recommended start (proposed) |
| [ADR-014](decisions/ADR-014-ticket-connectors.md) | Ticket connectors, read-only, Linear first (proposed) |

## Security

| Document | Contents |
|---|---|
| [THREAT_MODEL.md](security/THREAT_MODEL.md) | Threats, controls, the tests that cover them and residual risk |
| [DATA_CLASSIFICATION.md](security/DATA_CLASSIFICATION.md) | Data classes and how each is handled |
| [SECURITY_GLOSSARY.md](security/SECURITY_GLOSSARY.md) | Each security concept: what it is, the risk it addresses and what it does not protect against |
| [SUPPLY_CHAIN.md](security/SUPPLY_CHAIN.md) | How dependencies are checked, updated and rolled back |
| [SECURITY.md](../SECURITY.md) | Security summary, and how to report a vulnerability |
