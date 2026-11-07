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
  (6). The web UI keeps a single stream and multiplexes by event type.

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
