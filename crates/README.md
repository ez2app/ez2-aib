# AIB 2.0 Rust workspace (Phase 0 skeleton)

| Crate | What | Rule |
|---|---|---|
| `aib-core` | domain types, Seat/Session state machines, error codes, port traits | `#![no_std]` + `#![forbid(unsafe_code)]`, dependency-tree allow-list, no platform `cfg` (`scripts/check-core-purity.sh`; `--self-test` proves each rule catches a bypass) |
| `aib-store` | SQLite (rusqlite, bundled), migrations from `/migrations`, event log | only crate with SQL |
| `aib-sys` | macOS process APIs M1–M4 (libproc, incl. the private `PROC_PIDUNIQIDENTIFIERINFO`), `ProcessInfo` trait + `FakeProcessInfo`, start-up `selfcheck` | the only crate allowed `unsafe` (each block one FFI call with a SAFETY note); only `aibd` may depend on it (`scripts/check-sys-deps.sh`, `--self-test`); M1 only adds candidates, never decides a signal |
| `aibd` | local daemon: Axum on loopback, Host/Origin guard, bearer token, platform module | platform code only under `src/platform/`; logs only known route templates (`<unmatched>` otherwise), never raw paths or queries |

Decisions: `docs/adr/0001` (coexistence with the 1.x cockpit), `0002` (SQLite driver),
`0003` (SSE + REST, event cursor, local API auth).

## Run

```sh
cargo test --workspace
AIBD_DATA_DIR=/tmp/aibd-dev AIBD_BIND=127.0.0.1:0 cargo run -p aibd
# prints {"listening": "...", "token_file": "..."}; the token value is never printed
curl -s http://127.0.0.1:<port>/api/v1/health
curl -s -H "Authorization: Bearer $(cat /tmp/aibd-dev/run/aibd.token)" http://127.0.0.1:<port>/api/v1/events
```

Environment: `AIBD_BIND` (loopback only, default `127.0.0.1:47770`), `AIBD_DATA_DIR`
(default: OS app data dir, never `~/.ez2-aib`), `AIBD_ALLOWED_ORIGINS` (comma list of exact
origins, default none), `AIBD_LOG` (tracing filter, default `info`).

Exit codes: 0 stopped cleanly, 2 bad config (e.g. `0.0.0.0`), 3 another aibd holds the
data directory, 1 other start failure.

## Windows clippy on a Mac (check only)

`aibd` and `aib-store` pull bundled SQLite, whose build script needs a C compiler for the MSVC target.
`scripts/dev/fakecc-msvc.sh` is a fake one (writes empty outputs, compiles nothing), enough for check / clippy:

```
rustup target add x86_64-pc-windows-msvc        # once
scripts/dev/wincheck.sh                          # clippy -p aibd --target x86_64-pc-windows-msvc, -D warnings
scripts/dev/wincheck.sh --changed origin/main    # only when Windows code changed; prints "skip" otherwise
```

`aib prepr` runs the same for the members that build C (item `rust clippy (x86_64-pc-windows-msvc, fakecc)`).
Target dir `target-wincheck.noindex/`. Exit 0 green or skipped, 1 red, 2 not verified (target missing).
It is a type check only: say "Windows clippy (fakecc) green; tests still need CI", never "Windows green".
