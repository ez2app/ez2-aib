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

### 3. One-time stream ticket: unapproved option (not built)

A ticket in the query string **conflicts** with the rule in section 2 ("no credential in
a URL, no exception"): before it is redeemed it is a bearer credential that can be taken.
It is therefore **not approved**. It may only be built after a new explicit ruling and a
change to this ADR that removes the conflict. Until then nothing below is in force.

For that future discussion only, the option as proposed (DISCUSS-2): `POST
/api/v1/stream-tickets` with the Bearer token → `{ticket, expires_in: 30}`; 32 random
bytes, hash stored, single atomic use, 30 s, at most 16 outstanding, bound to the issuing
Origin and to `GET /api/v1/events/stream`, every failure `ticket_invalid`, never logged,
a new ticket for every reconnect.

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
  these. If there are more than fit in one response, `must_deliver_truncated` is true and
  the client pages the rest through the dedicated endpoint below, **not** through
  `/api/v1/events` (whose catch-up threshold would refuse an old cursor).
- `GET /api/v1/must-deliver?since=<old cursor>&upto=<s>&after=<continuation>&limit=<n>`:
  - returns only approval / error / terminal events with `since < global_seq <= upto`,
    in ascending `global_seq` order (stable: `global_seq` is unique);
  - `upto` is fixed by the snapshot, so events written while the client pages are not
    mixed in; they arrive through the stream that starts at `s`;
  - `after` is the last `global_seq` of the previous page (`since` for the first page);
    the response carries `next` (the last `global_seq` returned) or `null` at the end;
  - it is **not** subject to the ordinary catch-up threshold; it is bounded by `limit`
    per page (resource cap) only;
  - retention: these events are kept for at least the retention window (a trial value,
    configurable). If `since` is older than the oldest retained event, the response says
    `unrecoverable: true`; the client must show that history is incomplete and must
    **not** present the session as up to date.
- Tests for this contract: more events than one page; a `since` beyond the ordinary
  catch-up threshold; writes landing while the client pages (none appear before the
  stream); pruning past `since` gives `unrecoverable`.
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
  embedded in HTML would bypass the owner-only token file. Test: an unauthenticated
  `GET /` response does not contain the token.

### 7. Open: how a browser obtains a credential (discussion 5)

Section 2 assumes the page holds a bearer credential to put in `Authorization`. How it
gets one is **not decided here** (discussion 5, 2026-11-07). In particular this ADR does
not claim that an HttpOnly cookie can become a bearer header: page JavaScript cannot read
an HttpOnly cookie. Options under discussion: aibd accepts a cookie set by a one-time
pairing (with CSRF protection); a credential held only in page memory after pairing; a
desktop shell supplying the credential over its own channel. Also to settle there: cookies
are scoped by host, not port, so a cookie for `127.0.0.1` is sent to every port on it.
Until that summary, sections 2 and 4–6 describe the transport once a credential exists.

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
