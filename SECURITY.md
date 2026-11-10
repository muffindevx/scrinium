# Security

Scrinium is local-first:

- it listens only on `127.0.0.1`;
- it keeps data on your machine;
- by default it sends nothing to external services.

The threat model, and what it does and does not protect, are in `docs/security/` and `docs/data-flow.md`.

## Reporting a vulnerability

If you find a security issue, write to the project maintainer before publishing it. Include:

- the Scrinium version (`scrinium --version` or the status screen);
- how to reproduce it, without anyone's real data;
- what you think it allows (reading data, running something, bypassing the session…).

Do not include tokens, passwords or content from real repositories in the report.

## What counts as a vulnerability

Among other things:

- the API being reachable from another machine, or without a session;
- bypassing CSRF;
- reading files outside the approved sources;
- executing code from an indexed repository, or analyzed SQL;
- data leaving the machine without consent;
- secrets or content ending up in logs;
- a deletion that leaves data the application itself can recover.

These are not Scrinium vulnerabilities:

- malware that already controls your account;
- a machine without disk encryption;
- the known limitations documented in `docs/scrinium-internals.md` and `docs/security/THREAT_MODEL.md`.
