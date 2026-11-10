# ADR-008: sqlglot to analyze SQL as text

**Status:** accepted and implemented in T-015, 2026-11-07. Owner's decision.

## The question

Section 6.2 of the plan asks Scrinium to review SQL against the business rules. It must understand the statement's structure: tables, columns, reads and writes, in the declared dialect. It must **never execute** the SQL. Which tool can do that safely?

## What we decided

**`sqlglot`** (30.30.0, pinned in `uv.lock`):

- it is pure Python, with no runtime dependencies;
- it never opens a connection;
- it supports the common dialects.

Scrinium uses it only to parse. SQL is never passed to SQLite or to any other database.

## How it works

- The SQL review (`application/analysis/sql_review.py`) parses the statement into an AST with the chosen dialect. From the AST it extracts the statement type, the tables and columns touched, and whether it reads or writes.
- An optional schema DDL is parsed the same way, to check columns against it.
- The API accepts a closed list of dialects: generic, PostgreSQL, MySQL, SQLite, T-SQL, Oracle, BigQuery, Snowflake and DuckDB.

## Why

- **What does "never execute" rule out?** Any approach that hands the SQL to a database engine, even an empty one. Triggers, functions and engine bugs are third-party code paths. Parsing to an AST is the only way to understand SQL without running it.
- **Why an AST and not tokens?** Knowing whether a statement writes, and to which columns, needs structure: subqueries, CTEs, `INSERT … SELECT`. Token matching gets this wrong.
- **Why sqlglot?** It has broad dialect coverage, is pure Python and has no dependencies. That keeps the supply chain small, with no native code to audit.

## Alternatives considered

| Option | Why not |
|---|---|
| **sqlparse** | It only tokenizes. Detecting writes and columns would be fragile. |
| **Running `EXPLAIN` on an empty database** | It still executes third-party SQL, which is exactly what the plan forbids. |
| **A tree-sitter SQL grammar** | Dialect coverage is uneven, and it gives syntax only, without the semantic helpers (qualified columns, statement types) that sqlglot has. |
| **Asking the LLM to read the SQL** | Not deterministic. It can invent tables or miss writes. |

## Consequences

- Error messages from sqlglot copy parts of the SQL. Scrinium stores only the line and column, never the message.
- A statement that sqlglot does not fully understand can appear as `other`, or with incomplete tables. The result says so; nothing is invented.

## Revisit when

- users need a dialect sqlglot does not support; or
- SQL review needs data-flow analysis across many statements.
