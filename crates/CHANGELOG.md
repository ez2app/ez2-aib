# Changelog — Rust workspace (aibd)

The Python `aib` keeps its own CHANGELOG; this file covers the `crates/` workspace only.
The version lives in one place: `[workspace.package] version` in the root `Cargo.toml`.

## Unreleased

- `Idempotency-Key` on the session writes (ADR 0003; T81): `POST /api/v1/sessions`,
  `POST /api/v1/seats/{id}/sessions` and `DELETE /api/v1/sessions/{id}`. A retry with the
  same key starts or stops nothing again and gets the first status with the session as it
  is now (`Idempotent-Replayed: true`, also after a restart); the same key for another
  request, or while the first one still runs (or was cut off by a stop of aibd), is
  `409 idempotency_conflict`; a refused request gives its key back. The key is bound to a
  SHA-256 of the request (argv and env stay out of the store, spec 9.4) and the stored
  answer is only the session id (`aib_store::IdemClaim`: claim, finish, release). The
  pairing writes are not covered: their answers carry one-time codes.
- macOS: a late-exec candidate's environment is read by `aib-sys` (`Macos::environ`, new
  `aib_sys::procargs`) and cut at its real end. sysinfo 0.40.1 skips every run of NULs in
  `KERN_PROCARGS2` and read the apple strings after the variables (`ptr_munge=`, `main_stack=`)
  as variables, so a process started with a cleared environment (`env -i`) looked like one
  without the session marker and was let go as "not ours" instead of being reported
  (#614 CI). A failed read keeps the candidate (unreadable or other coalition).
- macOS: a start waits up to `HOLD_LIMIT` + 1 s (6 s, `server::LOCK_WAIT`) for `aibd.lock`
  before it says `AlreadyRunning`. A program held before exec keeps copies of `aibd.lock` and
  the listening socket until it execs or gives up at `HOLD_LIMIT` (ADR 0004), so a restart
  right after a crash, or a second server in the same process, saw the lock taken (main
  74ec2d39 red: `session_queue` restart test 3/3 on macOS). A second aibd that really runs is
  still refused, 6 s later. Other platforms do not wait. The hold ends at an absolute
  deadline, so a signal does not extend it.

## 0.2.0-preview.5 — 2026-11-12

(preview.3 and preview.4 were never split out here; this section covers everything since
preview.2.)

- A macOS stop no longer holds the slot for an unreadable candidate that is in another
  coalition (a launchd-started XPC helper such as DockHelper, whose environment macOS will not
  show us). Candidates are classified by a pure `classify()`; one that is readable in both
  coalitions and different is listed as `other_coalition` in the stop record and let go; one
  that cannot be placed still gives `candidates_unreadable` and holds the slot (#618).
- `aibd ctl seat open <name> -- <absolute-program> [args...]`: seat create + session start in
  one step. The program is resolved first, so a bad argv leaves no seat; a session start that
  fails after the seat was made prints `{"seat": …, "session": null}` and the server's error,
  so `session start <seat-id>` can be retried (#630).
- `ErrorCode::ALL` can no longer drift from the enum unnoticed (a63ab 11-10; a merge once
  turned two edits to `25` into a 26-entry array). New `ErrorCode::ordinal()` is an exhaustive
  match, so a new code does not build until it is numbered; build-time checks fail when a
  number points outside `ALL` or an `ALL` entry is not at its own number (missing or
  repeated codes, wrong order). A test pins the wire order. `ALL` stays a public array.
  Knives: `scripts/knives-errorcode.sh` (build-time knives use `scripts/knife-build.py`).
- `aib-cloud-host build-info` prints one fixed line, `aib-build-info version=<v> commit=<40 hex>
  dirty=<0|1>;`, which `build.rs` takes from git only (dirty = changes in `crates/`, `Cargo.toml`,
  `Cargo.lock`, `rust-toolchain.toml` or `.cargo/`, untracked files included) (no environment variable can set it; no git
  ⇒ `commit=unknown dirty=1`). The same line is in the file, so it can be read on a Mac from a
  Linux build. `--version` is unchanged.
- `aib-release host-build`: before a build goes onto a host, checks it against
  `refs/remotes/origin/main` (fixed, not a flag; fetch first) and the build the host runs (`--current-bin` / `--current-info`, or `--current-unknown`). A build
  not on main is refused with no way round it (`not_on_main`); a build without commit or built
  dirty is refused; one that does not contain the host's build (`not_a_descendant`) or a host
  without build info (`current_unknown`) passes only with `--allow-downgrade WHY` /
  `--allow-unknown-current WHY`, and the reason is in the JSON answer the approval is made from
  (`allowed`; a reason given but not needed is in `unused_allow`). With `--candidate-bin` the
  answer carries the file's `candidate_sha256`, so the file installed can be the file checked. Every `GIT_*` variable is
  dropped before git is asked.
  Exit 0 ok, 2 refused, 3 git could not answer (fetch first), 64 usage.
- `aibd ctl web [--no-open]` (also `aib web`, which hands over to it; aibd from `AIBD_BIN` or
  `PATH`): checks aibd answers, asks for a pairing code, prints it with the page address (the
  address carries nothing) and opens the browser, waits until the page has redeemed the code,
  then shows the short code and confirms only on `y`; any other answer cancels the pairing.
  Pairing off ⇒ says how to turn it on. Needs `AIBD_PAIRING=terminal-unverified` (ADR 0006 §7:
  the y/N answer does not stop an AI running as the same account).
- `GET /api/v1/health` adds `version`, `started_at` (Unix seconds) and `instance_id` (random
  per start), so a restarted aibd can be told apart; still no path in it (043dd S3). If the
  random read fails, aibd does not start (it used to report an id of zeros, the same on every
  restart).
- `aib web` on Windows waits for `aibd ctl web` and returns its exit code. Windows `execv` gave
  the shell its prompt back while aibd was still asking y/N.
- Static files of the web workbench (ADR 0006 §6), behind the cargo feature `web` (off by
  default): `build.rs` embeds `apps/web/dist` (or `AIBD_WEB_DIST`; hidden files skipped) into
  aibd. Files outside `assets/` (`index.html`, also `/`) have a strong `ETag` per encoding
  (`"<hash>"`, `"<hash>-br"`, `"<hash>-gz"`; `If-None-Match` with any of them gives `304`)
  and `Cache-Control: no-cache`; `assets/*` are `public, max-age=31536000, immutable`; `.br` /
  `.gz` siblings follow `Accept-Encoding` (`Vary: Accept-Encoding`). Every `text/html` file
  gets a CSP that loads only our own files (plus `data:` images and fonts) and
  `X-Frame-Options: DENY`. Non-API paths need no bearer (the files are the same for every
  caller); Host and token-in-URL checks still apply. Without the feature every non-API path is
  `404 web_not_built` (new). Every `/api/` answer now carries `Cache-Control: no-store`
  unless it set its own, and `X-Aibd-Build` (version + embedded `dist` hash): an open page
  reloads when it changes, so old page code never calls a new API. No deep-link fallback:
  the front end uses hash routes. Routes are mounted only from `guard::ROUTES`, which is
  tested to sit under `/api/` (a route outside it would skip the bearer check).
- Pairing (ADR 0006 §7, W1 manual entry), **off by default**; `AIBD_PAIRING=terminal-unverified`
  turns it on (off: pair/codes and pair/redeem are `404`, nothing is counted). `POST
  /api/v1/pair/codes` (start-up token) gives a 10-character code that a person types into the
  page; the page redeems it with a form `POST /api/v1/pair/redeem` (`Origin` must be aibd's
  own, ≤ 1 KiB, field `code`) and gets a page credential in the exchange page body (no-store,
  no-referrer, CSP `default-src 'none'`). The credential is restricted until the 6-digit
  short code (made at redemption, bound to that credential) shown on the page (`GET
  /api/v1/pair/status`) and in the terminal (`GET /api/v1/pair/codes/{id}`) is confirmed with
  `POST /api/v1/pair/codes/{id}/confirm` (start-up token); until then only pair/status,
  pair/revoke and health answer, everything else is `403 pairing_unconfirmed` (new). A
  confirmed page credential may only read (GET / HEAD) and revoke itself; any other request
  is `403 page_write_not_allowed` (new) until the launch-profile routes exist, so a page
  never sends argv. A second redemption is `409 pairing_code_reused` (new) and revokes the
  first credential; a cancelled or expired code and every other failure is `401
  pairing_failed` (new, one message for all); 10 redeem failures a minute per aibd give `429
  pairing_rate_limited` (new). Confirmation has no shared limit (a wrong short code revokes).
  Credentials live 12 h, only as SHA-256, in memory; a live page credential anywhere in a
  path or query is `400 token_in_url`. **Behaviour change:** `Origin: null` is refused on
  every path, even when listed in the allowed origins (before, a listed `null` passed;
  `AIBD_ALLOWED_ORIGINS` never accepted it). `null` returns with the P2 local-file pairing.
  `/api/v1/metrics` has `pairing {channel, failures_total, rate_limited_total,
  confirm_failures_total, pairs}`.
  Known limits: the terminal y/N confirmation does **not** stop an AI on the same OS account
  (it can read the token file and confirm itself), so it is UNVERIFIED and opt-in. Anything
  that can send a form from aibd's own origin, or another local account, can fill the redeem
  limit and keep new pairings from redeeming (visible in metrics); a pairing already waiting
  for confirmation still confirms. With 16 live credentials a redemption answers `429`
  without using up the code and without counting as a failure. Back / bfcache / session
  restore of the exchange page are not tested in a real browser yet (needed before turning
  pairing on by default).
- Thermal guard hooks (R1 PR 3a, ADR 0005 §1): the sessions take a `launch_allowed` and an
  `on_running_changed(old, new)` hook (`Sessions::set_hooks`, called under the sessions lock)
  and `Sessions::kick_now`. While `launch_allowed` is false a POST answers `503 thermal_nogo`
  (new code, `Retry-After: 600`, not queued) and the queue holds. Nothing sets the hooks yet,
  so behaviour is unchanged until the guard is wired in.
- Session queue (ADR 0005 §5): when every slot is taken a POST is no longer refused with
  `429 too_many_sessions`; it answers `202` with the session `pending` (no process, `pid` 0,
  event `session.queued` with its `position`). Waiting sessions start in arrival order as
  slots come back. `DELETE` of a pending session cancels it (`cancelled`, never started).
  At most 64 wait; the next POST is `429 session_queue_full` (new code). At shutdown the
  waiting ones end `interrupted` (`ended_by: shutdown`); after a crash the next start marks
  a stored `pending` session `interrupted` (`previous_state: pending`,
  `processes_may_remain: false`). A queued start that cannot start ends `failed`
  (`ended_by: spawn_failed`); one the M1 gate refuses when it leaves the queue (it runs again
  on the `argv[0]` asked for) ends `failed` (`ended_by: policy_refused`). `GET` of a pending
  session has `queue {position, slots, held_by: [{id, state, reason}], held_unknown}`. Known
  limit: slots kept by unconfirmed stops are not given back by time, so the queue can stand
  still; `held_by` shows which (a session that is starting right now is not listed). A panic
  in the dequeue thread after it reserved a slot would keep that slot (no known path). The default running limit is now
  `clamp(floor(physical memory × 0.5 ÷ 500 MiB), 2, 4)`; `AIBD_MAX_SESSIONS` (1–64) is for
  stress tests.
- **M1 security gate, `POST /api/v1/sessions` (breaking):** only programs and env names on an
  allow-list may be started. The list is a JSON file named by `AIBD_SESSION_POLICY`
  (`{"programs": [{"name", "path", "signer"}], "env": [...]}`), read once at start. Unset means
  nothing may be started; an unreadable or invalid file stops aibd (exit 2).
  - Every spawn resolves the real path again and runs it.
  - Shells, interpreters and launchers are refused even when listed. So is `..` in `argv[0]`.
    These answer `403 program_not_allowed` (new).
  - An npm (node) install of a provider answers `403 provider_install_unsupported` (new).
  - Unlisted env names answer `400 env_not_allowed`.
  - The fixed deny list (`AIBD_*`, `DYLD_*`, `LD_*`, `PATH`) still applies and cannot be listed.
  - `signer` is recorded, not checked yet (M2).
  - Only recognised native formats run (Mach-O / ELF / PE magic).
  - Every refusal, including deny-listed env names and a relative `argv[0]`, writes a
    `session.spawn_rejected` audit event (aggregate `security`) plus a warn line. These are
    folded per token + code per minute. The folded count is written by the new operation
    timer `session.reject_flush` or at shutdown.
  - Not a defence against a same-user process: ADR 0003 "Update 2026-11-09" lists the known
    gaps.
- `aib-fixture-tree smoke-tree` / `smoke-wait`: a portable native program for the sessions smoke.
  The release smoke now runs it with `--fixture` instead of a Python helper.
- Seats (M1, spec 4.1 / 5 / 8): `POST /api/v1/seats` (`{"name"}`, 1–128 characters), `GET /api/v1/seats`,
  `GET /api/v1/seats/{id}`. A seat is stored in SQLite (migration 0002) together with its `seat.created`
  event in one transaction, so it is read back after a restart. A new seat is `CREATED`. Writes take an optional `Idempotency-Key` (1–128 of `A-Z a-z 0-9 - _ . :`):
  the same key and body replays the first answer with `Idempotent-Replayed: true` (also after a
  restart), another body is `409 idempotency_conflict`. New error codes `seat_not_found`,
  `idempotency_conflict`.
- Sessions of a seat (M1): `POST /api/v1/seats/{id}/sessions` (same body and answers as
  `POST /api/v1/sessions`; `404 seat_not_found` for an unknown seat) and `GET /api/v1/seats/{id}/sessions`
  (the stored rows, oldest first). Every session (both routes) is now stored in SQLite (migration 0003)
  with each of its events in one transaction; the stored form keeps only argv[0]'s file name and the
  argument count, never argv or env. Sessions gain `seat_id` (`null` on the preview.2 route).
  At start, aibd marks every session still `running`, or still holding a slot, from the previous run as
  `interrupted` (`ended_by: aibd_restart`, event `session.interrupted` with `processes_may_remain: true`);
  nothing is re-attached or restarted.
- Session output (preview.3): on macOS / Linux a session's stdout and stderr are kept, one
  `session.output` event per line (`{stream, line, cut}`) on the aggregate `<session id>/output`, one
  sequence for both streams, ending with `session.output_closed {stream, why}` per stream. They are on
  the global cursor and read with `GET /api/v1/sessions/{id}/output?after=&limit=` (also for a session
  of an earlier aibd run). A line over 256 KiB is cut (`cut: true`); after 32 MiB per session one
  `session.output_truncated` event and the rest is read and dropped. With Claude Code
  `--output-format stream-json` each line is one whole event, not a token delta. The text is never
  written to aibd's log. Windows sessions still start with null stdio (output not kept; UNVERIFIED).

## 0.2.0-preview.2 — 2026-11-09

- `/api/v1/sessions`: start, list, query and stop a program; stop takes the whole process tree
  (macOS process group, Windows Job). Details and limits: the preview.2 release notes.

- Sessions API (token only, loopback only): `POST /api/v1/sessions` starts a program in its own process
  group (absolute `argv[0]`, no PATH lookup; stdin is the null device; stdout / stderr: see session output
  above), `GET /api/v1/sessions[/{id}]`, `DELETE /api/v1/sessions/{id}?grace_ms=` stops the whole
  recorded tree and answers after the stop.
  - End states are honest: `exited` / `killed` only for a proven-clean stop; on macOS (never provable)
    a session ends `stop_unconfirmed` with the reason, survivors and blind spots. A session with a known
    survivor keeps its slot and a second DELETE stops it again.
  - At most 8 running at once (`AIBD_MAX_SESSIONS`, 1–64); the slot is reserved before the start.
  - When the root exits by itself, what it left is stopped once.
  - `aibd` stopping (Ctrl-C or SIGTERM) refuses new sessions and stops every running one first
    (10 s cap). If aibd itself is killed (`kill -9`, crash), macOS cannot stop what it started:
    known limit (UNVERIFIED).
  - Events: `session.started` (argv[0] file name + count only), `session.exited`, `session.killed`,
    `session.stop_unconfirmed`. env is never returned, logged or stored.
  - Windows: sessions run in a Job Object and a stop takes the whole Job (`killed` only when the Job
    emptied and every handle signaled); `aibd selfcheck` `process_management` is still `not_wired`.
- `aibd selfcheck`: `process_management` now really starts and stops one small tree (a shell with a
  child) and reports that stop's own answer. The stop comes only once the child reported it started
  (at most 3 s); if it never does, the row is `unverified` with reason `child_not_started`. macOS: no survivor seen = `unverified` with the reason
  (e.g. `observed_clean`), never `ok`; a survivor = `failed:tree_not_clean` (exit code 1). Windows:
  `not_wired` (no Windows check tree yet; the POSIX one is never tried there, even once Windows
  sessions are wired).

## 0.2.0-preview.1 — 2026-11-09

Preview build, not signed, does not replace the Python `aib`.

- `aibd --version` prints `aibd <version> <os>-<arch>`.
- `aibd selfcheck [--json]` lists what this build can do on this machine, one row per capability
  (`ok` / `failed:<tag>` / `unverified` / `unsupported` / `not_wired`); exit code 1 only when a row failed.
  - process management: library code only, `not_wired` (no endpoint starts processes yet);
  - Windows Job Object: `unverified` (GitHub's Windows runner only, no real machine);
  - macOS lineage (M1): `unverified` even when its start-up check passes, never reported clean.
- `aibd` with no argument starts the local server as before; an unknown argument exits 2 and starts nothing.
