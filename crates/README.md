# AIB 2.0 Rust workspace (Phase 0 skeleton)

| Crate | What | Rule |
|---|---|---|
| `aib-core` | domain types, Seat/Session state machines, error codes, port traits | no I/O, no async runtime, no `cfg(target_os)` (`scripts/check-core-purity.sh`) |
| `aib-store` | SQLite (rusqlite, bundled), migrations from `/migrations`, event log | only crate with SQL |
| `aibd` | local daemon: Axum on loopback, Host/Origin guard, bearer token, platform module | platform code only under `src/platform/` |

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
