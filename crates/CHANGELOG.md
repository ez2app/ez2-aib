# Changelog — Rust workspace (aibd)

The Python `aib` keeps its own CHANGELOG; this file covers the `crates/` workspace only.
The version lives in one place: `[workspace.package] version` in the root `Cargo.toml`.

## Unreleased

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
