# Threat model

A threat model answers five questions:

- what we protect;
- from whom;
- how;
- how we check it;
- what remains unsolved.

## Assets

- **Content:** code, tickets and documents.
- **Credentials:** connector tokens and the local session itself.
- **Derived data:** extracted rules, chunks, embeddings, prompts and answers.
- **Operational data:** logs and backups.

## Risk matrix

The Status column uses these values:

- **Tested:** has automated tests today.
- **Implemented:** done, without a dedicated automated test.
- **Partial:** mitigated only in part.
- **Accepted:** a known risk the owner chose to keep.

| Threat | Asset | Impact | Mitigation | Test | Status | Residual risk |
|---|---|---|---|---|---|---|
| Another machine on the network calls Scrinium | Everything | High | Only `127.0.0.1`, with no option to change it | `runtime/tests/integration/test_start_command.py` | Tested | A tunnel or proxy you set up yourself would expose it |
| A hostile website uses DNS rebinding | Session, data | High | Exact `Host` validation | `runtime/tests/security/test_host_origin.py` | Tested | None known at this layer |
| A hostile website calls the API or triggers actions (CSRF) | Session, data | High | `Origin`, CSRF token, `SameSite=Strict`, no CORS | `runtime/tests/security/test_host_origin.py`, `test_session_csrf.py` | Tested | A buggy browser could fail |
| Another user on the machine calls the port | Data | High | The whole API requires a session | `runtime/tests/security/test_session_csrf.py` | Tested | See the launch token and malware rows below |
| Another account reads the launch token in the process list | Session | Medium | Single use, expires in 2 minutes. Someone else using it is noticed, because your link stops working | `test_launch_token_is_single_use`, `test_launch_token_expires` | Accepted | A short window in which another account could get there first. Owner's decision: [ADR-003](../decisions/ADR-003-local-runtime-security.md) |
| Another program takes the port and receives the token | Session | High | The port is reserved before the token is issued | `test_port_in_use_fails_without_issuing_a_token` | Tested | None known at this layer |
| Another service on `127.0.0.1` reads the cookie | Session | Medium | `HttpOnly`; the CSRF token and `Origin` still block actions from another origin | Manual test with a page on another port | Partial | Cookies ignore the port: that service could receive the cookie and use it outside the browser |
| Secrets in logs or errors | Tokens, content | High | No query strings, redaction, generic errors; 422 errors do not echo the input | `runtime/tests/security/test_logging.py`, `test_validation_errors_do_not_echo_the_input` | Tested | Redaction recognizes known formats, not all of them |
| A new route is left unprotected | Data | High | Protected by default in the middleware | `test_every_api_route_requires_a_session`, `test_every_mutating_route_requires_csrf` | Tested | Depends on not widening the exceptions |
| Source revisions or the audit log are altered | Evidence, audit | Medium | Triggers that reject `UPDATE` | `test_revisions_are_immutable`, `test_audit_events_are_append_only` | Tested | Whoever can write the file can drop the triggers. This guards against bugs in Scrinium's own code, not against an attacker with your account |
| Another account reads the database | Data | High | `0600` file in a `0700` folder. Scrinium fixes broad permissions, and refuses to start on a disk that ignores them or in a folder owned by another account | `test_database_is_private_to_this_account`, `test_data_dir.py`, `test_data_dir_on_a_disk_without_permissions_is_refused` | Tested | Root can read everything. Not checked on Windows |
| Malware, or an unlocked session on your machine | Everything | Critical | **Scrinium cannot solve this.** Keep your system up to date | Cannot be automated | Accepted | Total: a program running as your account can read what you can |
| Machine theft | Everything | High | System disk encryption (LUKS, FileVault, BitLocker). Scrinium detects it and warns if it finds none | `test_disk_encryption.py` | Partial | Scrinium encrypts nothing. **No disk encryption was detected on the reference machine.** On Windows it cannot be checked without administrator rights |
| A connector token is exposed | Sources | High | Keyring with a closed list of backends, opaque reference in the database. Minimal permissions and revocation once connectors are built | `test_secret_store.py`, `test_real_keyring.py` | Partial | Any program in your unlocked session can read the keyring |
| Secrets are indexed by accident | Secrets | High | Sensitive files excluded by name; known formats detected by content; a preview with the reason for each exclusion; built-in exclusions cannot be removed | `test_ingestion.py`, `test_ingestion_policy.py` | Partial | A secret in an unknown format would be indexed |
| A malicious repository runs code or reads outside its root | Machine | High | Only git commands that read objects, with dangerous configuration overridden. Folders read with descriptors and `O_NOFOLLOW`. No links, submodules or special files; nothing is unpacked. Limits on size, count and depth | `test_preview_explains_every_exclusion` (with hostile fsmonitor, filters, textconv and hooks), `test_hostile_config_is_live_for_plain_git`, `test_read_file_refuses_a_folder_swapped_for_a_symlink` | Tested | A bug in git itself when reading malformed objects. Git runs as your account |
| Scrinium indexes itself | Data | Medium | A source that contains the data directory, or is inside it, is rejected | `test_invalid_roots_are_rejected_without_details` | Tested | |
| Prompt injection in documents, tickets or code | Answers, rules, secrets | High | The model has no tools and no access to anything. Sources go between random per-request delimiters. Output is constrained by a schema or grammar, with limits. Every claim needs a valid citation or is discarded, and extraction citations come from the code, not the model. Secret formats are redacted. Answers are shown as plain text | `test_prompt_separates_instructions_from_untrusted_sources`, `test_all_claims_uncited_means_no_evidence`, `test_rag_evaluation` (real model) | Partial | No technique removes it: the model can write something false while citing a real source. That is why answers and rules are proposals to verify against their citations |
| Remote AI receives data without consent | Confidential | High | Embeddings and LLM only through the local `llama-server` (default) or Scrinium's own Ollama, both on loopback, with no proxies. No remote provider in the code; `require_local` | `test_remote_or_odd_urls_are_rejected`, `test_ollama_round_trip_ignores_proxies`, `test_chat_refuses_remote_urls`, `test_policy_refuses_a_remote_provider` | Tested | Ollama may log the text it receives. Models and tools are downloaded from the Internet only when you run `scrinium tools install` or `make models` |
| Instructions are lost because a prompt is too long | Answers | Medium | Fixed context and an input limit before sending; llama-server fails instead of truncating | `test_input_and_output_limits_are_enforced_before_sending` | Tested | |
| A query manipulates search (FTS5 or filter injection) | Data | Medium | Only quoted words in FTS5; fixed columns; LanceDB filters only with validated UUIDs | `test_fts_query_only_quotes_words`, `test_hostile_queries_never_fail` | Tested | |
| A compromised dependency | Everything | High | Lockfiles; `make audit` and `make sbom` ([SUPPLY_CHAIN.md](SUPPLY_CHAIN.md)); pinned, hash-checked tools and models | Manual, before each release | Partial | Pinning does not detect a version that was already malicious |
| Incomplete deletion | Data | Medium | Propagates to derived data and purges the disk (`secure_delete`, truncated WAL, optimized FTS5, old LanceDB versions removed) | `test_privacy.py` | Implemented | The SSD or file system may keep freed blocks; existing backups and exports keep their data |

## Risk specific to the reference machine

The project lives on an NTFS disk, where `chmod` has no effect. Verified: a file set to `chmod 600` stays `777`.

- **Source code:** any user of this machine who can reach the disk can read and modify it. That is acceptable while the code contains no secrets.
- **Scrinium's private data:** it goes in `~/.local/share/scrinium`, on ext4, where permissions work. If `SCRINIUM_DATA_DIR` points to the NTFS disk, Scrinium refuses to start (verified with `SCRINIUM_TEST_NTFS_DIR`).
- **Disk encryption:** the system partition (`/dev/sda6`, ext4) does not use LUKS. If the machine is stolen, Scrinium's data and the rest of the account can be read. This is the owner's decision: enabling LUKS means reinstalling or encrypting the partition.

## Development mode

`pnpm dev` weakens one control. The Vite proxy rewrites `Origin` so the runtime accepts its requests, and another page on `127.0.0.1` could abuse that proxy while it runs.

Use it only for development, never with real data. It does not affect `scrinium start`.

## What we do not claim

Passing these tests does not mean Scrinium is "secure". The tests are our own, with no external review, and cover only what is described here.
