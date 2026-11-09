# Changelog — Rust workspace (aibd)

The Python `aib` keeps its own CHANGELOG; this file covers the `crates/` workspace only.
The version lives in one place: `[workspace.package] version` in the root `Cargo.toml`.

## 0.2.0-preview.2 — unreleased

- `/api/v1/sessions`: start, list, query and stop a program; stop takes the whole process tree
  (macOS process group, Windows Job). Details and limits: the preview.2 release notes.

- Sessions API (token only, loopback only): `POST /api/v1/sessions` starts a program in its own process
  group (absolute `argv[0]`, no PATH lookup; stdin / stdout / stderr are the null device, no output
  is kept), `GET /api/v1/sessions[/{id}]`, `DELETE /api/v1/sessions/{id}?grace_ms=` stops the whole
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
