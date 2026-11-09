# ADR 0003 — Event push over SSE, commands over REST, one global cursor; local API auth

- Status: accepted for Phase 0–1 (a63ab, 2026-11-07)
- Deciders: @a9136 (proposal), 9320/a63ab (approval)

## Context

Spec section 8 leaves WebSocket vs SSE to a PoC. Section 4.3 lists `events` and
`event_outbox` as separate tables with uniqueness on `(aggregate_id, sequence)`, while
`GET /events?cursor=…` must resume across all aggregates. Section 9.1 requires loopback
binding, Host/Origin checks and a startup token kept out of URLs and logs.

## Decision

### Transport

- **Commands are REST** (`POST`, with an `Idempotency-Key` header on every write).
- **Events are SSE** (`GET /api/v1/events/stream`, `text/event-stream`). Each SSE `id:` is
  the event's `global_seq`. On reconnect the client sends `Last-Event-ID` (or
  `?cursor=` on `GET /api/v1/events` for paged catch-up); the server replays from SQLite
  and then switches to live.
- Each subscriber has a **bounded** in-memory buffer. If it overflows, the server ends that
  stream; the client reconnects and catches up from SQLite. A slow client can never
  block the core (spec 5).
- **WebSocket is reserved** for bidirectional terminal streaming (PTY/ConPTY view) from
  Phase 3 on, under the same auth and Origin rules.

Why: event delivery is one-way; SSE gives resume-by-id for free, works with plain HTTP
middleware (auth, Host/Origin, tracing), and needs no framing protocol of our own.

### Events table is the outbox

- `events(global_seq INTEGER PRIMARY KEY, event_id TEXT UNIQUE, aggregate_type,
  aggregate_id, sequence, type, schema_version, correlation_id, occurred_at, payload)`
  with `UNIQUE(aggregate_id, sequence)`.
- State change and its events are written in **one transaction**. Because all consumers
  run inside aibd, the events table itself is the outbox; `event_consumers(name,
  last_global_seq)` records each consumer's cursor. Consumers are idempotent by
  `event_id`.
- A separate `event_outbox` table is added only if events must leave the process (cloud
  sync, Phase 6+).

### Local API authentication

- Bind `127.0.0.1` only (IPv6 `::1` only if added explicitly; `0.0.0.0` is refused at
  config load).
- **Host check:** the `Host` header must be exactly `127.0.0.1:<port>` or
  `localhost:<port>`. Anything else → `421`/`403` with code `host_not_allowed` (defeats DNS
  rebinding).
- **Origin check:** if an `Origin` header is present it must be in the configured
  allow-list (empty by default; the desktop shell and the bundled web UI add their own
  origin). A disallowed Origin → `403 origin_not_allowed`. No CORS headers are ever sent,
  so browsers cannot read cross-origin responses either.
- **Token:** 32 random bytes from the OS CSPRNG, base64url, generated at each start,
  written to `<data_dir>/run/aibd.token` with owner-only permissions (0600 on Unix;
  Windows ACL is CI-windows / UNVERIFIED-desktop). Accepted **only** as
  `Authorization: Bearer <token>`. A token in the query string is rejected with
  `token_in_url` (even if correct) so it never lands in logs or history. Comparison is
  constant-time. `/api/v1/health` needs no token but does pass Host/Origin checks and
  returns only `{status, api_version}`.
- Browser clients (EventSource cannot set headers) will use a one-time pairing exchange
  that sets an `HttpOnly; SameSite=Strict` cookie, plus CSRF protection on writes. Not in
  Phase 0.

### What the token does not protect against (explicit)

Any process running as the **same OS user** can read `aibd.token` (and anything else in
the user's files) and call the API. The token stops other users, web pages, DNS
rebinding and accidental exposure; it is **not** a defence against a same-user AI agent.
Risk 3 operations (delete data, payments, credential export, arbitrary system commands,
high-privilege plugins) therefore require an OS-native human-presence proof (Touch ID /
Windows Hello) bound to `actor + operation + resource + parameter_digest + nonce +
expiry` (spec 9.2), following the maintainers' same-user AI threat model (a file, hash or env var is not a defence; a biometric prompt, passphrase or signature is). Phase 1 ships the trait
for that proof; no Risk 3 operation exists before it is implemented.

## Consequences

- Positive: one cursor for every client; resume is built in; fewer moving parts than WS.
- Positive: one transaction per state change, no outbox drift.
- Negative: browser EventSource needs the cookie pairing flow before the web UI can run
  outside Tauri. Planned for Phase 3.
- Negative: SSE over HTTP/1.1 counts against the browser's per-host connection limit
  (6). The web UI keeps a single stream and multiplexes by event type. *See the
  2026-11-07 update: sharing one stream across tabs is not settled here.*

## Update 2026-11-07 (performance budget, ADR 0005)

- **Streaming fragments are not persisted.** `message.delta` is pushed live over SSE and
  never written to `events`; it has no `global_seq` and no `id:` line. What is persisted:
  state changes, `message.completed`, and terminal / approval / error / cancel events.
- For an in-progress message aibd stores a **snapshot** of the partial text at most once
  per second. On reconnect (`Last-Event-ID` or `?cursor=`) the client gets every persisted
  event after its cursor, then the latest snapshot of each message still in progress, then
  live events. It does not get the fragment-by-fragment history.
- Coalescing per aggregate: trailing 50 ms, at most 250 ms; fragments are merged into one
  push every 50 ms. Terminal, approval, error and cancel events are never coalesced.
- No periodic SSE heartbeat by default (loopback has no proxy that drops idle streams). If
  one is ever added it is an `Operation` timer that lives only while that stream is open
  (ADR 0005).
- Clients must use SSE for updates; periodic GET on a route that has an SSE equivalent is
  a bug, and aibd flags it in `/api/v1/metrics`.
- **Open, not decided in this ADR (spec P4, web / lobby seat):** one SSE stream shared by
  all tabs, catch-up when a hidden tab becomes visible again, stopping non-essential
  network while hidden, and their exceptions. aibd's side (cursor resume, snapshot,
  bounded buffers) is fixed here; the browser-side contract follows in a later ADR from
  the web seat, after spec v0.2.1.

## Update 2026-11-09 (Risk 3: who a session may start, M1 gate)

Four rulings (a63ab 11-09 19:12 / 19:14 / 19:15, Andy approved 19:1x):

1. **Shells and interpreters are never on the list.** `POST /api/v1/sessions` starts only
   programs on an allow-list (`AIBD_SESSION_POLICY`, a JSON file read once at start; unset
   = nothing may start). Shells, interpreters and launchers (sh, bash, zsh, cmd,
   PowerShell, python, node, ruby, perl, osascript, env, xargs, sudo …) are refused both
   when the file is read and at every spawn. Programs are compared by **real path**
   (symlinks resolved), so a link to `sh` or a renamed link is refused. On Windows the
   comparison ignores case, and the name check drops `.exe`. `argv[0]` with `..` is
   refused before anything is resolved, and aibd runs the real path it checked. If you
   really need an interpreter later, write a new ADR. It needs per-program rules (a fixed
   script path; no `-c` / `-e` / `-m` / program on stdin) and is Risk 3 with approval.
2. **A renamed copy of a shell passes M1 (known gap, I4).** The M1 list guards against
   mistakes and against an API caller passing an arbitrary path. It is **not** a defence
   against a same-user AI. That AI can edit the policy file or copy `sh` under another
   name. A permission or hash check on the file would not change that, and is not added.
   The defences come in three steps:
   - M2: the provider adapter checks the executable's signer (macOS codesign Team ID,
     Windows Authenticode subject). A copied `sh` is not signed by the provider vendor.
   - M4: the approval centre, with Touch ID / Windows Hello.
   - Phase 5: the installer keeps the list where the user cannot write.

   The I4 test asserts today's "allowed". It must flip to "refused" when M2 lands.
3. **Only standalone signed installs are supported.** An npm install of a provider runs
   through node. aibd refuses it with `provider_install_unsupported` (403), naming the
   standalone build, rather than relax the node rule. The cases are a `.js` real path, or
   a `#!` line or `.cmd` shim that names node. Measured standalone builds (11-09, macOS
   only): claude 2.1.319 (Anthropic, Team Q6L2SF6YDW) and codex 0.160.0 (Team 2DC432GLL2).
   Both are signed Mach-O binaries. Windows waits for a real machine.
4. **A list entry is a name plus the expected signer.**
   `{"name": "claude", "path": "/abs/claude", "signer": "Q6L2SF6YDW"}`. In M1, every spawn
   resolves the real path again with no cache, so a version update that moves the target
   is followed. M1 records `signer` and does not check it. From M2 the signer decides, so a
   versioned path change does not matter.

Env names are on the same file's `env` list. Anything else answers `env_not_allowed`.
The fixed deny list (`AIBD_*`, `DYLD_*`, `LD_*`, `PATH`) cannot be listed. Refusals happen
before anything starts. They take no slot, write no event, and name the setting, never a
value from the request. Gaps that remain:
- A file swapped between the check and exec (P8) is not caught.
- **The child inherits aibd's whole environment, minus `AIBD_*`** (`platform::session::child_env`).
  That includes any other variable in aibd's environment, API keys among them. The caller
  cannot add to it beyond the `env` list, and cannot pass `PATH`. Narrowing it to a fixed
  list (PATH / HOME / USER / LOGNAME / LANG / TMPDIR, plus what claude / codex are measured
  to need) is a separate change after preview.3 (a63ab 11-09 19:55).
