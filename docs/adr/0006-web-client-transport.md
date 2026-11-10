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
  - W1 (a9136 Q4, 2026-11-10): the files are compiled into aibd (cargo feature `web`), so
    the previous version's files live only in the previous binary. W1 ships one bundle with
    no lazy chunks, and the page does a full reload when a hashed file returns `404`
    (`index.html` is `no-cache`, so the reload gets the new version). That keeps an open page
    from breaking. Once the bundle is split into chunks, the previous version's `dist` is
    compiled in alongside the new one at release time.
- API responses send `Cache-Control: no-store`; static cache rules never apply to `/api/`.
- **No page contains the token.** `index.html` and every static file are identical for
  every caller. Other OS accounts on the same machine can reach `127.0.0.1`, so a token
  embedded in HTML would bypass the owner-only token file. Test: an unauthenticated
  `GET /` response does not contain the token. The one exception is the pairing exchange
  page, which only a valid one-time code gets (section 7).

### 7. How a browser obtains a credential: pairing, then a page credential (discussions 5, 6, 6b, 7)

Decided (maintainers' summaries of discussions 5, 6 and 6b, 9320/a63ab, 2026-11-07). Code
delivery has a candidate (P2) that has not passed security acceptance yet.

**No cookie.** A cookie is scoped by host, not by port: a cookie for `127.0.0.1` is sent
to every port on `127.0.0.1`. `SameSite=Strict` does not help, because another port on the
same IP counts as the same site. The `__Host-` prefix requires https, which a local http
server cannot use. So any web server another OS account runs on a local port could collect
the cookie. aibd therefore sets no cookie, and needs no CSRF protection.

**Pairing.**
- `aib` (authorised by the owner-only token file) asks aibd for a one-time pairing code
  (details below).
- The page redeems the code with a form `POST`. The page credential comes back in the
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

**Delivering the code: P2 is the candidate, not yet accepted (discussion 6).** The
proofs of concept so far ran headless against a mock server, so they are not a security
acceptance. P2 is not enabled by default. If any check below fails, the fallback is the
terminal short code typed in by the person, or the feature is reported as blocked. It
never falls back silently to a code in a URL.
- P2: `aib` writes a `0600` HTML file inside a `0700` owner-only folder. The file holds the
  code and posts it to the pairing endpoint with a form. Process arguments show only the
  path.
- P1 (URL fragment `#pair=…`) is **rejected**: no exception to "no credential in a URL".

**Security rests on the code, not on `Origin: null`.** `null` is also sent by sandboxed
iframes, `data:` pages and any other local file. It identifies nothing.

**Pairing endpoint (specification; built once the aibd skeleton is in place):**
- `POST /api/v1/pair/codes`: start-up token only, from `aib`.
  - Response body: `{code, expires_in: 15}`.
  - The code is 32 bytes from the OS secure RNG. It is single use and bound to this aibd
    instance. aibd stores only its hash.
  - Its lifetime is 15 s. This is a trial value: p50 / p95 / p99 and the tail timeouts are
    measured in the PoC. A failure fails closed and can be re-paired.
  - A successful window-open command does not mean the legitimate page redeemed the code.
    A silent failure is possible, and this limit is stated, not hidden.
- `POST /api/v1/pair/redeem`:
  - Accepts **only** `application/x-www-form-urlencoded`, with a body of at most 1 KiB
    and the single field `code`.
  - `Origin` must be `null` or aibd's own origin. The Host allow-list still applies, so
    DNS rebinding is refused.
  - GET, JSON or any other content type is refused. `Origin: null` on any other path is
    refused.
  - Redemption is atomic and rate limited: 10 failures a minute per aibd is a trial value;
    above it the endpoint returns `429`.
  - Every failure returns the same fixed message.
- Outcomes of redeem:
  - **Valid code:** `200` with the exchange page (below).
  - **Any second redemption of the same code:** `409`, and **every** credential issued from
    this code is revoked: the first redeemer's and nobody else's. Other sessions are
    unaffected.
    - There is no nonce and no exemption for a re-sent form. The exchange page leaves
      history with `location.replace`, so a legitimate reload does not re-send it.
    - Revocation records are kept at least as long as a credential lives (12 h), so a
      late re-send is still recognised.
    - Revocation closes that credential's open streams at once.
  - **Invalid, expired or malformed code:** `401`. A wrong guess never affects an
    unrelated session.

**A new credential is restricted until it is confirmed (discussion 6b).**
- A countdown running out is **not** a confirmation. Expiry proves nothing about who
  redeemed first, and an attacker could simply wait.
- Until confirmed, the credential gets **neither writes nor sensitive reads**. The event
  stream, snapshots, conversations, files and personal data all count as sensitive.
- It may call only this allow-list; everything else returns `403 pairing_unconfirmed`:
  - `GET /api/v1/pair/status` → `{state: "awaiting_confirmation" | "confirmed" |
    "revoked", expires_in}`;
  - `POST /api/v1/pair/revoke` (its own credential);
  - `GET /api/v1/health`.
- If it is not confirmed within the confirmation window (a trial value, set by the PoC),
  it is revoked and the person pairs again.
- While restricted, the page sends no write at all. Nothing is queued to resend
  automatically. Later retries only cover requests the server explicitly refused and did
  not execute, and they carry a stable idempotency key.
- **Unlocking needs an independent channel that an attacker who read the file cannot
  reach** (DECISION-7 B). It does not count as established until a PoC proves it, and it
  stays UNVERIFIED until a real person has done it once:
  - **A short code is always shown and compared by the person.** It is bound to this aibd
    instance, this pairing request, a digest of the credential, the operation (unlock)
    and an expiry, and it is never in the `0600` file.
  - **At least one of the two places that show it is a channel the attacker cannot
    control**; the candidate is the terminal. The other is the page.
  - **Touch ID is never enough on its own.** It proves that the person is present and
    approves, not which browser asked: approving alone would unlock whoever redeemed
    first (PoC S3b). If the Touch ID pop-up is used, it shows the same short code and
    only adds the "person present" proof.
  - The code cannot be replayed, expires fail closed and is rate limited. Cancelling or
    closing the prompt never authorizes. That another AI on the same account, or a
    malicious local page, cannot press approve is a PoC item, not an assumption.
  - Showing both a code and a pop-up is **not proof of safety**. The PoC covers: page A
    pairs but request B arrives first; codes swapped or replayed; two requests at once;
    the real page never comes; an impostor pop-up; the person cancels.
- If confirmation fails or times out, the result is manual pairing or an explicit
  "blocked". Expiry never turns into confirmed.
- Revoking after detection is not prevention: it cannot undo what was done in the
  takeover window. That is why the restriction comes first. Risk 3 actions need Touch ID
  as before.
- If it is later decided to rely on the `0600` file permissions alone and accept the
  remaining risk, that is a **risk acceptance**. It needs Andy's explicit consent and is
  never described as authentication.

**Exchange page: an exception to section 6.** The `200` response of
`/api/v1/pair/redeem` contains the page credential. It is therefore an exception to "no
page contains a credential", which covers the static pages that are identical for every
caller. Only a valid code gets this page.
- Headers: `Cache-Control: no-store`, `Referrer-Policy: no-referrer`, and a CSP that loads
  nothing external with `frame-ancestors 'none'`.
- It is not served from the static file path and is never logged.
- The page stores the credential in `sessionStorage` and then calls `location.replace('/')`.
- Back, bfcache, the disk cache and session restore must be tested not to bring the
  credential back.

**The local file.**
- Before writing, `aib` verifies that the `0700` folder is owned by the user.
- The file is created with `O_CREAT | O_EXCL | O_NOFOLLOW`, under a random name that is
  never reused. Owner and mode are read back; if they are wrong, no window is opened
  (fail closed).
- The file is deleted after success, failure, expiry, or a failed window open.
  If it cannot be deleted, an error is reported. After a crash, the next start removes
  stale files, and only its own.
- On Windows the file uses the same `set_owner_only` as the token file. UNVERIFIED.

**`sessionStorage` is copied into a new tab that keeps its opener** (measured by @3f312 in
Chrome). Every new page is therefore opened with `noopener` / `noreferrer`. Tests cover
`window.open`, `target` links, duplicated tabs, the same IP on another port, reload and
restore.

**Still UNVERIFIED** (stated, not hidden):
- a real aibd with a Chrome `--app` window;
- other desktop shells;
- a real second OS account (waits for the test account to be chosen);
- Windows ACLs.

Threats outside local isolation are written as boundaries, per the same-user AI threat
model guide: the same-account AI, XSS, browser extensions.

A1-a (a per-install `*.localhost` host name plus a cookie) is kept as a later option and
is not part of this round.

**Required tests (each with a knife that turns only its own assertion red, plus a positive
path so "refuse everything" cannot pass):**
- Reading the API or the stream without pairing is refused.
- Pairing code reuse, expiry and concurrent takeover: at most one success. Any second use
  returns `409` and revokes the first credential. Revocation closes its open streams. A
  reload, a bfcache restore or a late re-send is recognised as a second use.
- A second redemption racing the expiry; clock jumps never unlock.
- A wrong guess does not affect unrelated sessions.
- `null` origins without a code, with a random, expired or malformed code, or replaying a
  request, are refused. `null` on any other endpoint is refused. A GET carrying a code is
  refused.
- The file is gone after use. A mode other than `0600`, a symlink, or a pre-created file of
  the same name means no window is opened.
- An unconfirmed credential gets `403 pairing_unconfirmed` for every call outside the
  allow-list, both writes and sensitive reads (stream, snapshot, events).
- An attacker who read the whole file redeems first and the legitimate page never
  appears. After expiry, writes and sensitive reads are still refused, and the credential
  is revoked at the end of the confirmation window.
- A hostile page on another local port can neither read data nor change anything.
- Wrong, `null` or missing `Origin`, a forged `Host`, DNS rebinding and preflight abuse
  are refused.
- No credential or pairing code appears in logs, query strings, static HTML, error output
  or browser history.
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
- Open: P2 security acceptance (real `--app` window, other shells, a second OS account,
  Windows) and the independent confirmation channel that lifts the restriction (section 7).
- Negative: until a confirmation channel is proven, a freshly paired page can only show
  "waiting for confirmation".
- Open: the catch-up threshold and the `must_deliver` cap are trial values until measured
  (B4).
