# ADR 0006 — Web client transport: aibd's side of the contract

- Status: proposed (input: maintainers' summaries of discussions 2 and 5, 9320/a63ab,
  2026-11-07; performance budget spec v0.2.1, P4 / B6 / B7)
- Deciders: @a9136 (aibd side), the web seat (browser side), 9320/a63ab (approval)
- Scope: what aibd guarantees to a browser or desktop-shell client. The browser-side
  rules (redraw only on change, one stream per profile via Web Locks, hidden-tab
  behaviour, reconnect backoff) belong to the web seat and are only referenced here.

## Context

ADR 0003 fixes SSE for events, REST for commands, a global cursor, and "no credential in a
URL". ADR 0005 fixes that streaming fragments are not persisted and that catch-up returns
persisted events plus snapshots. The web client needs three more things from aibd: a way
to obtain and present a credential without putting it in the URL, a clear signal for "you are
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
  sends `Authorization: Bearer <credential>` (start-up token or page credential, section
  7), and sends `Last-Event-ID` itself on reconnect.
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

### 7. How a browser obtains a credential: pairing, then a page credential (discussion 5)

Decided (maintainers' summary of discussion 5, 9320/a63ab, 2026-11-07). The delivery of
the pairing code to the page is still open; see the end of this section.

**No cookie.** A cookie is scoped by host, not by port: a cookie for `127.0.0.1` is sent
to every port on `127.0.0.1`. `SameSite=Strict` does not help, because another port on the
same IP counts as the same site. The `__Host-` prefix requires https, which a local http
server cannot use. So any web server another OS account runs on a local port could collect
the cookie. aibd therefore sets no cookie, and needs no CSRF protection.

**Pairing.**
- `aib` (authorised by the owner-only token file) asks aibd for a one-time pairing code.
  The code is single use and short lived (≤ 30 s). aibd stores only its hash.
- The page redeems the code with a `POST`. The page credential comes back in the
  **response body**, never in a URL or a cookie.
- Redeeming twice, after expiry, or concurrently succeeds at most once.

**Page credential.**
- Sent as `Authorization: Bearer <credential>` on the stream and on REST (section 2).
- Stored by aibd only as a hash. Lifetime 12 h (trial value). Revocable. All page
  credentials become invalid when aibd restarts.
- Kept by the page in memory. It may also be kept in `sessionStorage` so a reload does not
  need a new pairing (this tab only; gone when the tab closes; another port is another
  origin and cannot read it). **Never** in a cookie or `localStorage`.

**Guard.** The guard accepts exactly two kinds of Bearer: the start-up token (owner-only
token file) and a valid page credential. Everything else in sections 2 and 6 is
unchanged: no credential in any path or query, no page contains a credential, logs never
contain one.

**Not decided yet: how the pairing code reaches the page.** Two proofs of concept run first
(sandbox only); the pairing endpoint is not built until their results are reviewed.
- P1, URL fragment `#pair=…`: the fragment is not sent to the server; the page redeems it
  on load and clears it with `history.replaceState`. But the window's URL appears in
  process arguments, which other accounts can read with `ps`, so P1 needs takeover
  detection: when the legitimate page finds its code already used, every credential issued
  for that code is revoked and the page asks to reopen. **P1 literally puts a credential
  in a URL, which section 2 forbids.** Whether to allow an exception for fragments is a
  separate discussion after the PoC; it is not allowed by default.
- P2: `aib` writes a `0600` HTML file inside a `0700` owner-only folder; the file holds the
  code and posts it to the pairing endpoint. Process arguments show only the path. To
  verify: whether aibd can accept a `POST` from a `file://` page safely (only on the
  pairing endpoint; how `Origin: null` is handled), and which shells support it.
- If neither holds: the terminal shows a short code and the person types it in.

A1-a (a per-install `*.localhost` host name plus a cookie) is kept as a later option and
is not part of this round.

**Required tests (each with a knife that turns only its own assertion red, plus a positive
path so "refuse everything" cannot pass):**
- Reading the API or the stream without pairing is refused.
- Pairing code reuse, expiry and concurrent takeover: at most one success.
- A hostile page on another local port can neither read data nor change anything.
- Wrong, `null` or missing `Origin`, a forged `Host`, DNS rebinding and preflight abuse
  are refused.
- No credential or pairing code appears in logs, query strings, HTML or error output.
- Testing from a real second OS account is UNVERIFIED until it has been run.

## Consequences

- Positive: the "no credential in a URL" rule stays without exceptions; there is no
  ticket endpoint to secure until one is proven necessary.
- Positive: one place (aibd) decides when a client is too far behind; approvals, errors and
  terminal events survive any catch-up path.
- Negative: the client implements SSE parsing and reconnection itself (about 50 lines;
  backoff was required anyway).
- Negative: the web UI cannot be hosted on another origin. This is intended.
- Negative: a page reload without `sessionStorage`, or any aibd restart, needs a new
  pairing.
- Open: how the pairing code reaches the page (section 7, P1 / P2 proofs of concept).
- Open: the catch-up threshold and the `must_deliver` cap are trial values until measured
  (B4).
