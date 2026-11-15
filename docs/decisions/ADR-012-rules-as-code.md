# ADR-012: Verified rules live in the repository, one file per rule

**Status:** proposed, 2026-11-15. Needs the owner's acceptance before phase 3 (rules as code and mirrors) starts. Depends on [ADR-011](ADR-011-rule-identity.md).

## The question

Today every verification lives in one SQLite file on one person's machine. That raises questions the product cannot answer yet:

- **Who else can see the verified rules?** Nobody. A teammate who clones the repository gets none of them.
- **What survives a lost laptop or a wiped data directory?** Nothing, unless a backup exists.
- **How does CI know which rules exist?** It cannot: CI has no running Scrinium and no database ([`scrinium check`](#what-this-enables) in phase 2 must run without a server).
- **How does a rule change get reviewed?** Outside the team's normal review: no diff, no pull request.

So: where should the source of truth for verified rules live, and in what shape?

## What we decided

1. **Verified rules are files in the repository** they describe, under `.scrinium/rules/<uuid>.md`, **one file per rule**. They are the source of truth for what was verified. SQLite becomes a cache and an index that can be rebuilt from them.
2. **Only verified rules are written.** Unverified proposals are never exported (owner's decision, 2026-11-15): the repository holds human judgments, not model output.
3. **Rejected rules leave a tombstone**: only the fingerprint and the reason, so the same decision is not proposed again while its fingerprint is unchanged.
4. **`scrinium rules export` and `scrinium rules import`** move between the two. Importing rebuilds the verified state from a clean clone. The console warns about verifications not yet exported.
5. **The files never contain source code text**: only citations (path and lines), anchors and fingerprints.
6. **Products with several repositories** declare them in `scrinium.product.yaml`, kept in the backend repository (owner's decision). A rule in the UI repository can be a **mirror** of the backend's rule, referenced by id, and the two are compared by their resolved values ("the UI allows 30 %, the API 25 %"). In a monorepo, subfolders can be declared as separate sources, each with its own `.scrinium/rules/`.

## How it works

### The file

```markdown
---
id: 0193a1c2-7f00-7c3e-9a1b-2f6d4e8c1a55
status: verified
verified_by: Ana Pérez
verified_at: 2026-11-20T10:14:00Z
anchors:
  - source: backend
    path: src/billing/discount.ts
    symbol: discountFor
    lines: [40, 44]
    shape: 9f2c…e1
    value: 4b7a…07
provenance:
  extractor: rules-map-2
  model: deepseek-chat
---
When a customer is VIP and the subtotal is 100 or more, the discount is 10 %.

WHEN: VIP customer, subtotal ≥ 100
THEN: 10 % discount
```

- **Stable key order and normalised line endings**, so a re-export changes nothing byte for byte and diffs show only real changes.
- **One file per rule**, so two people verifying different rules never touch the same file and merges stay local.
- The statement and rationale are the person's text; the anchors are what [ADR-011](ADR-011-rule-identity.md) computes.

### Tombstones

```markdown
---
id: 0193a1c2-…
status: rejected
reason: not_a_rule
shape: 1a0e…9c
---
```

Re-extraction skips any candidate whose shape matches a tombstone. If the code changes (a new shape), it can be proposed again.

### Round trip

`export → import → export` must produce identical files. A clean clone plus `import` must reproduce the verified rulebook.

## What this enables

- **`scrinium check` in CI without a server**: it reads `.scrinium/rules/`, parses only the changed files and compares fingerprints (phase 2).
- **Rule changes go through code review**: verifying, editing or retiring a rule is a diff in a pull request.
- **Shared knowledge**: a teammate's clone has the rulebook.
- **Agents can read the rules** through MCP or directly from the files (phase 6).

## Why

**Why the repository and not a shared server?** The product is local-first for one person with no Scrinium cloud ([ADR-001](ADR-001-local-first.md)). The repository is already the place the team shares, reviews and backs up; using it adds no infrastructure and no accounts.

**Why one file per rule and not one per module (the earlier plan, N13)?** A file per module turns two unrelated verifications into a merge conflict. Per rule, conflicts only happen when two people change the same rule, which is a real conflict worth seeing.

**Why never export unverified proposals?** The repository would fill with model output that nobody has judged, and reviewers would learn to ignore the folder. What is committed must mean "a person agreed".

**Why no code text in the files?** The repository already has the code; copying it would go stale and duplicate content that may be sensitive. Fingerprints and citations are enough to re-find it.

**Why Markdown with front-matter?** Humans review it in a pull request; tools parse the front-matter. JSON would be harder to read in a diff, YAML alone would lose the prose.

## Alternatives considered

| Alternative | Why not |
|---|---|
| **SQLite only (today)** | Not shared, not reviewable, not usable from CI, lost with the machine. |
| **One file per module, regenerated (N13)** | Merge conflicts between unrelated rules; regeneration rewrites lines nobody changed. |
| **A single `rules.json`** | Every verification touches the same file. |
| **Export proposals too, with a status** | The folder stops meaning "verified"; reviews get noisy. |
| **A Scrinium server or sync service** | Contradicts local-first and adds accounts and operations the product does not have. |
| **Git notes** | Not visible in normal review and easy to lose on push. |

## What this does not cover

- How identity and fingerprints are computed: [ADR-011](ADR-011-rule-identity.md).
- Warning levels and exit codes of `scrinium check`: phase 2.
- Ticket links in the files: [ADR-014](ADR-014-ticket-connectors.md) decides what is stored.

## Consequences

- SQLite stops being the only truth: a schema change can drop and rebuild the index from the files.
- The console shows unexported verifications, and export becomes part of the normal flow.
- Teams that do not want `.scrinium/` in their repository cannot use CI checks or sharing; local use still works.
- Tests: byte-for-byte round trip, clean-clone import, tombstones blocking re-proposals.

## Revisit when

- The pilot shows teams that cannot commit `.scrinium/` (policy or monorepo rules), and need another place for the files.
- Mirrors between repositories turn out to need more than value comparison.
