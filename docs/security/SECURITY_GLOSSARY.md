# Security glossary

Every concept uses the same format: **what it is**, **the risk it addresses** and **what it does not protect against**.

## Loopback (`127.0.0.1`)

- **What it is:** an address that only exists inside your machine.
- **Risk:** another machine on your network or on the Internet connecting to Scrinium.
- **Does not protect against:** programs on your own machine, or web pages open in your browser.

## `Host` validation

- **What it is:** every request says which name it is addressed to. Scrinium only accepts `127.0.0.1:<port>`.
- **Risk:** *DNS rebinding*. A website points its domain at `127.0.0.1`, so your browser believes that talking to Scrinium is talking to that site.
- **Does not protect against:** local programs, which can send any `Host` they like.

## Origin and `Origin` validation

- **What it is:** a page's origin is its scheme, domain and port. The browser attaches it to requests, and the page cannot forge it.
- **Risk:** a foreign page calling the API.
- **Does not protect against:** programs outside the browser, which can forge the header.

## CORS

- **What it is:** permission for a website to read responses from another origin. Scrinium grants none.
- **Risk:** a website reading your data.
- **Does not protect against:** CORS alone does not stop the request from reaching the server. That is why the other layers exist.

## CSRF

- **What it is:** tricking your browser into performing an action with your session without you knowing. Scrinium requires a secret token on every action, and a foreign page cannot read it.
- **Risk:** unauthorized actions, such as deleting data.
- **Does not protect against:** a malicious script running inside Scrinium's own page (XSS).

## `HttpOnly` and `SameSite=Strict` cookies

- **What it is:** `HttpOnly` stops JavaScript from reading the cookie. `SameSite=Strict` stops it from being sent from other sites.
- **Risk:** session theft through scripts, and CSRF.
- **Does not protect against:**
  - other services on `127.0.0.1`, because "site" ignores the port: they count as the same site;
  - interception on the wire: there is no `Secure` flag, because Scrinium uses local http.

## Launch token

- **What it is:** a random, single-use value, valid for 2 minutes, exchanged for the session.
- **Risk:** anyone who finds the port simply walking in.
- **Does not protect against:** someone who reads your terminal, or the process list from another account on the machine, and uses it before you do.

## Local session

- **What it is:** proof that you already signed in. It lives only in memory, and only its hash is stored.
- **Risk:** other programs or users calling the API just because they are on your machine.
- **Does not protect against:** malware that controls your account or your browser.

## Content Security Policy (CSP)

- **What it is:** a list that tells the browser where the page may load code from. In Scrinium, only from itself.
- **Risk:** an injected script running, or sending data out.
- **Does not protect against:** bugs in Scrinium's own, already allowed code.

## Log redaction

- **What it is:** replacing anything that looks like a secret with `[REDACTADO]`, and never logging content.
- **Risk:** secrets leaking through log files or terminal captures.
- **Does not protect against:** secrets in a format it does not recognize. That is why request bodies and query strings are never logged either.

## File permissions (`0600`, `0700`)

- **What it is:** only your user can read the file or folder.
- **Risk:** another user on the machine reading Scrinium's data.
- **Does not protect against:**
  - root;
  - programs running as your account;
  - NTFS or exFAT disks, which ignore permissions.

## Keyring

- **What it is:** the operating system's secret store: Secret Service, the macOS Keychain, the Windows Credential Manager. Scrinium stores only a reference to the secret in its database.
- **Risk:** tokens stored as plain text in files.
- **Does not protect against:** malware in your unlocked session.

## Disk encryption

- **What it is:** LUKS, FileVault or BitLocker encrypt the whole disk. Scrinium detects LUKS and FileVault and warns if it finds none. It does not encrypt anything itself.
- **Risk:** theft of a powered-off machine.
- **Does not protect against:** a machine that is on and unlocked, or malware.

## Embeddings

- **What it is:** a list of numbers that represents the meaning of a text. Similar texts have nearby vectors, which is how search by meaning works.
- **Risk:** they are treated as confidential data. They are not an encrypted copy, and can reveal the text they came from.
- **Does not protect against:** anything. They are not a security control, and are stored in the same private directory as the rest.

## Prompt injection

- **What it is:** text inside a document, ticket or code that tries to give orders to the AI.
- **Risk:** the AI revealing data or ignoring its rules.
- **Does not protect against:** no control removes it completely; what the AI can do is limited instead.
  - In Scrinium the model has no tools: the worst it can do is write something false.
  - Every claim shows its citation so it can be checked.
  - Extracted rules are proposals until a person verifies them.
