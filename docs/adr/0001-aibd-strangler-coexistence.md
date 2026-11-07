# ADR 0001 — aibd runs beside the 1.x cockpit (strangler), no dual writes

- Status: accepted for Phase 0–1 (a63ab, 2026-11-07)
- Deciders: @a9136 (proposal), 9320/a63ab (approval)

## Context

AIB 1.x is a Python + shell cockpit built on tmux (see
`docs/migration/legacy-inventory.md`). tmux is supervisor, identity source, message
transport and state store at the same time, and it does not exist natively on Windows.
AIB 2.0's main driver is "works on macOS and Windows". Rewriting everything at once
would stop the team that uses 1.x every day, and spec 13.3 forbids deleting or
overwriting old data, hooks or tmux code.

## Decision

1. **Same repository, new top-level Rust workspace.** `Cargo.toml`, `crates/`,
   `migrations/` live at the root of `ez2app/ez2-aib` next to the untouched `aib/`
   Python package. A separate workflow (`.github/workflows/aibd.yml`) only triggers on
   Rust paths; the existing `ci.yml` / `release.yml` and the PyPI package are unchanged.
2. **aibd owns its own data directory and SQLite database.** It never writes to the
   1.x state root (`~/.ez2-aib`) or the Vault. Default data dir comes from the OS;
   `AIBD_DATA_DIR` overrides it (tests always set it to a temp dir).
3. **No dual writes.** During Phase 1 the two systems do not write to each other.
   1.x data reaches aibd only through `aib migrate --dry-run` / `aib migrate` (read-only
   source, rollback manifest) or a read-only legacy reader for display.
4. **aibd does not depend on tmux.** tmux may later become an optional client of the aibd
   API (a pane that renders a seat), never the other way round.
5. **Strangling order** (each step ships alone and can be rolled back by not using it):
   health/API skeleton → seat + session + events (Phase 1 gate) → one Provider Adapter →
   web workspace reads → migration import → retire 1.x features one by one, each after
   its 2.0 replacement has passed its gate on both OSes.
6. Paid add-ons stay in the private `aib-plugins` repository. The open-source core keeps
   interfaces (feature strings in entitlements, Plugin API) and no paid logic.

## Consequences

- Positive: no data-loss path during the transition; 1.x keeps working; rollback is
  "stop aibd".
- Positive: shared fixtures and docs; one review flow (@abb89 merges).
- Negative: during the transition the two systems show different state. Accepted; the
  migration import is the only bridge.
- Negative: the repository now needs both Python and Rust toolchains for full CI.
  Mitigated by path filters.

## Verification levels (used in every Phase document)

| Level | Meaning |
|---|---|
| VERIFIED-mac | ran on a real Mac by a seat, command and exit code recorded |
| CI-windows | compiled and tests ran on a GitHub-hosted Windows runner (real Windows, not a mock) |
| UNVERIFIED-desktop | needs an interactive Windows desktop (ConPTY interaction with a real CLI login, Windows Hello, toast, autostart) — not yet run |

Mocks never raise a level.
