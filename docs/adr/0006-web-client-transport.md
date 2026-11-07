# ADR 0006 — Web client transport: aibd's side of the contract

- Status: proposed (input: maintainers' summary of discussion 2, 9320/a63ab,
  2026-11-07; performance budget spec v0.2.1, P4 / B6 / B7)
- Deciders: @a9136 (aibd side), the web seat (browser side), 9320/a63ab (approval)
- Scope: what aibd guarantees to a browser or desktop-shell client. The browser-side
  rules (redraw only on change, one stream per profile via Web Locks, hidden-tab
  behaviour, reconnect backoff) belong to the web seat and are only referenced here.

## Context

ADR 0003 fixes SSE for events, REST for commands, a global cursor, and "no credential in a
URL". ADR 0005 fixes that streaming fragments are not persisted and that catch-up returns
persisted events plus snapshots. The web client needs three more things from aibd: a way
to read the stream without putting a credential in the URL, a clear signal for "you are
too far behind, take a snapshot", and cache rules for static files.

## Decision

### 1. Same origin is a precondition

aibd sends no CORS headers (ADR 0003), so a page on another origin cannot read aibd's
responses, whether through `fetch` or `EventSource`. **The web UI is served by aibd
itself** (same origin, `http://127.0.0.1:<port>`), or by a desktop shell that forwards
requests to aibd. aibd's own origin is always in the Origin allow-list. No CORS is added
to make another arrangement work.

### 2. Reading the stream: `fetch`, token in a header

- Default: the client reads `GET /api/v1/events/stream` with `fetch` + `ReadableStream`,
  sends `Authorization: Bearer <token>`, and sends `Last-Event-ID` itself on reconnect.
- "No credential in a URL" keeps **no exception**: the guard keeps refusing credentials
  in the path or query on every route, including the stream.
- The client owns reconnection (bounded exponential backoff with jitter, cap 60 s as a
  trial value). aibd adds nothing for it.
- No heartbeat by default (ADR 0003 update).

### 3. Fallback only: one-time stream ticket (not built)

Used only if a client proves it must use `EventSource`, which cannot set headers. Until
then there is no endpoint. If it is built, it is exactly this:

- `POST /api/v1/stream-tickets` with the Bearer token and the usual Host / Origin checks
  → `{ "ticket": "...", "expires_in": 30 }`.
- 32 random bytes, base64url, unrelated to the token. aibd stores only a hash. A ticket is
  redeemed **once**, atomically; unredeemed tickets expire after **30 s**; at most **16**
  outstanding, further requests are refused.
- Bound to the issuing Origin and to `GET /api/v1/events/stream` only, as the query
  parameter `ticket`. On any other route `ticket` is a credential in a URL and is refused
  with `token_in_url`. A redeem with another Origin is refused.
- Every failure (used, expired, unknown, wrong Origin) returns the same code
  `ticket_invalid`. Logs record only the route template; the ticket never appears.
- Every reconnect asks for a new ticket.
- Tests, each with a knife: second use refused, expired refused, other Origin refused,
  other route refused, ticket value absent from logs.

### 4. Catch-up and the `truncated` signal

- `GET /api/v1/events?cursor=<global_seq>&limit=<n>` returns
  `{ "events": [...], "next_cursor": <global_seq>, "truncated": <bool> }`.
- `truncated: true` means the client is further behind than aibd will replay; it then
  returns **no events** and the client must take a snapshot. The threshold lives in aibd
  only (configurable, a trial value until B4 is measured); clients never guess it.
- The same applies when a stream is opened with a `Last-Event-ID` that is too old: aibd
  sends one `truncated` event and closes the stream.

### 5. Snapshots never skip what matters

- `GET /api/v1/snapshot?since=<old cursor>` returns
  `{ "global_seq": <s>, "state": {...}, "must_deliver": [...], "must_deliver_truncated": <bool> }`.
- `global_seq` is the cursor the snapshot is consistent with; the client continues the
  stream from there (`Last-Event-ID: <s>`), so nothing between the snapshot and the
  stream is lost or duplicated.
- `state` holds current seats, sessions and the latest snapshot of each message still in
  progress (ADR 0005).
- `must_deliver` lists every **approval request, error and terminal event** with
  `since < global_seq <= s`, in order. A snapshot can replace ordinary history, never
  these. They are rare; if there are more than a cap, `must_deliver_truncated` is true and
  the client pages them through `GET /api/v1/events?cursor=...&kinds=approval,error,terminal`
  before showing the session as up to date.
- Closing a UI connection (hidden tab, closed window) never cancels or pauses authorized
  background work.

### 6. Static files served by aibd

- Asset files have a content hash in their name and are served with
  `Cache-Control: public, max-age=31536000, immutable`.
- `index.html` is served with a strong `ETag` (content hash) and `Cache-Control: no-cache`;
  a matching `If-None-Match` returns `304` with no body.
- Pre-compressed `.br` / `.gz` files are sent with `Content-Encoding` and
  `Vary: Accept-Encoding`; a client that accepts neither gets the uncompressed file.
- On upgrade, hashed files of the previous version stay available, so an open page that
  still references them keeps working.
- API responses send `Cache-Control: no-store`; static cache rules never apply to `/api/`.
- **No page contains the token.** `index.html` and every static file are identical for
  every caller. Other OS accounts on the same machine can reach `127.0.0.1`, so a token
  embedded in HTML would bypass the owner-only token file. A browser gets access through
  the one-time pairing of ADR 0003 (HttpOnly cookie), a desktop shell through its own
  forwarding. Test: an unauthenticated `GET /` response does not contain the token.

## Consequences

- Positive: the "no credential in a URL" rule stays without exceptions; there is no
  ticket endpoint to secure until one is proven necessary.
- Positive: one place (aibd) decides when a client is too far behind; approvals, errors and
  terminal events survive any catch-up path.
- Negative: the client implements SSE parsing and reconnection itself (about 50 lines;
  backoff was required anyway).
- Negative: the web UI cannot be hosted on another origin. This is intended.
- Open: the catch-up threshold and the `must_deliver` cap are trial values until measured
  (B4).
