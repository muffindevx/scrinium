# Product: MVP scope

**Confirmed by the owner on 6 November 2026.** The Scrinium MVP is **local-first, for one person, with one workspace and no Scrinium cloud infrastructure**.

**Promise.** Explain:

- what changed;
- why we believe it changed;
- what evidence supports it;
- what may be affected;
- what we still do not know.

**Main use case.** Connect a local repository and its documentation. Then look up the business rules the code implements, what a change touches, and what changed between releases.

**Tickets come last.** A ticket source (Linear first, then Jira) is the final stage: it touches third-party systems, credentials and people's data, so it is built once the core works. Until then, ticket keys are read only from the local Git history.

**Out of the MVP:**

- Scrinium accounts, teams and multi-tenancy;
- WorkOS, SAML and SCIM;
- sync between devices;
- PostgreSQL/pgvector, Redis, S3 and Kubernetes.
