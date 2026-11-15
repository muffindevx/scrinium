# ADR-011: A rule is identified by its anchors, not by its title

**Status:** proposed, 2026-11-15. Needs the owner's acceptance before phase 1 (stable rule identity) starts.

## The question

A verified rule is the most valuable thing Scrinium holds: a person read the code, agreed that it implements a business rule, and said so. Everything else (drift warnings, `scrinium check`, rules as code, the review inbox) protects or uses that judgment. So the first question is the simplest one: **when Scrinium extracts again, how does it know that a rule it finds is the same rule a person already verified?**

Today the answer is the title. `rule_key` ([`extraction.py:326`](../../runtime/src/scrinium/application/analysis/extraction.py#L326)) turns the title into a slug, and extraction looks the rule up by that `canonical_key` ([`extraction.py:689`](../../runtime/src/scrinium/application/analysis/extraction.py#L689)). That has two failures, and both are silent:

- **Another title, another rule.** The model writes "Sesión caducada a los 7 días" one run and "Duración máxima de la sesión" the next. The second becomes a new, unverified rule, and the verification stays on an orphan.
- **Same title, same rule.** Two different decisions in two files that the model happens to title alike are merged into one, with the citations of both.

Every change of model, prompt or provider makes the first failure more likely, and the product now expects people to change models ([ADR-010](ADR-010-remote-model-providers.md)). So: what should identify a rule?

## What we decided

1. **A rule's identity is its id, and it is matched by what it cites, not by what it is called.** Rules keep the UUID they already have (`KnowledgeEntity.id`); new ids are UUIDv7 so they sort by creation time. The title becomes an ordinary, editable field.
2. **What a rule cites is a decision node with two fingerprints.** The first level of an intermediate representation (guards, thresholds, state changes, formulas, tables, schema constraints, validations) is extracted with tree-sitter queries for TypeScript/JavaScript and Python, and `sqlglot` for SQL; `candidates.py` remains the fallback for other languages. Each node gets:
   - a **shape** fingerprint, with literals replaced by their type: "the same decision";
   - a **value** fingerprint, with literals and resolved constants: "the same decision with the same numbers".
3. **Each citation carries an anchor:** source, path, qualified symbol, node and both fingerprints.
4. **Anchors are re-matched on every sync and every re-extraction**, from cheapest to most expensive: exact (path, symbol, shape) → same shape elsewhere in the file → same rare shape in the project (a move) → fuzzy (MinHash with LSH) → a 1:1 assignment of what is left.
5. **A file is identified by (source, path)** in every layer: search, graph, relationships, architecture and citations.
6. **Existing rules migrate** from slug keys without losing a verification. Rules that were merged because they shared a title are split when their anchors differ.

## How it works

### Normalising before fingerprinting

The node's syntax tree is normalised so cosmetic edits do not change it:

1. comments and whitespace are dropped;
2. local variables are renamed in order of appearance (alpha renaming);
3. operands of commutative operators are sorted;
4. `a < b` and `b > a` become one form.

Both fingerprints are BLAKE2b (128 bits) of the canonical serialisation. Constants are resolved through the file's symbol table and the import resolution `edges.py` already does, following aliases and `export const` one level deep. Results are cached per (blob, parser version), so an unchanged blob is never parsed again, even after a move.

### Re-anchoring

For each changed file, with old nodes O and new nodes N:

| Step | Match on | Cost |
|---|---|---|
| Exact | (path, qualified symbol, shape) | Hash lookup |
| Same file | shape among the file's nodes | Hash lookup |
| Moved | shape across the project, only if that shape is rare | Hash lookup |
| Fuzzy | MinHash over 3-token shingles, LSH with 16 bands of 8 rows (Jaccard ≥ 0.8) | Sublinear |
| Assignment | Greedy by score, or Hungarian when \|O\|·\|N\| < 10⁴ | Small sets only |

Each old node ends as: the same rule (with drift if its value changed), moved, or retired.

### What a verification survives

| Change | Identity | Verification |
|---|---|---|
| Rename a variable, reformat, reorder operands | Exact match | Kept |
| Move the function to another file | Moved | Kept |
| Change the model, prompt or provider | Exact match | Kept |
| Change the threshold from 40 to 50 | Same shape, other value | Kept, flagged as drift (phase 2) |
| Delete the decision | No match | Rule retired |

## Why

**Why not keep the title and make it more stable?** Because the title is written by a model, and the plan now expects different models. Any normalisation of free text (lowercasing, stemming, embeddings of the title) still moves when the wording moves, and still merges different decisions that read alike. The code is the one thing both runs see identically.

**Why fingerprints of a syntax node rather than line ranges?** Line ranges move with every edit above them; a rule citing lines 40–44 points at something else after a ten-line import block. A node is found wherever it lands.

**Why two fingerprints?** One question is "is this still the same decision?" and another is "did its numbers change?". With one hash, changing a threshold from 40 to 50 would look like a deleted rule and a new one, and the verification would be lost exactly when it matters most.

**Why tree-sitter queries and not the LLM?** Identity must be deterministic and free: it runs on every sync and, in phase 2, in CI. Nothing on that path may call a model.

**Why does the acceptance bar say 95 % of reference rules must have a node?** Below that, too many rules fall back to weaker anchors and the guarantee becomes a hope. It is measured on the extended reference set (phase 0.5).

## Alternatives considered

| Alternative | Why not |
|---|---|
| **Title slug (today)** | Loses verifications on any rewording and merges distinct decisions. |
| **Embedding similarity of title and statement** | Not deterministic across model versions, costs a model call, and two thresholds on similar text still look alike. |
| **Line ranges plus `git log -L`** | Follows edits but not reformatting or moves; slow on long histories. |
| **Hash of the whole function** | Any unrelated edit in a long function (the game's `start` has 6 600 lines) would detach every rule in it. |
| **SCIP or a language server for precise symbols** | Better resolution, but heavy per-language setup. Kept as an optional later step for TS and Python (call graph, not identity). |
| **GumTree-style full AST diff** | Precise but quadratic on large files; the staged matching above reaches the same result in the common case and uses assignment only on small leftovers. |

## What this does not cover

- What happens when a decision drifts (warning levels, CI exit codes): phase 2 and its own design.
- How rules are written to the repository: [ADR-012](ADR-012-rules-as-code.md).
- Languages without a tree-sitter grammar in Scrinium: they keep `candidates.py` anchors, which are weaker; adding grammars waits until the pilot asks for them.

## Consequences

- A migration that rewrites how existing rules are keyed, with `foreign_key_check` and a rule-by-rule comparison on the reference copy.
- Sync gets more work per changed file (parse, fingerprint, re-anchor). The content-addressed cache keeps unchanged blobs free.
- The title stops being part of identity, so editing it is safe and will be offered in the UI.
- Tests: synthetic refactors (rename, move, reformat, reorder) must keep 100 % of verifications; a change of model must keep them too.

## Revisit when

- The pilot shows rules whose anchors cannot be re-found more than rarely.
- A language the pilot needs has no grammar and `candidates.py` anchors prove too weak for it.
