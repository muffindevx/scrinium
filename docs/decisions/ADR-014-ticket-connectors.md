# ADR-014: Ticket connectors, read-only, Linear first

**Status:** accepted, 2026-11-18, with the amendments in [Amendments at acceptance](#amendments-at-acceptance-2026-11-18). Proposed 2026-11-15. The owner chose Linear as the first connector, with Jira following the same design.

## The question

Scrinium's promise ends with "why": why a rule exists and why it changed. Today the only answer comes from Git: [`knowledge/tickets.py`](../../runtime/src/scrinium/application/knowledge/tickets.py) runs `git blame` on a rule's cited lines and turns the ticket keys found in those commits' messages (`PAY-142`, `#37`) into labels. That gives a key, not a ticket: no title, no type, no status, no way to tell a bug from a feature, and nothing when the commit message names no key.

Connecting to the ticket system would close that gap, but it is the riskiest integration in the plan:

- it reaches a third-party system with the person's credentials;
- tickets contain people's names and free text that may be sensitive;
- the provider's answers are untrusted input;
- a write to the provider is visible to a whole team.

So: what should a connector do, how does it authenticate and stay in sync, what does it store, and what must it never do?

## What we decided

1. **Connectors are read-only by default.** Scrinium is not a ticket client. The one optional write (a comment on a ticket listing the rules a change affects) is off by default and asks for confirmation each time.
2. **Linear first, then Jira** (Cloud and Data Center), with the same design.
3. **Authentication as a native app** (RFC 8252): OAuth 2.0 with PKCE (S256) and a redirect to `http://127.0.0.1:<ephemeral port>/callback`, open only during the flow, with a random `state`. Jira Data Center uses a personal access token. Tokens go to the OS keyring, tied to the host, like API keys ([ADR-010](ADR-010-remote-model-providers.md)); refresh tokens are rotated and revoked on disconnect.
4. **A ticket's identity is the provider's immutable id.** The key (`SHOP-212`) is a label, because Jira changes it when a ticket moves between projects; the history of keys is kept so old commit messages still resolve.
5. **Incremental sync by polling**, with a cursor per connection (`updated` plus the last id), small pages, idempotent upserts, tombstones for deleted or inaccessible tickets, and a weekly reconciliation sweep. It respects `Retry-After` and Linear's query cost, and runs at low priority in the durable job queue.
6. **Metadata only by default:** key, title, type, status, labels, dates, links and commit ids. Descriptions and comments are a separate opt-in, stored locally, and never sent to a model without the project's egress consent, with masking. Assignees and authors are not stored by default.
7. **An evidence graph rule ↔ commit ↔ ticket with four confidence levels:**

   | Level | Comes from | Shown as |
   |---|---|---|
   | Explicit | The provider links the commit or PR | Fact |
   | Mentioned | The key appears in a commit message, branch or PR title | Fact, with its origin |
   | Inferred | Same period, same files, local similarity between ticket and rule | Proposal, in the review inbox |
   | Confirmed | A person linked or accepted it | Verified |

## How it works

### Data model

- `ticket_connections`: provider, host, scope (which Linear teams or Jira projects belong to which product), cursor, status.
- `tickets`: provider id (identity), current key, previous keys, title, type, status, labels, dates, tombstone flag.
- `ticket_links`: from (rule, commit or ticket), to (ticket), type, confidence level, origin.

### What people get

- **Rule detail:** the tickets that introduced or changed it, with type, status and link: the "why".
- **`scrinium check` and the Changes screen:** "rule X changes; ticket SHOP-221". And the signal **"rule changed without a ticket"** for teams that require one.
- **Triage from a bug ticket:** stack trace (if descriptions were allowed) → functions → rules → recent changes → similar resolved bugs, searched locally by title.
- **Bug density per rule and per area**, feeding the review inbox and the coverage view.
- **Release notes:** rules changed between two tags, with their tickets.

### Quality bar

- Conformance tests with recorded responses per provider: pagination, 429 with `Retry-After`, expired and revoked tokens, moved tickets, renamed keys.
- Fuzzing of the response parser: missing fields, unexpected types, Unicode.
- The `httpx` logger at WARNING; no URL with a token in any log; `state` and PKCE checked; the redirect port closed after the flow.
- 10 000 tickets in under 5 minutes the first time, under 10 seconds incrementally.

## Why

**Why read-only?** The value is in reading: linking rules to the reasons behind them. Writing to a team's tracker without a click would be surprising, and a bug in Scrinium would become noise in other people's work.

**Why OAuth with PKCE on loopback rather than pasting a token?** It gives the narrowest read scopes, works without a client secret in a desktop app, and lets the provider revoke access. Personal tokens remain only where OAuth is not available (Jira Data Center).

**Why the provider's id and not the key?** Keys change when tickets move; ids do not. Matching by key would silently break links.

**Why polling and not webhooks?** A local application cannot receive incoming calls. Polling with a cursor and ETags is cheap for read-only sync.

**Why metadata only by default?** Descriptions and comments carry the most sensitive text and are rarely needed for the "why". Making them opt-in keeps the default small and the egress record empty of ticket text.

**Why confidence levels?** An inferred link presented as fact would undermine trust in every link. Keeping the level visible lets explicit and mentioned links be shown plainly and inferred ones be reviewed.

**Why Linear first?** The owner's choice: its GraphQL API and OAuth app model are simpler to do well, and the design carries over to Jira.

## Alternatives considered

| Alternative | Why not |
|---|---|
| **Only ticket keys from commits (today)** | No title, type or status; nothing when messages name no key. Kept as the "mentioned" level. |
| **Import an exported CSV or JSON** | No credentials, but no updates and a manual step every time. Useful for a one-off pilot, not as the design. |
| **Go through the provider's own MCP server** | Ties Scrinium to an agent runtime and gives less control over scopes, rate limits and what is stored. |
| **Read and write by default** | Surprises teams and multiplies the blast radius of a bug. |
| **Webhooks through a relay** | Needs hosted infrastructure the product does not have. |

## What this does not cover

- GitHub issues and pull requests (T-012): only if the pilot needs them, with this same design.
- How tickets weigh in the review inbox order: the inbox design.

## Amendments at acceptance (2026-11-18)

Two facts about Linear, checked against its OAuth documentation when phase 10 started, change how decision 3 applies to it. A third clarifies decision 4.

### 1. Linear starts with a personal API key; OAuth follows once Scrinium has a registered app

**What.** The first Linear connection authenticates with a personal API key. The person pastes it once; it goes straight to the OS keyring and is sent only to `api.linear.app`, the same way a model provider's key is handled ([ADR-010](ADR-010-remote-model-providers.md)). OAuth with PKCE remains the target, and arrives as soon as a Scrinium OAuth app is registered with Linear.

**Why not OAuth from day one?** Two things stand in the way:

- **No client id.** OAuth needs an application registered with Linear, whose `client_id` Scrinium ships. That application does not exist yet. Registering it is the publisher's act, not code.
- **No ephemeral port.** Linear matches `redirect_uri` exactly against the URLs registered for the app. A "random free port" callback, as RFC 8252 suggests, would be rejected. So the loopback callback must use one fixed port registered with the app (for example `http://127.0.0.1:<fixed port>/callback`). If that port is busy, the flow fails with a clear message instead of picking another.

**Is a personal key acceptable?** The panel's annex C.2 already lists it for Linear. The risks are the same as for a model provider's key, and the same controls apply:

- the key lives in the keyring only, and is never kept in the database, logs, exports or `.scrinium/`;
- it is tied to one host;
- it is deleted from the keyring when the connection is removed.

It does lose two properties OAuth has:

- **Scopes.** The person should create the key with read access only, where Linear offers that. Scrinium only ever sends read queries anyway.
- **Revocation from Scrinium.** Removing the connection deletes Scrinium's copy, but the key stays valid in Linear until the person revokes it there. The interface says so when the connection is removed.

**Alternatives.**

| Alternative | Why not |
|---|---|
| Wait for the OAuth app before shipping anything | Blocks the whole phase on a registration step that is not code. The data model, sync and evidence graph do not depend on how the token was obtained. |
| Each person registers their own OAuth app | Asks too much of a person who only wants their tickets. It is still possible later as an advanced option, because the client id is configuration. |
| OAuth with an ephemeral port anyway | Linear rejects it. |

### 2. Rate limits arrive in two shapes

Linear can answer HTTP 429. It can also answer with a GraphQL error whose `extensions.code` is `RATELIMITED`, with the reset time in its rate-limit headers. The client treats both the same way: it waits until the reset (or `Retry-After`) and does not count the wait as a failure. A sync that cannot finish within its job's time goes back to the queue with its cursor saved.

### 3. Key history comes from the provider

Linear returns an issue's `previousIdentifiers`, its keys from before it moved between teams. Scrinium stores them as the key history in decision 4, so an old commit that says `ENG-12` still resolves to the issue that is now `OPS-40`. Jira will need its own source for the same history (its changelog), which is why the history is a column and not something derived.

### 4. Linear has no issue type

Jira has issue types (bug, story, task). Linear does not: teams mark bugs with labels. The ticket's `type` is therefore `bug` when a label's name is "bug" (in any case or language form Scrinium recognises), and empty otherwise. Bug density (C.5) counts only tickets typed `bug`.

## As built (2026-11-18)

What phase 10 ended up doing where the text above left a choice open.

- **OAuth comes back to Scrinium itself.** The redirect URI is `http://127.0.0.1:<Scrinium's port>/oauth/linear/callback`: the fixed port amendment 1 asks for, with no second listener. The registered app lists that URL; the `client_id` comes from `SCRINIUM_LINEAR_CLIENT_ID`, and without it the console offers the personal key only. The `state` is single-use and expires in ten minutes. Access and refresh tokens are one keyring secret; the access token is refreshed two minutes before it expires, the rotated refresh token replaces the old one under the same reference, and removing the connection revokes the token at Linear.
- **Jira authenticates two ways.** Cloud uses an API token with the account e-mail (HTTP Basic, REST v3, `nextPageToken` pagination); Data Center uses a personal access token (Bearer, REST v2, `startAt`). The site address must be HTTPS to a named host, as with model providers. Jira does not report a moved issue's old key, so the sync keeps the key a ticket had before as a previous one; it does not expose development links through its public API, so Jira tickets reach rules through commit messages and people, not through "explicit" links.
- **Polling adapts.** A background thread asks every five minutes which connections are due. A connection with news waits ten minutes; one without doubles its wait up to an hour.
- **Inferred links are word overlap, shown as proposals.** Tickets whose title and labels share at least two words with the rule's title and statement (identifiers the statement quotes do not count), at least 0.2 Jaccard, three at most. A person confirms one (it becomes a confirmed link) or dismisses it (it is never proposed again).
- **Text and comments are per-connection switches.** Reading descriptions and comments re-reads every ticket; turning it off deletes the stored text at once. Comments are stored without authors. A description that holds a stack trace is followed locally in the bug-ticket triage; ticket text never reaches a model.
- **Commenting needs the switch and a confirmed text.** The console drafts a comment listing the ticket's rules, the person edits and confirms it, and only then is it posted. Linear's OAuth scope for that is `comments:create`; the read-only default asks for `read` only. The audit records that a comment was posted and its length, never its text. The privacy summary says which trackers may receive comments.

## Consequences

- New tables and a migration; deleting a connection removes its tickets, links and tokens with the two-phase delete.
- The privacy card and data-flow endpoint list each connected tracker and what is read from it.
- Rules exported to the repository ([ADR-012](ADR-012-rules-as-code.md)) may carry ticket ids, never ticket text.

## Revisit when

- The pilot asks for writing back more than a comment.
- A tracker without OAuth or with a very different model (for example Azure DevOps) is needed.
